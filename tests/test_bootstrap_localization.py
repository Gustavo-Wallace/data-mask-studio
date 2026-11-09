"""Bootstrap presentation and failures, without recovery retries or detail rewriting."""

from contextlib import contextmanager
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QTranslator, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QMessageBox
from shiboken6 import isValid

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio import app as app_module
from data_mask_studio.app import create_application
from data_mask_studio.gui.dialog_localization import _DialogButtonTranslator, install_dialog_button_translations
from data_mask_studio.environment import EnvironmentError
from data_mask_studio.i18n import CatalogError, Translator
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.publication import PublicationError
from data_mask_studio.security import KeyProviderError
from data_mask_studio.vault import VaultError
from test_main_navigation import FakeProtector
from test_maintenance import paths_for
from test_navigation_localization import build_localized_window


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    application = create_application([])
    button_translator = application.findChild(_DialogButtonTranslator)
    original = button_translator._translator
    original_objects = application.findChildren(QTranslator)
    try:
        yield application
    finally:
        install_dialog_button_translations(application, translator=original)
        assert application.findChildren(QTranslator) == original_objects


def dialog_result(app, operation, respond):
    """Capture Qt callback failures and cancel pending timers if bootstrap raises early."""
    errors, expired = [], []

    def reply():
        box = app.activeModalWidget()
        try:
            assert isinstance(box, QMessageBox)
            respond(box)
        except BaseException as error:
            errors.append(error)
        finally:
            if isinstance(box, QMessageBox) and box.isVisible():
                box.reject()

    def abort():
        expired.append(True)
        box = app.activeModalWidget()
        if isinstance(box, QMessageBox):
            box.reject()

    callback, timeout = QTimer(), QTimer()
    callback.setSingleShot(True)
    timeout.setSingleShot(True)
    callback.timeout.connect(reply)
    timeout.timeout.connect(abort)
    callback.start(0)
    timeout.start(5000)
    try:
        result = operation()
    finally:
        callback.stop()
        timeout.stop()
    if errors:
        raise errors[0]
    assert not expired, "Startup warning did not finish through its expected interaction"
    return result


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("error_type,titles", [
    (EnvironmentError, {"pt-BR": "Ambiente indisponível", "en": "Environment unavailable"}),
    (PublicationError, {"pt-BR": "Publicação pendente", "en": "Pending publication"}),
])
def test_bootstrap_warning_uses_explicit_locale_without_main_window(app, monkeypatch, caplog, capsys, locale, error_type, titles):
    translator = Translator(locale)
    body = "Safe service detail & <literal> {filename}."
    error = error_type(body)
    error.__cause__ = RuntimeError("PRIVATE_TRACEBACK_KEY_SENTINEL")
    calls, seen = [], []

    def initialize(**kwargs):
        calls.append(kwargs)
        raise error

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", lambda: pytest.fail("A failed startup must not enter the application loop"))

    def respond(box):
        seen.append(box)
        app.processEvents()
        assert box.parent() is None
        assert box.windowTitle() == box.accessibleName() == titles[locale]
        assert box.text() == box.accessibleDescription() == body
        assert box.textFormat() == Qt.TextFormat.PlainText
        assert box.icon() == QMessageBox.Icon.Warning and box.isModal()
        assert box.windowModality() == Qt.WindowModality.ApplicationModal
        assert box.standardButtons() == QMessageBox.StandardButton.Ok
        button = box.button(QMessageBox.StandardButton.Ok)
        assert button.text() == translator.text("dialog.button.ok") == "OK"
        assert box.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole
        assert box.defaultButton() is box.escapeButton() is button
        assert button.isDefault() and app.focusWidget() is button
        assert "PRIVATE_TRACEBACK_KEY_SENTINEL" not in box.text()
        button.click()

    assert dialog_result(app, lambda: app_module.run(translator=translator), respond) == 1
    assert len(calls) == len(seen) == 1 and calls[0]["translator"] is translator
    assert isinstance(calls[0]["preferences_service"], PreferencesService)
    assert error.args == (body,) and type(error) is error_type
    assert not caplog.records and not capsys.readouterr().err
    QCoreApplication.sendPostedEvents(seen[0], QEvent.Type.DeferredDelete)
    assert not isValid(seen[0])


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("interaction", ["click", "enter", "escape", "close"])
def test_warning_keeps_single_ok_modal_keyboard_and_close_semantics(app, monkeypatch, locale, interaction):
    translator = Translator(locale)
    baseline, seen = [], []
    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)

    def fail(**kwargs):
        raise EnvironmentError("Synthetic safe rejection")

    monkeypatch.setattr(app_module, "MainWindow", fail)

    def interact(box):
        finished = QSignalSpy(box.finished)
        clicked = QSignalSpy(box.buttonClicked)
        button = box.button(QMessageBox.StandardButton.Ok)
        if interaction == "click":
            button.click()
        elif interaction == "close":
            box.close()
        else:
            QTest.keyClick(box, Qt.Key.Key_Return if interaction == "enter" else Qt.Key.Key_Escape)
        return finished.count(), clicked.count(), finished.at(0)[0]

    # Measure the original static-warning behavior rather than assuming that
    # window-close emits buttonClicked (Qt closes it without that signal).
    assert dialog_result(app, lambda: QMessageBox.warning(None, "Synthetic baseline", "Safe detail"),
                         lambda box: baseline.append(interact(box))) == QMessageBox.StandardButton.Ok

    def respond(box):
        button = box.button(QMessageBox.StandardButton.Ok)
        assert box.defaultButton() is box.escapeButton() is button
        seen.append(interact(box))

    assert dialog_result(app, lambda: app_module.run(translator=translator), respond) == 1
    assert seen == baseline == [(1, 0, 0) if interaction == "close" else (1, 1, QMessageBox.StandardButton.Ok)]


