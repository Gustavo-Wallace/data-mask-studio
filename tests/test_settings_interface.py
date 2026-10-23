"""Settings presentation uses the real preferences store, never vault state."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QLocale, Qt
from PySide6.QtTest import QTest

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.settings_widget import SettingsWidget
from data_mask_studio.preferences import LanguagePreference, PreferencesError, PreferencesService
from data_mask_studio.preferences import service as preferences_module
from test_main_navigation import build_window


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def open_settings(service=None):
    app = create_application([])
    widget = SettingsWidget(service)
    widget.show()
    app.processEvents()
    return app, widget


def select(widget, language):
    widget.language_combo.setCurrentIndex(widget.language_combo.findData(language))


def test_default_is_stored_system_not_resolved_language_and_opening_never_writes():
    service = PreferencesService(ui_languages_provider=lambda: pytest.fail("Do not resolve language"))
    _, widget = open_settings(service)
    assert isinstance(widget.language_combo, ScrollSafeComboBox)
    assert [(widget.language_combo.itemText(i), widget.language_combo.itemData(i))
            for i in range(widget.language_combo.count())] == [
        ("Automático (Windows)", "system"), ("English", "en"), ("Português (Brasil)", "pt-BR"),
    ]
    assert widget.language_combo.currentData() == "system"
    assert widget.save_button.isEnabled() and widget.save_button.text() == "Salvar preferência"
    assert widget.save_button.property("role") == "primary"
    assert widget.language_label.buddy() is widget.language_combo
    assert widget.language_combo.accessibleName() == "Idioma do aplicativo"
    assert widget.language_combo.focusPolicy() is Qt.FocusPolicy.StrongFocus
    assert widget.status_label.property("feedbackState") == "neutral"
    assert "continua em português" in widget.notice_label.text()
    assert "quando a tradução estiver disponível" in widget.notice_label.text()
    assert widget.notice_label.wordWrap()
    assert not service.path.parent.exists()


@pytest.mark.parametrize("language", list(LanguagePreference))
def test_loads_saved_choice_without_rewriting_or_resolving_it(language, monkeypatch):
    service = PreferencesService()
    assert service.save_language(language).error is None
    before = service.path.read_bytes(), service.path.stat().st_mtime_ns
    monkeypatch.setattr(service, "save_language", lambda *_: pytest.fail("Opening must not save"))
    monkeypatch.setattr(service, "resolve_language", lambda *_: pytest.fail("Stored, not effective"))
    _, widget = open_settings(service)
    assert widget.language_combo.currentData() == language.value
    assert (service.path.read_bytes(), service.path.stat().st_mtime_ns) == before
    assert widget.status_label.property("feedbackState") == "neutral"


@pytest.mark.parametrize("language", list(LanguagePreference))
def test_each_choice_saves_once_and_persists_in_fresh_widgets(language, monkeypatch):
    service = PreferencesService()
    app, widget = open_settings(service)
    calls = []
    real_save = service.save_language

    def save(value):
        calls.append(value)
        assert not widget.save_button.isEnabled()
        widget.save_preference()  # Reentrant/double activation cannot write again.
        return real_save(value)

    monkeypatch.setattr(service, "save_language", save)
    select(widget, language.value)
    QTest.mouseClick(widget.save_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(widget.save_button, Qt.MouseButton.LeftButton)
    widget.save_preference()
    assert calls == [language]
    assert widget.status_label.text() == "Preferência de idioma salva."
    assert widget.status_label.property("feedbackState") == "success"
    assert not widget.save_button.isEnabled()
    assert json.loads(service.path.read_text(encoding="utf-8")) == {
        "schema_version": 1, "language": language.value,
    }
    assert list(service.path.parent.iterdir()) == [service.path]
    widget.close()
    fresh = SettingsWidget(PreferencesService(service.path))
    fresh.show()
    app.processEvents()
    assert fresh.language_combo.currentData() == language.value
    assert fresh.status_label.property("feedbackState") == "neutral"


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_can_return_to_automatic_after_override(language):
    service = PreferencesService()
    service.save_language(language)
    _, widget = open_settings(service)
    select(widget, "system")
    widget.save_button.click()
    assert service.load().preferences.language is LanguagePreference.SYSTEM
    assert widget.language_combo.currentData() == "system"
    assert widget.status_label.property("feedbackState") == "success"


def test_changing_selection_after_save_invalidates_success_and_allows_next_save():
    service = PreferencesService()
    _, widget = open_settings(service)
    select(widget, "en")
    widget.save_button.click()
    select(widget, "pt-BR")
    assert widget.status_label.property("feedbackState") == "neutral"
    assert not widget.status_label.text()
    assert widget.save_button.isEnabled()
    assert service.load().preferences.language is LanguagePreference.ENGLISH
    widget.save_button.click()
    assert service.load().preferences.language is LanguagePreference.BRAZILIAN_PORTUGUESE


@pytest.mark.parametrize("raw", [
    b"SYNTHETIC_PRIVATE_CONTENT_NOT_JSON",
    b'{"schema_version":2,"language":"en","future":"SYNTHETIC_PRIVATE_CONTENT"}',
    b'{"schema_version":1,"language":"unsupported"}',
])
def test_bad_documents_show_safe_error_and_are_preserved(raw):
    service = PreferencesService()
    service.path.parent.mkdir(parents=True)
    service.path.write_bytes(raw)
    _, widget = open_settings(service)
    assert widget.status_label.text() == PreferencesError.INVALID_FILE.message
    assert widget.status_label.property("feedbackState") == "error"
    assert not widget.save_button.isEnabled()
    select(widget, "en")
    widget.save_preference()
    assert service.path.read_bytes() == raw
    assert not widget.save_button.isEnabled()
    assert "SYNTHETIC_PRIVATE_CONTENT" not in widget.status_label.text()
    assert str(service.path) not in widget.status_label.text()


def test_unreadable_preferences_do_not_write_or_show_false_success(monkeypatch):
    service = PreferencesService()
    service.save_language("en")
    before = service.path.read_bytes()
    app = create_application([])
    original_open = Path.open

    def denied(path, *args, **kwargs):
        if path == service.path:
            raise PermissionError("SYNTHETIC_PRIVATE_PATH")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", denied)
        widget = SettingsWidget(service)
        widget.show()
        app.processEvents()
        assert widget.status_label.text() == PreferencesError.READ_FAILED.message
        assert widget.status_label.property("feedbackState") == "error"
        assert not widget.save_button.isEnabled()
        widget.save_preference()
    assert service.path.read_bytes() == before


def test_missing_appdata_shows_safe_unavailable_feedback(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA")
    _, widget = open_settings()
    assert widget.status_label.text() == PreferencesError.STORAGE_UNAVAILABLE.message
    assert widget.status_label.property("feedbackState") == "error"
    assert not widget.save_button.isEnabled()


def test_atomic_write_failure_restores_actual_stored_choice_and_allows_retry(monkeypatch):
    service = PreferencesService()
    service.save_language("en")
    before = service.path.read_bytes()
    _, widget = open_settings(service)
    select(widget, "pt-BR")

    def denied(*_):
        raise PermissionError("SYNTHETIC_PRIVATE_PATH")

    with monkeypatch.context() as patch:
        patch.setattr(preferences_module.os, "replace", denied)
        widget.save_button.click()
    assert widget.language_combo.currentData() == "en"
    assert widget.status_label.text() == PreferencesError.WRITE_FAILED.message
    assert widget.status_label.property("feedbackState") == "error"
    assert service.path.read_bytes() == before
    assert list(service.path.parent.iterdir()) == [service.path]
    select(widget, "pt-BR")
    widget.save_button.click()
    assert service.load().preferences.language is LanguagePreference.BRAZILIAN_PORTUGUESE
    assert widget.status_label.property("feedbackState") == "success"


def test_corruption_between_load_and_save_is_not_overwritten():
    service = PreferencesService()
    _, widget = open_settings(service)
    service.path.parent.mkdir(parents=True)
    service.path.write_bytes(b"SYNTHETIC_INVALID")
    select(widget, "en")
    widget.save_button.click()
    assert service.path.read_bytes() == b"SYNTHETIC_INVALID"
    assert widget.status_label.property("feedbackState") == "error"
    assert not widget.save_button.isEnabled()


def test_invalid_combo_value_cannot_be_persisted(monkeypatch):
    service = PreferencesService()
    service.save_language("en")
    before = service.path.read_bytes()
    _, widget = open_settings(service)
    monkeypatch.setattr(service, "save_language", lambda *_: pytest.fail("Reject invalid UI value"))
    widget.language_combo.setItemData(0, "invalid")
    widget.language_combo.setCurrentIndex(0)
    widget.save_button.click()
    assert widget.language_combo.currentData() == "en"
    assert widget.status_label.text() == PreferencesError.INVALID_LANGUAGE.message
    assert widget.status_label.property("feedbackState") == "error"
    assert service.path.read_bytes() == before


def test_standalone_settings_has_no_vault_keys_or_environment_dependency(monkeypatch):
    import data_mask_studio.gui.main_window as main_module
    from data_mask_studio.security import LocalKeyProvider
    from data_mask_studio.vault import VaultKeyProvider

    def forbidden(*_, **__):
        pytest.fail("Settings must not inspect vault/keys/environment")

    for name in ("initialize_existing_vault", "recover_restore"):
        monkeypatch.setattr(main_module, name, forbidden)
    monkeypatch.setattr(LocalKeyProvider, "get_key", forbidden)
    monkeypatch.setattr(VaultKeyProvider, "get_key", forbidden)
    service = PreferencesService()
    service.path.parent.mkdir(parents=True)
    sentinels = [service.path.parent / name for name in ("vault.db", "secret.key", "vault_key.dpapi")]
    for sentinel in sentinels:
        sentinel.write_bytes(b"SYNTHETIC_INVALID_ENVIRONMENT")
    _, widget = open_settings(service)
    select(widget, "en")
    widget.save_button.click()
    assert service.load().preferences.language is LanguagePreference.ENGLISH
    assert all(p.read_bytes() == b"SYNTHETIC_INVALID_ENVIRONMENT" for p in sentinels)


def test_settings_navigation_reload_keyboard_and_portuguese_ui_are_preserved(tmp_path, monkeypatch):
    app = create_application([])
    window = build_window(tmp_path)
    window.show()
    app.processEvents()
    service = PreferencesService()
    assert not service.path.exists() and window.current_page_index() == 0
    settings = window.settings_widget
    button = window.navigation.buttons[-1]
    assert button.text() == "Configurações"
    original_titles = [shell.header.title_label.text() for shell in window.page_shells]
    locale_before = QLocale().name()

    def forbidden(*_, **__):
        pytest.fail("Navigation/saving must not initialize or read the vault")

    import data_mask_studio.gui.main_window as main_module
    monkeypatch.setattr(main_module, "initialize_existing_vault", forbidden)
    monkeypatch.setattr(window._key_provider, "get_key", forbidden)
    monkeypatch.setattr(window._vault_key_provider, "get_key", forbidden)
    monkeypatch.setattr(window, "_vault_repository_factory", forbidden)
    window.navigation.buttons[0].setFocus()
    QTest.keyClick(window.navigation.buttons[0], Qt.Key.Key_Up)
    app.processEvents()
    assert window.page_stack.currentWidget().content is settings
    assert button.hasFocus() and button.isChecked()
    assert not service.path.exists()
    QTest.keyClick(button, Qt.Key.Key_Tab)
    assert window.navigation.about_button.hasFocus()
    settings.language_combo.setFocus()
    QTest.keyClick(settings.language_combo, Qt.Key.Key_Down)
    assert settings.language_combo.currentData() == "en"
    QTest.keyClick(settings.language_combo, Qt.Key.Key_Tab)
    assert settings.save_button.hasFocus()
    QTest.keyClick(settings.save_button, Qt.Key.Key_Space)
    assert service.load().preferences.language is LanguagePreference.ENGLISH
    assert QLocale().name() == locale_before
    assert [shell.header.title_label.text() for shell in window.page_shells] == original_titles
    assert window.anonymization_widget.select_button.text() == "Selecionar CSV"
    assert settings.notice_label.isVisible() and "continua em português" in settings.notice_label.text()
    window.set_current_page(0)
    service.save_language("pt-BR")  # A different instance writes while Settings is closed.
    window.set_current_page(window.page_index(settings))
    app.processEvents()
    assert settings.language_combo.currentData() == "pt-BR"
    assert settings.status_label.property("feedbackState") == "neutral"
    window.set_current_page(0)
    service.path.unlink()  # Only this test's synthetic preference document.
    window.set_current_page(window.page_index(settings))
    assert settings.language_combo.currentData() == "system"
    assert not service.path.exists()


def test_main_window_uses_injected_preferences_service_without_default_store(tmp_path):
    app = create_application([])
    service = PreferencesService(tmp_path / "explicit-preferences" / "preferences.json")
    service.save_language("pt-BR")
    # Reuse the normal, isolated window composition while replacing only its
    # preference dependency before Settings is first opened.
    from data_mask_studio.backup import EnvironmentPaths
    from data_mask_studio.gui.main_window import MainWindow
    from data_mask_studio.profiles import ProfileRepository, ProfileService
    from test_main_navigation import FakeProtector, FixedKeyProvider

    local = tmp_path / "environment"
    paths = EnvironmentPaths(local, local / "secret.key", local / "vault_key.dpapi",
                             local / "vault.db", local / "profiles.json")
    window = MainWindow(
        key_provider=FixedKeyProvider(b"H" * 32),
        vault_key_provider=FixedKeyProvider(b"V" * 32),
        backup_paths=paths,
        profile_service=ProfileService(ProfileRepository(paths.profiles_path)),
        data_protector=FakeProtector(),
        preferences_service=service,
    )
    window.show()
    window.set_current_page(window.page_index(window.settings_widget))
    app.processEvents()
    assert window.settings_widget.language_combo.currentData() == "pt-BR"
    select(window.settings_widget, "en")
    window.settings_widget.save_button.click()
    assert service.load().preferences.language is LanguagePreference.ENGLISH
    assert not PreferencesService().path.exists()


def test_returning_to_unreadable_store_replaces_previous_success(monkeypatch):
    service = PreferencesService()
    app, widget = open_settings(service)
    select(widget, "en")
    widget.save_button.click()
    assert widget.status_label.property("feedbackState") == "success"
    widget.hide()
    original_open = Path.open

    def denied(path, *args, **kwargs):
        if path == service.path:
            raise PermissionError("SYNTHETIC_PRIVATE_PATH")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", denied)
        widget.show()
        app.processEvents()
    assert widget.status_label.text() == PreferencesError.READ_FAILED.message
    assert widget.status_label.property("feedbackState") == "error"
    assert not widget.save_button.isEnabled()
    assert service.load().preferences.language is LanguagePreference.ENGLISH


@pytest.mark.parametrize("language", list(LanguagePreference))
def test_preference_survives_a_fresh_python_qt_application(language):
    service = PreferencesService()
    _, widget = open_settings(service)
    select(widget, language.value)
    widget.save_button.click()
    script = """
import sys
from data_mask_studio.app import create_application
from data_mask_studio.gui.settings_widget import SettingsWidget
app = create_application([])
widget = SettingsWidget()
widget.show()
app.processEvents()
assert widget.language_combo.currentData() == sys.argv[1]
assert widget.status_label.property('feedbackState') == 'neutral'
assert 'continua em português' in widget.notice_label.text()
widget.close()
"""
    environment = os.environ.copy()
    environment.update(QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", script, language.value],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
