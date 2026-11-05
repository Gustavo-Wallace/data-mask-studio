"""Startup resolution and next-launch application while a session is busy."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLineEdit, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio import app as app_module
from data_mask_studio.app import create_application
from data_mask_studio.gui.dialog_localization import _DialogButtonTranslator
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesError, PreferencesService
from data_mask_studio.environment import EnvironmentError
from data_mask_studio.publication import PublicationError
from test_bootstrap_localization import dialog_result
from test_navigation_localization import build_localized_window


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    application = create_application([])
    button_translator = application.findChild(_DialogButtonTranslator)
    original = button_translator._translator
    original_objects = application.findChildren(QTranslator)
    try:
        yield application
    finally:
        # Restore this shared application's original locale, not its QObject ownership.
        button_translator._translator = original
        assert application.findChildren(QTranslator) == original_objects


class ObservedPreferences(PreferencesService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.loads = 0
        self.resolutions = []

    def load(self):
        self.loads += 1
        self.last_result = super().load()
        return self.last_result

    def resolve_language(self, preference):
        self.resolutions.append(preference.value)
        return super().resolve_language(preference)


def fake_bootstrap(app, monkeypatch, service, expected, *, override=None):
    seen = []

    def initialize(*, translator, preferences_service, live_language_switching):
        assert preferences_service is service and translator.locale == expected
        assert live_language_switching is (override is None)
        if override is not None:
            assert translator is override
        qt = app.findChildren(_DialogButtonTranslator)
        assert len(qt) == 1 and qt[0]._translator is translator
        assert len(app.findChildren(QTranslator)) == 1
        assert QCoreApplication.translate("QPlatformTheme", "&Yes") == ("&Sim" if expected == "pt-BR" else "&Yes")
        assert QCoreApplication.translate("QPlatformTheme", "&No") == ("&Não" if expected == "pt-BR" else "&No")
        assert QCoreApplication.translate("QPlatformTheme", "Cancel") == ("Cancelar" if expected == "pt-BR" else "Cancel")
        assert QCoreApplication.translate("QPlatformTheme", "OK") == "OK"
        assert QCoreApplication.translate("QLineEdit", "&Copy") == "&Copy"
        assert QCoreApplication.translate("QPlatformTheme", "Save") == "Save"
        seen.append(translator)
        return SimpleNamespace(show=lambda: seen.append("shown"))

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", lambda: 19)
    return seen


@pytest.mark.parametrize("saved,languages,expected", [
    ("pt-BR", ("en-US",), "pt-BR"), ("en", ("pt-BR",), "en"),
    ("system", ("pt-BR",), "pt-BR"), ("system", ("pt-Latn-BR",), "pt-BR"),
    ("system", ("en-US",), "en"), ("system", ("pt-PT",), "en"),
    ("system", ("unsupported", "pt-BR"), "en"), ("system", (), "en"),
    (None, ("pt-BR",), "pt-BR"), (None, ("en-US",), "en"),
])
def test_normal_entrypoint_resolves_persisted_or_system_language_once(app, tmp_path, monkeypatch, saved, languages, expected):
    detections = []

    def detect():
        detections.append(True)
        return languages

    service = ObservedPreferences(tmp_path / "preferences.json", ui_languages_provider=detect)
    if saved is not None:
        assert service.save_language(saved).error is None
    before = service.path.read_bytes() if saved is not None else None
    service.loads = 0
    seen = fake_bootstrap(app, monkeypatch, service, expected)
    assert app_module.run(preferences_service=service) == 19
    assert len(seen) == 2 and seen[1] == "shown"
    assert service.loads == 1 and service.resolutions == [saved or "system"]
    assert len(detections) == (0 if saved in ("en", "pt-BR") else 1)
    assert (service.path.read_bytes() if service.path.exists() else None) == before


@pytest.mark.parametrize("outcome", ["exception", "invalid_result"])
def test_failed_windows_detection_uses_english_without_writing(app, tmp_path, monkeypatch, outcome):
    calls = []

    def detect():
        calls.append(True)
        if outcome == "exception":
            raise OSError("Synthetic private OS failure")
        return None

    service = ObservedPreferences(tmp_path / "preferences.json", ui_languages_provider=detect)
    fake_bootstrap(app, monkeypatch, service, "en")
    assert app_module.run(preferences_service=service) == 19
    assert calls == [True] and service.loads == 1 and service.resolutions == ["system"]
    assert not service.path.exists()


@pytest.mark.parametrize("locale,languages", [("pt-BR", ("pt-BR",)), ("en", ("en-US",))])
@pytest.mark.parametrize("condition", ["malformed", "unsupported_language", "unsupported_schema", "unreadable"])
def test_invalid_preferences_use_system_safe_default_without_repair(app, tmp_path, monkeypatch, locale, languages, condition):
    raw = {
        "malformed": b"SYNTHETIC_PRIVATE_INVALID_JSON",
        "unsupported_language": b'{"schema_version":1,"language":"unsupported"}',
        "unsupported_schema": b'{"schema_version":99,"language":"en"}',
        "unreadable": b'{"schema_version":1,"language":"en"}',
    }[condition]
    service = ObservedPreferences(tmp_path / "preferences.json", ui_languages_provider=lambda: languages)
    service.path.write_bytes(raw)
    original_open = Path.open

    def read(path, *args, **kwargs):
        if path == service.path and condition == "unreadable":
            raise PermissionError("SYNTHETIC_PRIVATE_PATH")
        return original_open(path, *args, **kwargs)

    fake_bootstrap(app, monkeypatch, service, locale)
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", read)
        assert app_module.run(preferences_service=service) == 19
    assert service.loads == 1 and service.resolutions == ["system"]
    assert service.last_result.error is (PreferencesError.READ_FAILED if condition == "unreadable" else PreferencesError.INVALID_FILE)
    assert service.path.read_bytes() == raw


@pytest.mark.parametrize("saved,locale", [("pt-BR", "en"), ("en", "pt-BR")])
def test_explicit_override_bypasses_preference_resolution_and_catalog_reconstruction(app, tmp_path, monkeypatch, saved, locale):
    service = ObservedPreferences(tmp_path / "preferences.json")
    assert service.save_language(saved).error is None
    before = service.path.read_bytes()
    translator = Translator(locale)
    fake_bootstrap(app, monkeypatch, service, locale, override=translator)

    def forbidden(*args, **kwargs):
        pytest.fail("Explicit Translator must bypass persisted/Windows language and new catalog construction")

    with monkeypatch.context() as guard:
        guard.setattr(service, "load", forbidden)
        guard.setattr(service, "resolve_language", forbidden)
        guard.setattr(app_module, "Translator", forbidden)
        guard.setattr(Path, "open", forbidden)
        assert app_module.run(translator=translator, preferences_service=service) == 19
    assert service.path.read_bytes() == before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("error_type,identifier", [
    (EnvironmentError, "startup.error.environment_title"),
    (PublicationError, "startup.error.publication_title"),
])
def test_resolved_language_reaches_bootstrap_failure_before_main_window(app, tmp_path, monkeypatch, locale, error_type, identifier):
    service = ObservedPreferences(tmp_path / "preferences.json", ui_languages_provider=lambda: pytest.fail("Explicit preference needs no OS detection"))
    assert service.save_language(locale).error is None
    service.loads = 0
    calls = []

    def initialize(*, translator, preferences_service, live_language_switching):
        calls.append(translator)
        assert translator.locale == locale and preferences_service is service
        assert app.findChild(_DialogButtonTranslator)._translator is translator
        raise error_type("Detalhe seguro de serviço permanece literal.")

    def respond(box):
        assert box.parent() is None and box.windowTitle() == calls[0].text(identifier)
        assert box.text() == "Detalhe seguro de serviço permanece literal."
        assert box.defaultButton() is box.escapeButton() is box.button(QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).click()

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", lambda: pytest.fail("Failed initialization cannot enter application loop"))
    assert dialog_result(app, lambda: app_module.run(preferences_service=service), respond) == 1
    assert len(calls) == service.loads == 1


@pytest.mark.parametrize("portable", [False, True])
def test_default_bootstrap_preferences_use_shared_localappdata_path_not_executable_or_cwd(app, tmp_path, monkeypatch, portable):
    work = tmp_path / ("portable executable" if portable else "installed executable")
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "frozen", portable, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(work), raising=False)
    service = ObservedPreferences(ui_languages_provider=lambda: pytest.fail("Saved EN must not detect Windows"))
    assert service.path == tmp_path / "isolated-appdata" / "DataMaskStudio" / "preferences.json"
    assert service.save_language("en").error is None
    service.loads = 0
    fake_bootstrap(app, monkeypatch, service, "en")
    constructed = []
    monkeypatch.setattr(app_module, "PreferencesService", lambda: constructed.append(service) or service)
    assert app_module.run() == 19
    assert constructed == [service] and service.loads == 1
    assert list(work.iterdir()) == []


@pytest.mark.parametrize("initial,target", [("pt-BR", "en"), ("en", "pt-BR")])
def test_settings_save_preserves_active_session_worker_and_inputs_until_restart(app, tmp_path, monkeypatch, initial, target):
    service = ObservedPreferences(tmp_path / "preferences.json")
    assert service.save_language(initial).error is None
    service.loads = 0
    windows = []
    qt = app.findChild(_DialogButtonTranslator)

    def initialize(*, translator, preferences_service, live_language_switching):
        root = tmp_path / f"launch-{len(windows)}"
        root.mkdir()
        window = build_localized_window(root, translator=translator, preferences_service=preferences_service,
                                        live_language_switching=live_language_switching)
        windows.append(window)
        return window

    def first_session():
        app.processEvents()
        window = windows[0]
        translator = window._translator
        assert translator.locale == initial and service.loads == 1
        assert window.settings_widget._preferences is service
        source = tmp_path / "synthetic.csv"
        source.write_text("Name,Note\nAlice,Synthetic\n", encoding="utf-8")
        mask = window.anonymization_widget
        mask.load_csv(str(source))
        mask._action_fields[0].setCurrentIndex(mask._action_fields[0].findData("mask"))
        mask._prefix_fields[0].setText("NAME")
        mask.validate_current_configuration()
        mask._start_processing(tmp_path / "not-published.csv", overwrite=False)
        worker = mask._worker
        assert worker is not None and not worker._cancel_requested.is_set()
        window.set_current_page(window.page_index(window.settings_widget))
        app.processEvents()
        settings = window.settings_widget
        settings.language_combo.setCurrentIndex(settings.language_combo.findData(target))
        fields = window.findChildren(QLineEdit)
        values = [(field.text(), field.echoMode(), field.selectedText()) for field in fields]
        page = window.page_stack.currentWidget()
        navigation = QSignalSpy(window.navigation.current_changed)

        def forbidden(*args, **kwargs):
            pytest.fail("Saving Settings must not retranslate or cancel the current session")

        with monkeypatch.context() as guard:
            guard.setattr(window, "retranslate", forbidden)
            guard.setattr(service, "resolve_language", forbidden)
            guard.setattr(worker, "request_cancel", forbidden)
            settings.save_button.click()
        assert json.loads(service.path.read_text(encoding="utf-8"))["language"] == target
        assert settings.status_label.text() == translator.text("settings.saved")
        assert settings.notice_label.text() == translator.text("settings.notice.live")
        assert window._translator is settings._translator is qt._translator is translator
        assert mask._worker is worker and not worker._cancel_requested.is_set()
        assert [(field.text(), field.echoMode(), field.selectedText()) for field in fields] == values
        assert window.page_stack.currentWidget() is page and navigation.count() == 0
        assert not (tmp_path / "not-published.csv").exists()
        worker.cancelled.emit()
        worker.finished.emit()
        window.close()
        return 0

    def restarted_session():
        app.processEvents()
        assert windows[1]._translator.locale == target
        assert app.findChild(_DialogButtonTranslator) is qt and qt._translator is windows[1]._translator
        assert len(app.findChildren(QTranslator)) == 1
        windows[1].close()
        return 0

    monkeypatch.setattr(AnonymizationWorker, "start", lambda worker: None)
    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", first_session)
    assert app_module.run(preferences_service=service) == 0
    monkeypatch.setattr(app, "exec", restarted_session)
    assert app_module.run(preferences_service=service) == 0
    assert service.resolutions == [initial, target]


def test_saved_system_resolves_current_windows_language_on_each_launch(app, tmp_path, monkeypatch):
    languages = [("pt-BR",)]
    detections = []

    def detect():
        detections.append(languages[0])
        return languages[0]

    service = ObservedPreferences(tmp_path / "preferences.json", ui_languages_provider=detect)
    service.save_language("system")
    raw = service.path.read_bytes()
    for current, expected in (("pt-BR", "pt-BR"), ("en-US", "en")):
        languages[0] = (current,)
        fake_bootstrap(app, monkeypatch, service, expected)
        assert app_module.run(preferences_service=service) == 19
        assert service.path.read_bytes() == raw
    assert detections == [("pt-BR",), ("en-US",)]


def test_invalid_startup_preference_retains_safe_settings_error_and_original_bytes(app, tmp_path, monkeypatch):
    service = ObservedPreferences(tmp_path / "preferences.json", ui_languages_provider=lambda: ("pt-BR",))
    raw = b"SYNTHETIC_PRIVATE_INVALID_DOCUMENT"
    service.path.write_bytes(raw)
    windows = []

    def initialize(*, translator, preferences_service, live_language_switching):
        window = build_localized_window(tmp_path, translator=translator, preferences_service=preferences_service,
                                        live_language_switching=live_language_switching)
        windows.append(window)
        return window

    def event_loop():
        app.processEvents()
        window = windows[0]
        assert window._translator.locale == "pt-BR" and service.loads == 1
        window.set_current_page(window.page_index(window.settings_widget))
        app.processEvents()
        settings = window.settings_widget
        assert settings._preferences is service and service.loads == 2
        assert settings.status_label.text() == window._translator.text("settings.error.invalid_file")
        assert "SYNTHETIC_PRIVATE" not in settings.status_label.text()
        assert str(service.path) not in settings.status_label.text()
        assert settings.status_label.property("feedbackState") == "error" and not settings.save_button.isEnabled()
        settings.save_preference()
        assert service.path.read_bytes() == raw
        return 0

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", event_loop)
    assert app_module.run(preferences_service=service) == 0


def test_fresh_normal_main_constructs_one_effective_catalog_after_qapplication(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text('{"schema_version":1,"language":"en"}', encoding="utf-8")
    program = r'''
import sys
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtWidgets import QApplication
from data_mask_studio import app
from data_mask_studio.i18n import translator as messages
from data_mask_studio.preferences import PreferencesService
loads = []
read = messages.load_catalogs
def load():
    assert QApplication.instance() is not None
    loads.append(True)
    return read()
messages.load_catalogs = load
service = PreferencesService(sys.argv[1], ui_languages_provider=lambda: (_ for _ in ()).throw(AssertionError("No OS detection")))
app.PreferencesService = lambda: service
def window(*, translator, preferences_service, live_language_switching):
    assert translator.locale == "en" and preferences_service is service
    assert live_language_switching
    assert len(QApplication.instance().findChildren(QTranslator)) == 1
    assert QCoreApplication.translate("QPlatformTheme", "&No") == "&No"
    QApplication.instance().exec = lambda: 23
    return SimpleNamespace(show=lambda: None)
app.MainWindow = window
from data_mask_studio.main import main
assert main() == 23
assert loads == [True], loads
'''
    environment = os.environ.copy()
    environment.update(LOCALAPPDATA=str(tmp_path / "isolated-process-data"), QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-B", "-c", program, str(path)], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
