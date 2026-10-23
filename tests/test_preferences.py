import json
import multiprocessing
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys

import pytest

from data_mask_studio.preferences import (
    LanguagePreference, Preferences, PreferencesError, PreferencesService, default_preferences_path,
)
from data_mask_studio.preferences import service as module


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))


def test_missing_preferences_default_to_system_without_creating_any_files(tmp_path):
    service = PreferencesService(ui_languages_provider=lambda: ("pt-BR",))
    result = service.load()
    assert result.preferences == Preferences(LanguagePreference.SYSTEM) and result.error is None
    assert service.resolve_language(result.preferences.language) == "pt-BR"
    assert service.path == Path(os.environ["LOCALAPPDATA"]) / "DataMaskStudio" / "preferences.json"
    assert service.path == default_preferences_path()
    assert not service.path.parent.exists()


@pytest.mark.parametrize("language", list(LanguagePreference))
def test_explicit_choice_persists_between_independent_service_instances(tmp_path, language):
    first = PreferencesService(ui_languages_provider=lambda: ("pt-BR",))
    saved = first.save_language(language)
    assert saved.error is None and saved.preferences.language is language
    second = PreferencesService(ui_languages_provider=lambda: ("en-US",))
    loaded = second.load()
    assert loaded.error is None and loaded.preferences.language is language
    document = json.loads(second.path.read_text(encoding="utf-8"))
    assert document == {"schema_version": 1, "language": language.value}
    assert "effective_language" not in document
    expected = "pt-BR" if language is LanguagePreference.BRAZILIAN_PORTUGUESE else "en"
    assert second.resolve_language(loaded.preferences.language) == expected
    if language is LanguagePreference.SYSTEM:
        before = first.path.read_bytes()
        assert first.resolve_language(language) == "pt-BR"
        assert second.resolve_language(language) == "en"
        assert first.path.read_bytes() == before  # Detection never becomes an explicit override.


@pytest.mark.parametrize("invalid", ["", "pt", "pt-PT", "pt_BR", "EN", "auto", None, True, 1, [], {}])
def test_invalid_language_is_rejected_without_overwriting_existing_preference(invalid):
    service = PreferencesService()
    assert service.save_language("en").error is None
    before = service.path.read_bytes()
    result = service.save_language(invalid)
    assert result.error is PreferencesError.INVALID_LANGUAGE
    assert service.path.read_bytes() == before


@pytest.mark.parametrize("raw", [
    b"", b'{"schema_version":1,', b"not JSON", b"\xff\xfe", b"null", b"[]",
    b'{"schema_version":true,"language":"en"}', b'{"schema_version":2,"language":"en"}',
    b'{"schema_version":1}', b'{"language":"en"}',
    b'{"schema_version":1,"language":"pt-PT"}', b'{"schema_version":1,"language":null}',
    b'{"schema_version":1,"language":"en","unknown_future_setting":true}',
    b'{"schema_version":1,"language":"en","language":"pt-BR"}',
    b"x" * (module.MAX_PREFERENCES_BYTES + 1),
])
def test_malformed_or_unsupported_documents_use_safe_defaults_but_are_never_repaired(tmp_path, raw):
    path = tmp_path / "preferences.json"
    path.write_bytes(raw)
    service = PreferencesService(path)
    for result in (service.load(), service.save_language("pt-BR")):
        assert result.error is PreferencesError.INVALID_FILE
        assert result.preferences.language is LanguagePreference.SYSTEM
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_snapshot_is_immutable_and_no_mutable_document_is_returned():
    service = PreferencesService()
    service.save_language("en")
    result = service.load()
    with pytest.raises(FrozenInstanceError):
        result.preferences.language = LanguagePreference.BRAZILIAN_PORTUGUESE
    with pytest.raises(FrozenInstanceError):
        result.error = PreferencesError.WRITE_FAILED
    assert service.load().preferences.language is LanguagePreference.ENGLISH


@pytest.mark.parametrize("local", [None, "", "relative-appdata"])
def test_unavailable_or_relative_appdata_has_no_cwd_or_user_profile_fallback(monkeypatch, local):
    if local is None:
        monkeypatch.delenv("LOCALAPPDATA")
    else:
        monkeypatch.setenv("LOCALAPPDATA", local)
    service = PreferencesService()
    assert service.path is None
    assert service.load().error is PreferencesError.STORAGE_UNAVAILABLE
    assert service.save_language("en").error is PreferencesError.STORAGE_UNAVAILABLE


