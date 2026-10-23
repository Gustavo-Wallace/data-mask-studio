"""Bundled catalog contracts; no GUI startup, keys, vault or persistence needed."""

from dataclasses import FrozenInstanceError
from html import escape
import os
from pathlib import Path
import subprocess
import sys
import traceback
import zipfile

import pytest

from data_mask_studio.i18n import (
    CatalogError, FALLBACK_LOCALE, MessageFormatError, MissingMessageError,
    SUPPORTED_LOCALES, Translator, load_catalogs, validate_catalogs,
)
from data_mask_studio.i18n import catalog as module


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def documents():
    return {locale: dict(messages) for locale, messages in load_catalogs().items()}


@pytest.mark.parametrize("locale,label,success", [
    ("en", "Settings", "Language preference saved: English."),
    ("pt-BR", "Configurações", "Preferência de idioma salva: English."),
])
def test_explicit_utf8_resources_lookup_and_named_parameters(locale, label, success):
    translator = Translator(locale)
    assert translator.locale == locale
    assert translator.text("common.settings") == label
    assert translator.text("preferences.saved", language="English") == success
    warning = translator.text("package.authentication_failed")
    assert ("password" if locale == "en" else "senha") in warning
    catalogs = load_catalogs()
    assert tuple(catalogs) == SUPPORTED_LOCALES == ("en", "pt-BR")
    assert set(catalogs["en"]) == set(catalogs["pt-BR"])
    for identifier in catalogs[locale]:
        assert module.placeholder_names(catalogs[locale][identifier]) == module.placeholder_names(
            catalogs["en"][identifier]
        )


def test_catalogs_and_translator_expose_no_mutable_state():
    catalogs = load_catalogs()
    with pytest.raises(TypeError):
        catalogs["en"] = {}
    with pytest.raises(TypeError):
        catalogs["en"]["common.settings"] = "changed"
    translator = Translator("en")
    with pytest.raises(FrozenInstanceError):
        translator.locale = "pt-BR"
    assert Translator("pt-BR").text("common.settings") == "Configurações"
    assert translator.text("common.settings") == "Settings"


@pytest.mark.parametrize("locale", ["", "system", "pt", "pt-PT", "pt_BR", "en-US", "EN", "../pt-BR", None, [], 123])
def test_unsupported_effective_locale_predictably_falls_back_to_english(locale):
    translator = Translator(locale)
    assert translator.locale == FALLBACK_LOCALE == "en"
    assert translator.text("common.settings") == "Settings"


@pytest.mark.parametrize("identifier", ["missing.key", "SYNTHETIC_PRIVATE_PATH", "", None, [], 123])
def test_missing_message_detection_never_silently_returns_key_or_sensitive_text(identifier):
    for translator in (Translator("en"), Translator("pt-BR")):
        with pytest.raises(MissingMessageError) as captured:
            translator.text(identifier)
        assert str(captured.value) == "Unknown application message identifier."
        assert "SYNTHETIC" not in str(captured.value)


@pytest.mark.parametrize("params", [
    {}, {"language": "en", "extra": "SYNTHETIC_PRIVATE"},
    {"wrong": "SYNTHETIC_PRIVATE"}, {"language": None}, {"language": object()},
    {"language": True}, {"language": {"private": "SYNTHETIC_PRIVATE"}},
])
def test_format_errors_are_explicit_and_do_not_echo_values(params):
    with pytest.raises(MessageFormatError) as captured:
        Translator("en").text("preferences.saved", **params)
    assert str(captured.value) == "Invalid application message parameters."
    assert "SYNTHETIC_PRIVATE" not in "".join(traceback.format_exception(captured.value))


