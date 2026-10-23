"""Explicit presentation locale never changes stored language identities."""

import json

import pytest

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.settings_widget import SettingsWidget
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import (
    LanguagePreference, Preferences, PreferencesError, PreferencesResult, PreferencesService,
)
from data_mask_studio.preferences import service as preferences_module


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "synthetic-appdata"))


def open_settings(locale, service=None):
    app = create_application([])
    widget = SettingsWidget(service, translator=Translator(locale))
    widget.show()
    app.processEvents()
    return app, widget


def select(widget, language):
    widget.language_combo.setCurrentIndex(widget.language_combo.findData(language))


@pytest.mark.parametrize("locale, label, options, save, notice", [
    ("pt-BR", "Idioma do aplicativo",
     ["Automático (Windows)", "English", "Português (Brasil)"], "Salvar preferência",
     "Nesta etapa, o aplicativo continua em português."),
    ("en", "Application language",
     ["Automatic (Windows)", "English", "Portuguese (Brazil)"], "Save preference",
     "At this stage, the application remains in Portuguese."),
])
def test_all_settings_labels_and_accessibility_use_explicit_locale_without_writes(
    locale, label, options, save, notice,
):
    service = PreferencesService(ui_languages_provider=lambda: pytest.fail("No OS resolution"))
    _, widget = open_settings(locale, service)
    assert widget.language_label.text() == label
    assert widget.language_label.buddy() is widget.language_combo
    assert widget.language_combo.accessibleName() == label
    assert widget.language_combo.accessibleDescription() == Translator(locale).text(
        "settings.language.accessible_description"
    )
    assert [widget.language_combo.itemText(i) for i in range(3)] == options
    assert [widget.language_combo.itemData(i) for i in range(3)] == ["system", "en", "pt-BR"]
    assert widget.language_combo.currentData() == "system"
    assert widget.save_button.text() == save
    assert notice in widget.notice_label.text()
    assert widget.notice_label.wordWrap()
    assert not widget.status_label.text()
    assert widget.status_label.property("feedbackState") == "neutral"
    assert not service.path.parent.exists()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("language", list(LanguagePreference))
def test_localized_settings_save_stable_ids_reload_and_keep_duplicate_save_protection(
    locale, language, monkeypatch,
):
    service = PreferencesService()
    app, widget = open_settings(locale, service)
    calls = []
    real_save = service.save_language

    def save(value):
        calls.append(value)
        assert not widget.save_button.isEnabled()
        widget.save_preference()  # Reentrant activation cannot persist twice.
        return real_save(value)

    monkeypatch.setattr(service, "save_language", save)
    select(widget, language.value)
    widget.save_button.click()
    assert calls == [language]
    assert widget.status_label.text() == Translator(locale).text("settings.saved")
    assert widget.status_label.property("feedbackState") == "success"
    assert not widget.save_button.isEnabled()
    assert json.loads(service.path.read_text(encoding="utf-8")) == {
        "schema_version": 1, "language": language.value,
    }
    before = service.path.read_bytes(), service.path.stat().st_mtime_ns
    widget.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    widget.save_button.click()
    widget.save_preference()
    assert calls == [language]
    assert (service.path.read_bytes(), service.path.stat().st_mtime_ns) == before
    fresh = SettingsWidget(PreferencesService(service.path), translator=Translator(locale))
    fresh.show()
    app.processEvents()
    assert fresh.language_combo.currentData() == language.value
    assert fresh.status_label.property("feedbackState") == "neutral"
    assert not fresh.status_label.text()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_localized_settings_can_return_to_automatic(locale):
    service = PreferencesService()
    service.save_language("en")
    _, widget = open_settings(locale, service)
    select(widget, "system")
    widget.save_button.click()
    assert service.load().preferences.language is LanguagePreference.SYSTEM
    assert widget.status_label.text() == Translator(locale).text("settings.saved")