@pytest.mark.parametrize("saved,explicit,expected", [
    (None, None, "pt-BR"), ("en", None, "en"), ("pt-BR", "en", "en"),
])
def test_successful_bootstrap_resolves_saved_locale_but_explicit_override_wins(app, tmp_path, monkeypatch, saved, explicit, expected):
    preferences = PreferencesService(tmp_path / "preferences.json", ui_languages_provider=lambda: ("pt-BR",))
    if saved is not None:
        assert preferences.save_language(saved).error is None
    before = preferences.path.read_bytes() if saved is not None else None
    requested = Translator(explicit) if explicit is not None else None
    windows, passed = [], []

    def initialize(**kwargs):
        passed.append(kwargs["translator"])
        window = build_localized_window(tmp_path, translator=kwargs["translator"], preferences_service=preferences)
        windows.append(window)
        return window

    def event_loop():
        app.processEvents()
        assert len(windows) == 1 and windows[0].isVisible()
        window = windows[0]
        assert window._translator is passed[0] and passed[0].locale == expected
        assert all(page._translator is passed[0] for page in window.page_widgets)
        assert window.anonymization_widget.select_button.text() == passed[0].text("masking.select_csv")
        if requested is not None:
            assert passed[0] is requested
        return 17

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", event_loop)
    assert app_module.run(translator=requested, preferences_service=preferences) == 17
    assert (preferences.path.read_bytes() if preferences.path.exists() else None) == before


@pytest.mark.parametrize("explicit", [False, True])
def test_failure_before_qapplication_propagates_without_dialog_translator_or_retry(app, monkeypatch, explicit):
    requested = Translator("en") if explicit else None
    failure = OSError("Synthetic AppUserModelID failure")
    calls = []

    def fail(**kwargs):
        calls.append(True)
        raise failure

    def forbidden(*args, **kwargs):
        pytest.fail("Do not retry bootstrap, construct UI or translate before QApplication succeeds")

    monkeypatch.setattr(app_module, "create_application", fail)
    monkeypatch.setattr(app_module, "Translator", forbidden)
    monkeypatch.setattr(app_module, "MainWindow", forbidden)
    monkeypatch.setattr(QMessageBox, "exec", forbidden)
    with pytest.raises(OSError) as caught:
        app_module.run(translator=requested)
    assert caught.value is failure and calls == [True]


