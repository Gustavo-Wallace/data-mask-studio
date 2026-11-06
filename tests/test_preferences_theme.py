"""Typed theme choice and coordinated field patches, without applying a theme."""

from dataclasses import FrozenInstanceError, fields
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data_mask_studio.preferences import (
    LanguagePreference, ThemePreference, Preferences, PreferencesError, PreferencesService,
)
from data_mask_studio.preferences import service as module
from data_mask_studio.preferences.locking import preferences_write_lock


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.mark.parametrize("language", list(LanguagePreference))
def test_legacy_language_only_is_valid_system_theme_and_read_never_migrates(tmp_path, language):
    path = tmp_path / "preferences.json"
    raw = json.dumps({"schema_version": 1, "language": language.value}).encode()
    path.write_bytes(raw)
    before = path.stat().st_mtime_ns
    service = PreferencesService(path)
    for _ in range(2):
        result = service.load()
        assert result.error is None
        assert result.preferences == Preferences(language, ThemePreference.SYSTEM)
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("theme", list(ThemePreference))
@pytest.mark.parametrize("language", list(LanguagePreference))
def test_typed_round_trip_and_partial_updates_preserve_the_other_field(tmp_path, theme, language):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.load().preferences == Preferences()
    assert not list(tmp_path.iterdir())
    assert service.save_theme(theme).preferences == Preferences(theme=theme)
    result = service.save_language(language)
    assert result.error is None and result.preferences == Preferences(language, theme)
    other = PreferencesService(service.path)
    assert other.load().preferences == result.preferences
    assert json.loads(service.path.read_bytes()) == {
        "schema_version": 1, "language": language.value, "theme": theme.value,
    }
    assert other.save_theme("system").preferences == Preferences(language, ThemePreference.SYSTEM)
    with pytest.raises(FrozenInstanceError):
        result.preferences.theme = ThemePreference.SYSTEM


@pytest.mark.parametrize("field,newer", [("language", "dark"), ("theme", "en")])
def test_stale_service_snapshot_never_overwrites_a_newer_independent_choice(tmp_path, field, newer):
    path = tmp_path / "preferences.json"
    stale, fresh = PreferencesService(path), PreferencesService(path)
    original = stale.load().preferences
    other = "theme" if field == "language" else "language"
    assert getattr(fresh, "save_" + other)(newer).error is None
    assert original == Preferences()  # The immutable earlier snapshot stays stale.
    result = getattr(stale, "save_" + field)("en" if field == "language" else "light")
    assert result.error is None
    assert getattr(result.preferences, other).value == newer
    assert PreferencesService(path).load().preferences == result.preferences


