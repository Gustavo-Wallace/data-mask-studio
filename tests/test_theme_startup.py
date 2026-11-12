"""Startup-only appearance uses independent, persistent theme choices."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from language_save_diagnostics import language_save_diagnostics
from data_mask_studio import app as app_module
from data_mask_studio.gui import theme_resolution as resolution
from data_mask_studio.gui.settings_widget import SettingsWidget
from data_mask_studio.gui.styles import application_palette, application_stylesheet, widget_palette
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesError, PreferencesResult, PreferencesService
from test_bootstrap_localization import app
from test_live_language_settings import WaitingWorker
from test_localization_integration import worker_references
from test_navigation_localization import build_localized_window


def forbidden(*args, **kwargs):
    pytest.fail("Saving/retranslating a theme must not apply it or perform unrelated operations")


@pytest.mark.parametrize("choice,expected", [("light", "light"), ("dark", "dark")])
def test_explicit_theme_never_queries_windows(choice, expected):
    assert resolution.resolve_theme(choice, system_theme_provider=forbidden) == expected


@pytest.mark.parametrize("hint,expected", [
    ("light", "light"), ("dark", "dark"), (None, "dark"), ("unknown", "dark"),
])
def test_system_resolution_is_conservative(hint, expected):
    assert resolution.resolve_theme("system", system_theme_provider=lambda: hint) == expected


def test_detection_failure_is_a_dark_fallback():
    def unavailable():
        raise OSError("Synthetic unavailable appearance service")
    assert resolution.resolve_theme("system", system_theme_provider=unavailable) == "dark"


@pytest.mark.parametrize("scheme,expected", [
    (Qt.ColorScheme.Light, "light"), (Qt.ColorScheme.Dark, "dark"), (Qt.ColorScheme.Unknown, None),
])
def test_windows_detection_reads_platform_hint_not_the_applied_palette(monkeypatch, scheme, expected):
    monkeypatch.setattr(resolution.sys, "platform", "win32")
    application = SimpleNamespace(styleHints=lambda: SimpleNamespace(colorScheme=lambda: scheme))
    monkeypatch.setattr(resolution, "QGuiApplication", SimpleNamespace(instance=lambda: application))
    assert resolution.detect_windows_theme() == expected


@pytest.mark.parametrize("condition", ["no-app", "no-api", "error", "not-windows"])
def test_unavailable_platform_hint_is_safe(monkeypatch, condition):
    monkeypatch.setattr(resolution.sys, "platform", "linux" if condition == "not-windows" else "win32")
    def instance():
        if condition == "not-windows":
            pytest.fail("Do not inspect another platform")
        if condition == "error":
            raise RuntimeError("Synthetic Qt failure")
        return None if condition == "no-app" else SimpleNamespace()
    monkeypatch.setattr(resolution, "QGuiApplication", SimpleNamespace(instance=instance))
    assert resolution.detect_windows_theme() is None


@pytest.mark.parametrize("language", ["pt-BR", "en"])
@pytest.mark.parametrize("hint", ["light", "dark", None])
def test_startup_applies_system_theme_before_pages_and_dialogs_without_rewriting_choice(
    app, tmp_path, monkeypatch, hint, language,
):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_language(language).error is None
    before = service.path.read_bytes(), service.path.stat().st_mtime_ns
    calls, windows = [], []
    colors = LIGHT_PALETTE if hint == "light" else DARK_PALETTE
    def detect():
        calls.append("appearance")
        return hint
    def initialize(**kwargs):
        assert app.palette() == application_palette(colors)
        assert app.styleSheet() == application_stylesheet(colors)
        window = build_localized_window(tmp_path, **kwargs)
        windows.append(window)
        return window
    def event_loop():
        app.processEvents()
        window = windows[0]
        assert len(window.page_widgets) == 8 and window._translator.locale == language
        assert all(widget_palette(page) is colors for page in window.page_widgets)
        window.set_current_page(7)
        app.processEvents()
        assert window.settings_widget.theme_combo.currentData() == "system"
        window.show_about_dialog()
        app.processEvents()
        assert widget_palette(window.about_dialog) is colors
        # Parentless standard dialogs must use the same application scope.
        message = QMessageBox()
        assert widget_palette(message) is colors
        assert message.palette().color(QPalette.ColorRole.Window).name() == colors.window
        return 0
    monkeypatch.setattr(resolution, "detect_windows_theme", detect)
    monkeypatch.setattr(app_module, "create_application", lambda **_: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", event_loop)
    assert app_module.run(preferences_service=service) == 0
    assert calls == ["appearance"]
    assert (service.path.read_bytes(), service.path.stat().st_mtime_ns) == before
    assert service.load().preferences.theme.value == "system"


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("choice", ["system", "light", "dark"])
def test_settings_persists_theme_only_once_without_applying_it(app, tmp_path, monkeypatch, locale, choice):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_language("pt-BR").error is None
    widget = SettingsWidget(service, translator=Translator(locale))
    widget.show()
    app.processEvents()
    assert widget.theme_combo.currentData() == "system"
    assert [widget.theme_combo.itemData(i) for i in range(3)] == ["system", "light", "dark"]
    assert widget.theme_label.buddy() is widget.theme_combo
    assert widget.theme_combo.accessibleName() == widget.theme_label.text()
    assert widget.theme_notice_label.text() == Translator(locale).text("settings.theme.notice")
    # Leave an unsaved language edit; saving theme must neither save nor reset it.
    widget.language_combo.setCurrentIndex(widget.language_combo.findData("en"))
    widget.theme_combo.setCurrentIndex(widget.theme_combo.findData(choice))
    notification = QSignalSpy(widget.language_saved)
    calls, real_save = [], service.save_theme
    def save(value):
        calls.append(value.value)
        assert not widget.theme_save_button.isEnabled()
        widget.save_theme_preference()  # Reentrant delivery cannot write twice.
        return real_save(value)
    style, palette, qss = app.style(), app.palette(), app.styleSheet()
    with monkeypatch.context() as guard:
        guard.setattr(service, "save_theme", save)
        guard.setattr(service, "save_language", forbidden)
        guard.setattr(service, "resolve_language", forbidden)
        guard.setattr(resolution, "detect_windows_theme", forbidden)
        guard.setattr(app, "setStyle", forbidden)
        guard.setattr(app, "setPalette", forbidden)
        guard.setattr(app, "setStyleSheet", forbidden)
        widget.theme_save_button.click()
        widget.theme_save_button.click()
        widget.save_theme_preference()
    assert calls == [choice] and notification.count() == 0
    stored = service.load().preferences
    assert stored.theme.value == choice and stored.language.value == "pt-BR"
    assert widget.language_combo.currentData() == "en"
    assert widget.theme_status_label.text() == Translator(locale).text("settings.theme.saved")
    assert widget.theme_status_label.property("feedbackState") == "success"
    assert app.style() is style and app.palette() == palette and app.styleSheet() == qss


def test_theme_retranslation_preserves_drafts_focus_and_workers_without_io(app, tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    window = build_localized_window(tmp_path, preferences_service=service)
    window.show()
    window.set_current_page(7)
    app.processEvents()
    widget = window.settings_widget
    widget.theme_combo.setCurrentIndex(widget.theme_combo.findData("light"))
    widget.theme_save_button.click()
    widget.theme_combo.setFocus()
    pages, workers, focus = window.page_widgets, worker_references(window), app.focusWidget()
    style, palette, qss = app.style(), app.palette(), app.styleSheet()
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    # Keep an existing dialog instance; language rendering must not recreate it.
    message = QMessageBox(window)
    message.setText("Synthetic dialog")
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", forbidden)
        guard.setattr(service, "load", forbidden)
        guard.setattr(service, "save_theme", forbidden)
        guard.setattr(resolution, "detect_windows_theme", forbidden)
        for translator in translators:
            window.retranslate(translator)
            assert widget.theme_status_label.text() == translator.text("settings.theme.saved")
            assert widget.theme_combo.currentData() == "light"
            assert widget.theme_status_label.property("feedbackState") == "success"
    assert window.page_widgets == pages and worker_references(window) == workers
    assert app.focusWidget() is focus and window.current_page_index() == 7
    assert message.text() == "Synthetic dialog"
    assert app.style() is style and app.palette() == palette and app.styleSheet() == qss


def test_theme_save_during_owned_work_is_deferred_and_language_draft_remains_independent(
    app, tmp_path, monkeypatch, language_save_diagnostics,
):
    service = PreferencesService(tmp_path / "preferences.json")
    window = build_localized_window(tmp_path, preferences_service=service, live_language_switching=True)
    window.show()
    window.set_current_page(7)
    app.processEvents()
    settings = window.settings_widget
    pages, translator = window.page_widgets, window._translator
    style, palette, qss = app.style(), app.palette(), app.styleSheet()
    worker = WaitingWorker()
    language_save_diagnostics.watch(window, service, worker)
    worker.start()
    assert worker.entered.wait(2)
    try:
        with monkeypatch.context() as guard:
            guard.setattr(window.anonymization_widget, "_worker", worker)
            guard.setattr(window, "retranslate", forbidden)
            guard.setattr(worker, "request_cancel", forbidden, raising=False)
            guard.setattr(app, "setStyle", forbidden)
            guard.setattr(app, "setPalette", forbidden)
            guard.setattr(app, "setStyleSheet", forbidden)
            settings.theme_combo.setCurrentIndex(settings.theme_combo.findData("light"))
            settings.theme_save_button.click()
            assert worker.isRunning() and not worker.isInterruptionRequested()
            assert window.anonymization_widget._worker is worker
    finally:
        worker.release.set()
        assert worker.wait(3000)
    assert service.load().preferences.theme.value == "light"
    assert window.page_widgets == pages and window._translator is translator
    assert app.style() is style and app.palette() == palette and app.styleSheet() == qss
    # A live language save must retain an independent, still-unsaved theme draft.
    settings.theme_combo.setCurrentIndex(settings.theme_combo.findData("dark"))
    settings.language_combo.setCurrentIndex(settings.language_combo.findData("en"))
    language_save_diagnostics.capture("before_language_save")
    settings.save_button.click()
    language_save_diagnostics.capture("after_language_save")
    assert window._translator.locale == "en"
    assert settings.theme_combo.currentData() == "dark"
    assert service.load().preferences.theme.value == "light"
    assert app.style() is style and app.palette() == palette and app.styleSheet() == qss


@pytest.mark.parametrize("failure", ["write", "invalid-choice", "invalid-file"])
def test_theme_save_failure_retains_stored_choice_and_safe_error(app, tmp_path, monkeypatch, failure):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_theme("dark").error is None
    widget = SettingsWidget(service, translator=Translator("en"))
    widget.show()
    app.processEvents()
    before = service.path.read_bytes()
    widget.theme_combo.setCurrentIndex(widget.theme_combo.findData("light"))
    if failure == "write":
        monkeypatch.setattr(service, "save_theme", lambda _: PreferencesResult(
            service.load().preferences, PreferencesError.WRITE_FAILED))
    elif failure == "invalid-choice":
        widget.theme_combo.addItem("Synthetic invalid choice", "invalid")
        widget.theme_combo.setCurrentIndex(3)
    else:
        service.path.write_bytes(b"not-json")
        before = service.path.read_bytes()
    widget.theme_save_button.click()
    assert service.path.read_bytes() == before
    assert widget.theme_status_label.property("feedbackState") == "error"
    assert widget.theme_combo.currentData() == ("system" if failure == "invalid-file" else "dark")
    assert widget.theme_save_button.isEnabled() is (failure != "invalid-file")