def test_missing_translation_resources_propagate_without_recursive_error_ui(app, monkeypatch):
    failure = CatalogError("Invalid catalog resource.")
    calls = []

    def fail(locale):
        calls.append(locale)
        raise failure

    def forbidden(*args, **kwargs):
        pytest.fail("Do not construct MainWindow or recursively render unusable translation resources")

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "Translator", fail)
    monkeypatch.setattr(app_module, "MainWindow", forbidden)
    monkeypatch.setattr(QMessageBox, "exec", forbidden)
    with pytest.raises(CatalogError) as caught:
        app_module.run(preferences_service=PreferencesService(ui_languages_provider=lambda: ("pt-BR",)))
    assert caught.value is failure and calls == ["pt-BR"]


@pytest.mark.parametrize("error_type", [KeyProviderError, VaultError, RuntimeError])
def test_unclassified_fatal_startup_errors_keep_existing_propagation(app, monkeypatch, error_type):
    translator = Translator("en")
    failure = error_type("Synthetic unclassified failure")
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise failure

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", fail)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("Do not broaden caught error classes"))
    with pytest.raises(error_type) as caught:
        app_module.run(translator=translator)
    assert caught.value is failure and len(calls) == 1 and calls[0]["translator"] is translator
    assert isinstance(calls[0]["preferences_service"], PreferencesService)


def test_modal_execution_failure_is_not_hidden_or_retried_and_dialog_is_released(app, monkeypatch):
    translator = Translator("en")
    failure = RuntimeError("Synthetic modal failure")
    seen, calls = [], []

    def initialize(**kwargs):
        calls.append(kwargs)
        raise PublicationError("Synthetic safe publication failure")

    def fail(box):
        seen.append(box)
        raise failure

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(QMessageBox, "exec", fail)
    with pytest.raises(RuntimeError) as caught:
        app_module.run(translator=translator)
    assert caught.value is failure and len(seen) == len(calls) == 1
    QCoreApplication.sendPostedEvents(seen[0], QEvent.Type.DeferredDelete)
    assert not isValid(seen[0])


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("body", [
    "O ambiente exige recuperação de uma restauração interrompida.",
    '<img src="untrusted">startup.error.environment_title & {filename}',
])
def test_safe_service_body_remains_literal_and_presentation_does_no_filesystem_work(app, tmp_path, monkeypatch, locale, body):
    translator = Translator(locale)
    service = PreferencesService(tmp_path / "preferences.json")
    stored = service.load()
    preference_reads = []
    def startup_preferences():
        preference_reads.append(True)
        return stored
    # Startup now reads appearance preferences; the error presentation itself
    # must still perform no filesystem work, even with that dependency supplied.
    monkeypatch.setattr(service, "load", startup_preferences)
    sentinel = tmp_path / "synthetic-evidence.bin"
    sentinel.write_bytes(b"Synthetic evidence must not be changed")
    calls = []

    def initialize(**kwargs):
        assert preference_reads == [True]
        calls.append(True)
        raise EnvironmentError(body)

    def forbidden(*args, **kwargs):
        pytest.fail("Startup error presentation may not retry operations or access files")

    def respond(box):
        assert box.text() == box.accessibleDescription() == body
        assert box.textFormat() == Qt.TextFormat.PlainText
        box.button(QMessageBox.StandardButton.Ok).click()

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    with monkeypatch.context() as guard:
        for name in ("open", "read_text", "read_bytes", "stat", "exists", "resolve", "unlink", "mkdir"):
            guard.setattr(Path, name, forbidden)
        assert dialog_result(app, lambda: app_module.run(
            translator=translator, preferences_service=service), respond) == 1
    assert calls == [True] and sentinel.read_bytes() == b"Synthetic evidence must not be changed"