@pytest.mark.parametrize("portable", [False, True])
def test_theme_uses_same_appdata_path_in_installed_and_portable_locations(tmp_path, monkeypatch, portable):
    local = tmp_path / "Dados # %23 % 日本"
    program = tmp_path / ("portable" if portable else "installed")
    program.mkdir()
    monkeypatch.chdir(program)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(sys, "executable", str(program / "DataMaskStudio.exe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    service = PreferencesService()
    assert service.load().preferences.theme is ThemePreference.SYSTEM
    assert not local.exists()
    assert service.save_theme("light").error is None
    assert service.path == local / "DataMaskStudio" / "preferences.json"
    assert PreferencesService().load().preferences.theme is ThemePreference.LIGHT
    assert not list(program.iterdir())


@pytest.mark.parametrize("invalid", [None, True, 1, [], {}, "", "Light", "auto", "system "])
def test_invalid_theme_is_rejected_without_coercion_or_io(tmp_path, monkeypatch, invalid):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_theme("dark").error is None
    before = service.path.read_bytes()
    with monkeypatch.context() as guard:
        guard.setattr(service, "load", lambda: pytest.fail("Invalid update needs no storage access"))
        result = service.save_theme(invalid)
    assert result.error is PreferencesError.INVALID_THEME
    assert service.path.read_bytes() == before
    assert str(service.path) not in result.error.message


@pytest.mark.parametrize("raw", [
    b'{"schema_version":1,"language":"en","theme":null}',
    b'{"schema_version":1,"language":"en","theme":"bright"}',
    b'{"schema_version":1,"language":"en","theme":"dark","theme":"light"}',
    b'{"schema_version":1,"language":"en","future_setting":{"private":"synthetic"}}',
    b'{"schema_version":2,"language":"en","theme":"dark"}',
])
def test_unsupported_theme_document_or_unknown_fields_remain_untouched(tmp_path, raw):
    path = tmp_path / "preferences.json"
    path.write_bytes(raw)
    service = PreferencesService(path)
    for result in (service.load(), service.save_language("pt-BR"), service.save_theme("light")):
        assert result.error is PreferencesError.INVALID_FILE
        assert result.preferences == Preferences()
    assert path.read_bytes() == raw and list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("phase", ["mkdir", "mkstemp", "fdopen", "fsync", "replace"])
def test_failed_theme_patch_preserves_complete_document_and_releases_lock(tmp_path, monkeypatch, phase):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_language("en").error is None
    assert service.save_theme("dark").error is None
    before = service.path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("SYNTHETIC_PRIVATE_PATH")
    with monkeypatch.context() as guard:
        target = Path if phase == "mkdir" else module.tempfile if phase == "mkstemp" else module.os
        guard.setattr(target, phase, fail)
        result = service.save_theme("light")
    assert result.error is PreferencesError.WRITE_FAILED
    assert result.preferences == Preferences(LanguagePreference.ENGLISH, ThemePreference.DARK)
    assert service.path.read_bytes() == before
    assert set(tmp_path.iterdir()) == {service.path, tmp_path / ".preferences.json.lock"}
    assert service.save_theme("light").error is None  # No abandoned OS lock.


def test_merged_state_is_reread_inside_lock_and_fully_validated(tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_language("en").error is None
    read = service.load
    events = []
    def observe():
        result = read()
        events.append(True)
        if len(events) == 1:
            # A complete external update between preflight and the transaction.
            service.path.write_text('{"schema_version":1,"language":"pt-BR","theme":"dark"}')
        return result
    monkeypatch.setattr(service, "load", observe)
    result = service.save_theme("light")
    assert len(events) == 2 and result.error is None
    assert result.preferences == Preferences(LanguagePreference.BRAZILIAN_PORTUGUESE, ThemePreference.LIGHT)
    assert set(module._FIELDS) == {field.name for field in fields(Preferences)}


def test_document_invalidated_after_preflight_is_not_repaired(tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_theme("dark").error is None
    read = service.load
    def invalidate():
        result = read()
        if result.error is None:
            service.path.write_bytes(b"SYNTHETIC_INVALID_DOCUMENT")
        return result
    monkeypatch.setattr(service, "load", invalidate)
    assert service.save_language("en").error is PreferencesError.INVALID_FILE
    assert service.path.read_bytes() == b"SYNTHETIC_INVALID_DOCUMENT"


def test_lock_is_document_scoped_nonblocking_and_exception_safe(tmp_path):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_theme("dark").error is None
    before = service.path.read_bytes()
    with pytest.raises(RuntimeError):
        with preferences_write_lock(service.path):
            assert service.save_theme("light").error is PreferencesError.WRITE_FAILED
            assert service.path.read_bytes() == before
            assert PreferencesService(tmp_path / "other.json").save_theme("light").error is None
            raise RuntimeError("synthetic operation failure")
    assert service.save_theme("light").error is None


@pytest.mark.parametrize("kind", ["symlink", "junction", "nonempty", "directory"])
def test_ambiguous_lock_is_not_replaced_or_modified(tmp_path, monkeypatch, kind):
    path = tmp_path / "preferences.json"
    path.write_text('{"schema_version":1,"language":"en"}')
    lock = tmp_path / ".preferences.json.lock"
    raw = path.read_bytes()
    if kind in ("nonempty", "directory"):
        if kind == "directory":
            lock.mkdir()
        else:
            lock.write_bytes(b"UNRELATED_SYNTHETIC_SENTINEL")
    else:
        method = "is_symlink" if kind == "symlink" else "is_junction"
        original = getattr(Path, method)
        monkeypatch.setattr(Path, method, lambda candidate: candidate == lock or original(candidate))
    result = PreferencesService(path).save_theme("dark")
    assert result.error is PreferencesError.WRITE_FAILED and path.read_bytes() == raw
    if kind == "nonempty":
        assert lock.read_bytes() == b"UNRELATED_SYNTHETIC_SENTINEL"
    elif kind == "directory":
        assert lock.is_dir()


def _writer(path, field, value, channel, pause):
    os.environ["LOCALAPPDATA"] = str(path.parent / "isolated-child-appdata")
    service = PreferencesService(path)
    channel.send(("ready", service.load().preferences))
    original = module.os.replace
    if pause:
        def staged(source, destination):
            channel.send(("staged", None))
            assert channel.recv() == "publish"
            original(source, destination)
        module.os.replace = staged
    try:
        while channel.recv() == "save":
            result = getattr(service, "save_" + field)(value)
            channel.send(("result", result))
    finally:
        module.os.replace = original
        channel.close()


def _receive(channel, event):
    assert channel.poll(15), "Synthetic writer failed to report its phase"
    name, value = channel.recv()
    assert name == event
    return value


@pytest.mark.parametrize("first_field", ["language", "theme"])
def test_two_real_processes_do_not_lose_acknowledged_independent_updates(tmp_path, first_field):
    path = tmp_path / "preferences.json"
    assert PreferencesService(path).save_language("system").error is None
    original = path.read_bytes()
    context = multiprocessing.get_context("spawn")
    workers = []
    for field, pause in ((first_field, True), ("theme" if first_field == "language" else "language", False)):
        parent, child = context.Pipe()
        process = context.Process(target=_writer, args=(path, field, "en" if field == "language" else "dark", child, pause))
        process.start()
        child.close()
        workers.append((parent, process))
    first, second = [entry[0] for entry in workers]
    try:
        assert _receive(first, "ready") == _receive(second, "ready") == Preferences()
        first.send("save")
        _receive(first, "staged")
        second.send("save")
        blocked = _receive(second, "result")
        assert blocked.error is PreferencesError.WRITE_FAILED and path.read_bytes() == original
        first.send("publish")
        committed = _receive(first, "result")
        assert committed.error is None
        second.send("save")  # Explicit retry, not an automatic background retry.
        retry = _receive(second, "result")
        assert retry.error is None
        assert retry.preferences == Preferences(LanguagePreference.ENGLISH, ThemePreference.DARK)
        assert PreferencesService(path).load().preferences == retry.preferences
        assert getattr(retry.preferences, first_field) == getattr(committed.preferences, first_field)
        for channel, process in workers:
            channel.send("stop")
            process.join(10)
            assert process.exitcode == 0
    finally:
        for channel, process in workers:
            if process.is_alive():
                process.terminate()  # Only this isolated synthetic child.
                process.join(10)
            channel.close()


def test_terminated_writer_releases_os_lock_and_keeps_last_published_document(tmp_path):
    path = tmp_path / "preferences.json"
    assert PreferencesService(path).save_theme("light").error is None
    before = path.read_bytes()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_writer, args=(path, "language", "en", child, True))
    process.start()
    child.close()
    try:
        _receive(parent, "ready")
        parent.send("save")
        _receive(parent, "staged")
        assert path.read_bytes() == before
        process.terminate()
        process.join(10)
        assert process.exitcode is not None and process.exitcode != 0
        assert path.read_bytes() == before
        assert PreferencesService(path).save_language("pt-BR").error is None
        assert PreferencesService(path).load().preferences == Preferences(
            LanguagePreference.BRAZILIAN_PORTUGUESE, ThemePreference.LIGHT)
        # A killed process cannot run finally: its owned unpublished temp may
        # remain. It is never used as recovery state or mistaken for a commit.
        assert len(list(tmp_path.glob(".preferences.json.*.tmp"))) == 1
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        parent.close()


def test_theme_persistence_has_no_qt_vault_crypto_sqlite_or_detection_dependencies(tmp_path):
    program = r'''
import sys
from data_mask_studio.preferences import PreferencesService, ThemePreference
service = PreferencesService(sys.argv[1], ui_languages_provider=lambda: (_ for _ in ()).throw(AssertionError("No detection")))
assert service.load().preferences.theme is ThemePreference.SYSTEM
for value in ThemePreference:
    assert service.save_theme(value).error is None
assert not any(name.startswith(("PySide6", "cryptography", "sqlite3", "data_mask_studio.vault", "data_mask_studio.security", "data_mask_studio.environment", "data_mask_studio.profiles")) for name in sys.modules)
'''
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(tmp_path / "isolated-process-appdata")
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-B", "-c", program, str(tmp_path / "preferences.json")],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