def test_read_failure_is_safe_and_does_not_attempt_a_write(tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    def denied(*args, **kwargs):
        raise PermissionError("SYNTHETIC_PRIVATE_PATH_OR_SECRET")
    monkeypatch.setattr(Path, "open", denied)
    monkeypatch.setattr(module.tempfile, "mkstemp", lambda **kw: pytest.fail("No write after a failed read"))
    result = service.load()
    assert result.error is PreferencesError.READ_FAILED
    assert service.save_language("en").error is PreferencesError.READ_FAILED
    assert "SYNTHETIC" not in result.error.message and str(tmp_path) not in result.error.message


@pytest.mark.parametrize("phase", ["mkdir", "mkstemp", "fdopen", "fsync", "replace"])
def test_write_failure_keeps_last_good_document_and_cleans_owned_temporaries(tmp_path, monkeypatch, phase):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_language("en").error is None
    before = service.path.read_bytes()
    def failed(*args, **kwargs):
        raise OSError("SYNTHETIC_PRIVATE_FILE_DETAILS")
    if phase == "mkdir":
        monkeypatch.setattr(Path, "mkdir", failed)
    elif phase == "mkstemp":
        monkeypatch.setattr(module.tempfile, "mkstemp", failed)
    else:
        monkeypatch.setattr(module.os, phase, failed)
    result = service.save_language("pt-BR")
    assert result.error is PreferencesError.WRITE_FAILED
    assert result.preferences.language is LanguagePreference.ENGLISH
    assert "SYNTHETIC" not in result.error.message and str(tmp_path) not in result.error.message
    assert service.path.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["preferences.json"]


def test_atomic_replace_publishes_only_complete_fsynced_document_in_same_directory(tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    service.save_language("en")
    before = service.path.read_bytes()
    replace, fsync = module.os.replace, module.os.fsync
    events = []
    def synced(descriptor):
        fsync(descriptor)
        events.append("fsync")
    def published(source, destination):
        assert events == ["fsync"]
        assert source.parent == destination.parent == tmp_path
        assert source != destination and destination == service.path
        assert destination.read_bytes() == before
        assert json.loads(source.read_text(encoding="utf-8")) == {"schema_version": 1, "language": "pt-BR"}
        replace(source, destination)
        events.append("replace")
    monkeypatch.setattr(module.os, "fsync", synced)
    monkeypatch.setattr(module.os, "replace", published)
    assert service.save_language("pt-BR").error is None
    assert events == ["fsync", "replace"]
    assert service.load().preferences.language is LanguagePreference.BRAZILIAN_PORTUGUESE
    assert list(tmp_path.iterdir()) == [service.path]


def test_partial_temporary_write_never_truncates_the_previous_document(tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    service.save_language("en")
    before = service.path.read_bytes()
    fdopen = module.os.fdopen

    class InterruptedWrite:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            self.stream.write(data[:10])
            self.stream.flush()
            raise OSError("Synthetic interrupted write")

    monkeypatch.setattr(module.os, "fdopen", lambda *args: InterruptedWrite(fdopen(*args)))
    assert service.save_language("pt-BR").error is PreferencesError.WRITE_FAILED
    assert service.path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [service.path]


def test_oversized_input_is_read_with_a_bound_and_never_parsed(tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    service.path.write_bytes(b"x" * (module.MAX_PREFERENCES_BYTES * 2))
    open_file = Path.open

    class BoundedRead:
        def __enter__(self):
            self.stream = open_file(service.path, "rb")
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size):
            assert size == module.MAX_PREFERENCES_BYTES + 1
            return self.stream.read(size)

    monkeypatch.setattr(Path, "open", lambda *args: BoundedRead())
    monkeypatch.setattr(module.json, "loads", lambda *args, **kw: pytest.fail("Oversized input must not be parsed"))
    assert service.load().error is PreferencesError.INVALID_FILE


def test_directory_or_link_at_preferences_path_is_not_replaced(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    path.mkdir()
    service = PreferencesService(path)
    assert service.load().error is PreferencesError.READ_FAILED
    assert service.save_language("en").error is PreferencesError.READ_FAILED
    assert path.is_dir()
    monkeypatch.setattr(Path, "is_symlink", lambda candidate: candidate == path)
    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("Do not follow a preferences link"))
    assert service.load().error is PreferencesError.READ_FAILED
    assert service.save_language("en").error is PreferencesError.READ_FAILED


def test_unusual_paths_and_readonly_executable_location_are_independent(tmp_path, monkeypatch):
    local = tmp_path / "Dados # %23 % espaços 日本"
    executable = tmp_path / "unwritable-program-directory"
    executable.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable / "DataMaskStudio.exe"))
    service = PreferencesService()
    assert service.save_language("pt-BR").error is None
    assert service.path == local / "DataMaskStudio" / "preferences.json"
    assert PreferencesService().load().preferences.language is LanguagePreference.BRAZILIAN_PORTUGUESE
    assert not list(executable.iterdir())


def test_preferences_never_read_or_write_vault_profiles_keys_recovery_or_unknown_settings(monkeypatch):
    service = PreferencesService()
    service.path.parent.mkdir(parents=True)
    protected = {}
    for name in ("vault.db", "secret.key", "vault_key.dpapi", "profiles.json", "settings.json",
                 ".dms-environment-restore.json", "synthetic.dmsbackup", "synthetic.dmspackage"):
        path = service.path.parent / name
        protected[path] = b"SYNTHETIC_PROTECTED_SENTINEL"
        path.write_bytes(protected[path])
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        assert path not in protected, "Preferences must not access other DMS data"
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", guarded_open)
        assert service.load().error is None
        assert service.save_language("en").error is None
        assert set(json.loads(service.path.read_bytes())) == {"schema_version", "language"}
    assert all(path.read_bytes() == content for path, content in protected.items())
    assert set(service.path.parent.iterdir()) == set(protected) | {service.path}


def _paused_writer(path, channel):
    os.environ["LOCALAPPDATA"] = str(path.parent / "isolated-child-appdata")
    replace = module.os.replace
    def paused(source, destination):
        channel.send("staged")
        assert channel.recv() == "publish"
        replace(source, destination)
    module.os.replace = paused
    try:
        result = PreferencesService(path).save_language("en")
        channel.send(result.error.value if result.error else "success")
    finally:
        module.os.replace = replace
        channel.close()


def test_real_processes_use_complete_document_last_replacement_wins(tmp_path):
    path = tmp_path / "preferences.json"
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_paused_writer, args=(path, child))
    process.start()
    child.close()
    try:
        assert parent.poll(15) and parent.recv() == "staged"
        assert not path.exists()
        assert PreferencesService(path).save_language("pt-BR").error is None
        assert PreferencesService(path).load().preferences.language is LanguagePreference.BRAZILIAN_PORTUGUESE
        parent.send("publish")
        assert parent.poll(15) and parent.recv() == "success"
        process.join(10)
        assert process.exitcode == 0
        assert PreferencesService(path).load().preferences.language is LanguagePreference.ENGLISH
        assert list(tmp_path.iterdir()) == [path]
    finally:
        if process.is_alive():
            process.terminate()  # This synthetic test-owned child only.
            process.join(10)
        parent.close()