def test_parameter_objects_are_not_executed_and_number_formatting_errors_are_not_hidden():
    class Dangerous:
        def __str__(self):
            pytest.fail("Do not execute a parameter object's formatting code")

        def __format__(self, _):
            pytest.fail("Do not execute a parameter object's formatting code")

    with pytest.raises(MessageFormatError):
        Translator().text("preferences.saved", language=Dangerous())
    # CPython's integer-string conversion limit can reject corrupt huge counts.
    if sys.get_int_max_str_digits():
        with pytest.raises(MessageFormatError, match="Unable to format application message"):
            Translator().text("restoration.completed", count=10 ** (sys.get_int_max_str_digits() + 1), filename="safe.csv")


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
@pytest.mark.parametrize("filename", [
    "synthetic.csv", "ação-漢字-😀.csv", '<img src=x onerror="alert(1)">&\'arquivo.csv',
    r"C:\Synthetic\a&b\{count}.csv", "${__import__('os')} %s {{literal}}.csv",
])
def test_parameters_are_literal_and_rich_text_is_explicitly_escaped(locale, filename):
    translator = Translator(locale)
    text = translator.text("restoration.completed", count=42, filename=filename)
    assert filename in text and "42" in text
    assert translator.html("restoration.completed", count=42, filename=filename) == escape(text, quote=True)
    if "<img" in filename:
        assert "<img" not in translator.html("restoration.completed", count=42, filename=filename)
        assert "&lt;img" in translator.html("restoration.completed", count=42, filename=filename)


@pytest.mark.parametrize("locale,singular,plural", [
    ("en", "1 file selected.", "files selected."),
    ("pt-BR", "1 arquivo selecionado.", "arquivos selecionados."),
])
def test_explicit_integral_count_pairs_and_context(locale, singular, plural):
    translator = Translator(locale)
    assert translator.plural("files.selected", 1) == singular
    for count in (0, 2, 1000):
        assert translator.plural("files.selected", count) == f"{count} {plural}"
    with pytest.raises(MissingMessageError):
        translator.text("files.selected")


@pytest.mark.parametrize("count", [-1, 1.5, "1", None, True])
def test_invalid_plural_count_is_not_silently_coerced(count):
    with pytest.raises(MessageFormatError):
        Translator().plural("files.selected", count)
    with pytest.raises(MessageFormatError):
        Translator().plural("files.selected", 1, **{"count": "SYNTHETIC_PRIVATE"})


@pytest.mark.parametrize("template", [
    "{", "}", "{}", "{0}", "{filename.upper}", "{filename[0]}",
    "{filename.__class__}", "{filename!r}", "{filename!s}", "{filename:>999999999}",
    "{filename:{width}}", "{__import__('os')}", "{filename()}", "{naïve}",
])
def test_executable_or_fragile_templates_are_rejected(template):
    catalogs = documents()
    for messages in catalogs.values():
        messages["test.invalid"] = template
    with pytest.raises(CatalogError):
        validate_catalogs(catalogs)


def test_literal_braces_are_supported_without_recursively_formatting_parameters(monkeypatch):
    catalogs = documents()
    for messages in catalogs.values():
        messages["test.literal"] = "{{example}} {filename} {filename}"
    validate_catalogs(catalogs)
    monkeypatch.setattr("data_mask_studio.i18n.translator.load_catalogs", lambda: catalogs)
    assert Translator().text("test.literal", filename="{count}") == "{example} {count} {count}"


@pytest.mark.parametrize("defect", ["missing", "extra", "placeholder", "empty", "nontext", "identifier", "surrogate", "pair", "pair_fields"])
def test_coverage_and_catalog_validation_fail_closed(defect):
    catalogs = documents()
    if defect == "missing":
        del catalogs["pt-BR"]["common.settings"]
    elif defect == "extra":
        catalogs["pt-BR"]["unknown.message"] = "extra"
    elif defect == "placeholder":
        catalogs["pt-BR"]["restoration.completed"] = "{count} {wrong_filename}"
    else:
        for messages in catalogs.values():
            if defect == "empty":
                messages["common.settings"] = "  "
            elif defect == "nontext":
                messages["common.settings"] = 123
            elif defect == "identifier":
                messages["INVALID KEY"] = "bad"
            elif defect == "surrogate":
                messages["common.settings"] = "\ud800"
            elif defect == "pair":
                del messages["files.selected.one"]
            else:
                messages["files.selected.one"] = "{count} {filename}"
    with pytest.raises(CatalogError):
        validate_catalogs(catalogs)


@pytest.mark.parametrize("catalogs", [None, {}, {"en": {}}, {"en": {}, "pt-BR": {}}, {"en": [], "pt-BR": []}])
def test_invalid_catalog_collections_are_rejected(catalogs):
    with pytest.raises(CatalogError):
        validate_catalogs(catalogs)