@contextmanager
def _foreign_button_translation(app):
    class ForeignButtons(QTranslator):
        def isEmpty(self):
            return False

        def translate(self, context, sourceText, disambiguation=None, n=-1):
            return "PLATFORM_OK" if context == "QPlatformTheme" and sourceText == "OK" else None

    existing = app.findChildren(QTranslator)
    original_ok = QCoreApplication.translate("QPlatformTheme", "OK")
    foreign = ForeignButtons(app)
    try:
        assert app.installTranslator(foreign)
        yield foreign
    finally:
        app.removeTranslator(foreign)
        # Uninstalling does not release QObject parent ownership. Destroy only
        # this test's translator, even while its Python wrapper is still held.
        foreign.deleteLater()
        QCoreApplication.sendPostedEvents(foreign, QEvent.Type.DeferredDelete)
        assert not isValid(foreign)
        assert app.findChildren(QTranslator) == existing
        assert all(isValid(translator) for translator in existing)
        assert QCoreApplication.translate("QPlatformTheme", "OK") == original_ok


def test_bootstrap_button_uses_explicit_catalog_even_if_qt_platform_translation_conflicts(app, monkeypatch):
    with _foreign_button_translation(app):
        assert QCoreApplication.translate("QPlatformTheme", "OK") == "PLATFORM_OK"
        monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)

        def fail(**kwargs):
            raise EnvironmentError("Synthetic safe service detail")

        monkeypatch.setattr(app_module, "MainWindow", fail)

        def respond(box):
            assert box.button(QMessageBox.StandardButton.Ok).text() == "OK"
            box.button(QMessageBox.StandardButton.Ok).click()

        assert dialog_result(app, lambda: app_module.run(translator=Translator("en")), respond) == 1


def test_foreign_translation_restores_qapplication_children_and_labels_after_exception(app):
    existing = app.findChildren(QTranslator)
    original_ok = QCoreApplication.translate("QPlatformTheme", "OK")
    failure = RuntimeError("Synthetic failure while a test translator is installed")
    with pytest.raises(RuntimeError) as caught:
        with _foreign_button_translation(app) as foreign:
            assert foreign.parent() is app
            assert app.findChildren(QTranslator) == existing + [foreign]
            assert QCoreApplication.translate("QPlatformTheme", "OK") == "PLATFORM_OK"
            raise failure
    assert caught.value is failure
    assert not isValid(foreign)
    assert app.findChildren(QTranslator) == existing
    assert all(isValid(translator) for translator in existing)
    assert QCoreApplication.translate("QPlatformTheme", "OK") == original_ok


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_actual_invalid_synthetic_restore_journal_remains_fail_closed_and_preserved(app, tmp_path, monkeypatch, locale):
    from data_mask_studio.environment import RESTORE_JOURNAL
    from data_mask_studio.environment_restore import recover_restore

    paths = paths_for(tmp_path)
    protector = FakeProtector()
    journal = paths.directory / RESTORE_JOURNAL
    protected = protector.protect(b"Synthetic invalid journal, not JSON")
    journal.write_bytes(protected)
    calls = []

    def initialize(**kwargs):
        calls.append(True)
        # The real first recovery operation performed by MainWindow, not its default environment.
        recover_restore(paths.directory, protector)
        pytest.fail("Invalid recovery journal must not permit MainWindow initialization")

    monkeypatch.setattr(app_module, "create_application", lambda **kwargs: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    translator = Translator(locale)

    def respond(box):
        assert box.windowTitle() == translator.text("startup.error.environment_title")
        assert box.text() == "Journal de restauração inválido; evidências preservadas."
        assert str(paths.directory) not in box.text()
        box.button(QMessageBox.StandardButton.Ok).click()

    assert dialog_result(app, lambda: app_module.run(translator=translator), respond) == 1
    assert calls == [True] and journal.read_bytes() == protected
    assert not paths.vault_database_path.exists() and not paths.hmac_key_path.exists()
    assert not paths.vault_key_path.exists() and not paths.profiles_path.exists()