@pytest.mark.parametrize("acknowledged", [False, True])
def test_retranslation_preserves_widgets_unsaved_or_saved_state_and_emits_no_selection_signal(
    acknowledged, monkeypatch,
):
    service = PreferencesService()
    service.save_language("en")
    _, widget = open_settings("pt-BR", service)
    select(widget, "pt-BR")
    if acknowledged:
        widget.save_button.click()
    before = service.path.read_bytes(), service.path.stat().st_mtime_ns
    references = (widget.language_combo, widget.language_label, widget.save_button,
                  widget.notice_label, widget.status_label)
    state = (widget.language_combo.currentIndex(), widget.language_combo.currentData(),
             widget.save_button.isEnabled(), widget._acknowledged_language, widget._readable)
    selection_signals = []
    widget.language_combo.currentIndexChanged.connect(selection_signals.append)
    widget.language_combo.currentTextChanged.connect(selection_signals.append)
    monkeypatch.setattr(service, "load", lambda: pytest.fail("Retranslation must not reload"))
    monkeypatch.setattr(service, "save_language", lambda *_: pytest.fail("Retranslation must not save"))
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert references == (widget.language_combo, widget.language_label, widget.save_button,
                              widget.notice_label, widget.status_label)
        assert state == (widget.language_combo.currentIndex(), widget.language_combo.currentData(),
                         widget.save_button.isEnabled(), widget._acknowledged_language,
                         widget._readable)
        assert [widget.language_combo.itemData(i) for i in range(3)] == ["system", "en", "pt-BR"]
        assert widget.status_label.property("feedbackState") == (
            "success" if acknowledged else "neutral"
        )
        assert widget.status_label.text() == (
            Translator(locale).text("settings.saved") if acknowledged else ""
        )
    assert selection_signals == []
    assert (service.path.read_bytes(), service.path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("error", list(PreferencesError))
def test_each_typed_preferences_error_is_localized_without_exposing_storage_details(
    locale, error, monkeypatch,
):
    service = PreferencesService()
    result = PreferencesResult(Preferences(LanguagePreference.ENGLISH), error)
    monkeypatch.setattr(service, "load", lambda: result)
    monkeypatch.setattr(service, "save_language", lambda *_: pytest.fail("Unreadable store"))
    _, widget = open_settings(locale, service)
    assert widget.language_combo.currentData() == "en"
    assert widget.status_label.text() == Translator(locale).text(f"settings.error.{error.value}")
    assert widget.status_label.property("feedbackState") == "error"
    assert not widget.save_button.isEnabled()
    assert str(service.path) not in widget.status_label.text()
    widget.save_preference()
    other_locale = "en" if locale == "pt-BR" else "pt-BR"
    widget.retranslate(Translator(other_locale))
    assert widget.status_label.text() == Translator(other_locale).text(
        f"settings.error.{error.value}"
    )
    assert widget.status_label.property("feedbackState") == "error"
    assert not widget.save_button.isEnabled()
    assert widget.language_combo.currentData() == "en"
    assert not service.path.exists()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_malformed_store_is_preserved_with_localized_error(locale):
    service = PreferencesService()
    service.path.parent.mkdir(parents=True)
    raw = b'SYNTHETIC_PRIVATE_DOCUMENT_NOT_JSON'
    service.path.write_bytes(raw)
    _, widget = open_settings(locale, service)
    select(widget, "en")
    widget.save_preference()
    assert service.path.read_bytes() == raw
    assert widget.status_label.text() == Translator(locale).text("settings.error.invalid_file")
    assert "SYNTHETIC_PRIVATE" not in widget.status_label.text()
    assert not widget.save_button.isEnabled()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_write_failure_keeps_stored_choice_and_localizes_error_and_retry(locale, monkeypatch):
    service = PreferencesService()
    service.save_language("en")
    _, widget = open_settings(locale, service)
    select(widget, "pt-BR")
    before = service.path.read_bytes()

    def denied(*_):
        raise PermissionError("SYNTHETIC_PRIVATE_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr(preferences_module.os, "replace", denied)
        widget.save_button.click()
    assert service.path.read_bytes() == before
    assert widget.language_combo.currentData() == "en"
    assert widget.status_label.text() == Translator(locale).text("settings.error.write_failed")
    assert widget.status_label.property("feedbackState") == "error"
    assert "SYNTHETIC_PRIVATE" not in widget.status_label.text()
    assert list(service.path.parent.iterdir()) == [service.path]
    select(widget, "pt-BR")
    widget.save_button.click()
    assert service.load().preferences.language is LanguagePreference.BRAZILIAN_PORTUGUESE
    assert widget.status_label.text() == Translator(locale).text("settings.saved")


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_invalid_ui_value_is_not_saved_and_validation_error_is_localized(locale, monkeypatch):
    service = PreferencesService()
    service.save_language("en")
    _, widget = open_settings(locale, service)
    before = service.path.read_bytes()
    monkeypatch.setattr(service, "save_language", lambda *_: pytest.fail("Reject invalid UI value"))
    widget.language_combo.setItemData(0, "invalid")
    widget.language_combo.setCurrentIndex(0)
    widget.save_button.click()
    assert widget.language_combo.currentData() == "en"
    assert service.path.read_bytes() == before
    assert widget.status_label.text() == Translator(locale).text("settings.error.invalid_language")
    assert widget.status_label.property("feedbackState") == "error"


def test_unsupported_explicit_locale_falls_back_to_english_without_resolving_preference():
    service = PreferencesService(ui_languages_provider=lambda: pytest.fail("No OS resolution"))
    service.save_language("pt-BR")
    _, widget = open_settings("unsupported", service)
    assert widget.language_label.text() == "Application language"
    assert widget.language_combo.currentData() == "pt-BR"
    assert widget.save_button.text() == "Save preference"