@pytest.mark.parametrize("raw", [
    b"SYNTHETIC_PRIVATE_CONTENT", b"\xff", b"[]", b"null", b"{}",
    b'{"schema_version":true,"locale":"en","messages":{}}',
    b'{"schema_version":2,"locale":"en","messages":{}}',
    b'{"schema_version":1,"locale":"pt-BR","messages":{}}',
    b'{"schema_version":1,"locale":"en","messages":{},"extra":1}',
    b'{"schema_version":1,"locale":"en","messages":{},"messages":{}}',
    b'{"schema_version":1,"locale":"en","messages":{"x.y":"first","x.y":"second"}}',
    b'{"schema_version":1,"locale":"en","messages":[]}',
])
def test_malformed_bundled_resource_reports_safe_error(tmp_path, monkeypatch, raw):
    root = tmp_path / "synthetic-private-root"
    (root / "catalogs").mkdir(parents=True)
    (root / "catalogs" / "en.json").write_bytes(raw)
    monkeypatch.setattr(module.resources, "files", lambda _: root)
    with pytest.raises(CatalogError) as captured:
        load_catalogs()
    assert "SYNTHETIC_PRIVATE" not in str(captured.value)
    assert str(root) not in str(captured.value)


@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError])
def test_missing_or_unreadable_bundled_resources_are_explicit_errors(monkeypatch, failure):
    def denied(_):
        raise failure("SYNTHETIC_PRIVATE_PATH")

    monkeypatch.setattr(module.resources, "files", denied)
    with pytest.raises(CatalogError, match="Unable to load application message catalogs") as captured:
        load_catalogs()
    assert "SYNTHETIC_PRIVATE" not in "".join(traceback.format_exception(captured.value))


def test_preference_resolution_can_supply_locale_without_translation_writing_preferences(tmp_path):
    from data_mask_studio.preferences import PreferencesService

    service = PreferencesService(ui_languages_provider=lambda: ("pt-BR",))
    assert Translator(service.resolve_language(service.load().preferences.language)).locale == "pt-BR"
    assert not service.path.parent.exists()
    service.save_language("en")
    before = service.path.read_bytes(), service.path.stat().st_mtime_ns
    assert Translator(service.resolve_language(service.load().preferences.language)).locale == "en"
    assert (service.path.read_bytes(), service.path.stat().st_mtime_ns) == before
    service.save_language("pt-BR")
    assert Translator(service.resolve_language(service.load().preferences.language)).locale == "pt-BR"


def test_fresh_import_and_resource_loading_need_no_qt_windows_or_sensitive_modules(tmp_path):
    code = """
import sys
from importlib.abc import MetaPathFinder
class Deny(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'ctypes' or fullname.startswith(('PySide6', 'cryptography',
                'data_mask_studio.preferences', 'data_mask_studio.vault',
                'data_mask_studio.security', 'data_mask_studio.environment')):
            raise AssertionError('Localization must be independent')
sys.meta_path.insert(0, Deny())
from data_mask_studio.i18n import Translator
assert Translator('pt-BR').text('common.settings') == 'Configurações'
assert Translator('en').text('common.settings') == 'Settings'
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path,
                            env=os.environ.copy(), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not Path(os.environ["LOCALAPPDATA"]).exists()


def test_resource_loading_from_zip_in_fresh_interpreter_and_unrelated_cwd(tmp_path):
    root = Path(__file__).resolve().parents[1] / "src" / "data_mask_studio"
    archive = tmp_path / "synthetic-package.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(root / "__init__.py", "data_mask_studio/__init__.py")
        for path in (root / "i18n").rglob("*"):
            if path.suffix in {".py", ".json"}:
                bundle.write(path, str(path.relative_to(root.parent)).replace("\\", "/"))
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from data_mask_studio import i18n
assert '.zip' in i18n.__file__
assert i18n.Translator('pt-BR').text('common.settings') == 'Configurações'
assert i18n.Translator('en').text('restoration.completed', count=2, filename='synthetic.csv') == 'Rows restored: 2. File: synthetic.csv.'
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(archive)], cwd=tmp_path,
                            env=os.environ.copy(), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