def test_fresh_import_and_default_load_do_not_import_vault_or_initialize_qt(tmp_path):
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(tmp_path / "fresh-isolated-appdata")
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-B", "-c", """
import sys
from data_mask_studio.preferences import PreferencesService
service = PreferencesService()
assert service.load().error is None
assert not service.path.parent.exists()
assert not any(name.startswith(('PySide6', 'cryptography', 'data_mask_studio.vault',
                               'data_mask_studio.security', 'data_mask_studio.profiles'))
               for name in sys.modules)
"""], env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_backup_and_restore_preserve_local_preferences_outside_backup_components(tmp_path):
    from test_backup import prepare_environment, PASSWORD
    from data_mask_studio.backup import create_backup, restore_backup, validate_backup
    from data_mask_studio.backup.validator import extracted_backup

    paths, hmac_provider, vault_provider, protector = prepare_environment(tmp_path)
    preferences = PreferencesService(paths.directory / "preferences.json")
    assert preferences.save_language("en").error is None
    backup = tmp_path / "synthetic.dmsbackup"
    create_backup(backup, PASSWORD, PASSWORD, paths=paths, hmac_key_provider=hmac_provider,
                  vault_key_provider=vault_provider)
    assert validate_backup(backup, PASSWORD).is_compatible
    with extracted_backup(backup, PASSWORD) as extracted:
        assert {item["name"] for item in extracted.manifest["components"]} == {
            "hmac_key", "vault_key", "vault_database", "profiles",
        }
    assert preferences.save_language("pt-BR").error is None
    before = preferences.path.read_bytes()
    restored = restore_backup(backup, PASSWORD, paths=paths, protector=protector)
    assert restored.vault_restored and restored.profile_count == 1
    assert preferences.path.read_bytes() == before
    assert PreferencesService(preferences.path).load().preferences.language is LanguagePreference.BRAZILIAN_PORTUGUESE
