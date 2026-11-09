"""Storage of theme choices does not activate appearance or interfere with language."""

import pytest
from PySide6.QtCore import QTranslator
from PySide6.QtTest import QSignalSpy

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio import app as app_module
from data_mask_studio.preferences import PreferencesService, ThemePreference
from data_mask_studio.gui.dialog_localization import _DialogButtonTranslator
from data_mask_studio.gui.styles import APPLICATION_THEME_NAME, application_palette, application_stylesheet
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from test_bootstrap_localization import app
from test_navigation_localization import build_localized_window
from test_live_language_settings import save, assert_applied


@pytest.mark.parametrize("theme", list(ThemePreference))
def test_normal_startup_resolves_language_and_stored_theme_without_coupling_saves(app, tmp_path, monkeypatch, theme):
    monkeypatch.setattr("data_mask_studio.gui.theme_resolution.detect_windows_theme", lambda: "dark")
    service = PreferencesService(tmp_path / "preferences.json",
        ui_languages_provider=lambda: pytest.fail("Explicit English does not query the OS"))
    assert service.save_language("en").error is None
    assert service.save_theme(theme).error is None
    windows = []
    def initialize(**kwargs):
        window = build_localized_window(tmp_path, **kwargs)
        windows.append(window)
        return window
    def event_loop():
        app.processEvents()
        window = windows[0]
        assert window._translator.locale == "en"
        colors = LIGHT_PALETTE if theme is ThemePreference.LIGHT else DARK_PALETTE
        name = "DataMaskStudioLight" if theme is ThemePreference.LIGHT else APPLICATION_THEME_NAME
        assert app.property("dataMaskStudioTheme") == name
        assert app.palette() == application_palette(colors) and app.styleSheet() == application_stylesheet(colors)
        translator = window._translator
        qt_translators = app.findChildren(QTranslator)
        assert len(qt_translators) == 1 and isinstance(qt_translators[0], _DialogButtonTranslator)
        assert qt_translators[0]._translator is translator
        pages, style, palette, stylesheet = window.page_widgets, app.style(), app.palette(), app.styleSheet()
        settings = window.settings_widget
        notifications = QSignalSpy(settings.language_saved)
        def forbidden(*args, **kwargs):
            pytest.fail("Theme storage must not apply appearance, retranslate or resolve language")
        with monkeypatch.context() as guard:
            guard.setattr(window, "retranslate", forbidden)
            guard.setattr(service, "resolve_language", forbidden)
            guard.setattr(app, "setPalette", forbidden)
            guard.setattr(app, "setStyleSheet", forbidden)
            for choice in ThemePreference:
                result = service.save_theme(choice)
                assert result.error is None and result.preferences.language.value == "en"
        assert window.page_widgets == pages and window._translator is translator
        assert app.style() is style and app.palette() == palette and app.styleSheet() == stylesheet
        assert app.findChildren(QTranslator) == qt_translators and qt_translators[0]._translator is translator
        assert notifications.count() == 0
        return 0
    monkeypatch.setattr(app_module, "create_application", lambda **_: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", event_loop)
    assert app_module.run(preferences_service=service) == 0


def test_live_settings_language_save_preserves_newer_theme_without_applying_it(app, tmp_path):
    service = PreferencesService(tmp_path / "preferences.json")
    window = build_localized_window(tmp_path, preferences_service=service, live_language_switching=True)
    window.show()
    window.set_current_page(7)
    app.processEvents()
    assert window.settings_widget.language_combo.currentData() == "system"
    assert PreferencesService(service.path).save_theme("light").error is None
    save(window, "en")
    assert_applied(window, "en")
    assert service.load().preferences.theme is ThemePreference.LIGHT
    assert app.property("dataMaskStudioTheme") == "DataMaskStudioDark"
