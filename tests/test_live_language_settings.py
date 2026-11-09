"""Settings applies synchronously only across a proven idle GUI boundary."""

from pathlib import Path
import sqlite3
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QLocale, QThread, QTranslator, Qt
from PySide6.QtGui import QWindow
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio import app as app_module
from data_mask_studio.batch import BatchFileStatus
from data_mask_studio.gui.dialog_localization import (
    _DialogButtonTranslator, install_dialog_button_translations,
)
from data_mask_studio.i18n import Translator, load_catalogs, validate_catalogs
from data_mask_studio.preferences import PreferencesError, PreferencesResult, PreferencesService
from test_bootstrap_localization import app, dialog_result
from test_localization_integration import labels, worker_references
from test_navigation_localization import build_localized_window


def save(window, language):
    settings = window.settings_widget
    settings.language_combo.setCurrentIndex(settings.language_combo.findData(language))
    settings.save_button.click()


@pytest.fixture
def session(app, tmp_path):
    service = PreferencesService(tmp_path / "preferences.json", ui_languages_provider=lambda: ("pt-BR",))
    window = build_localized_window(tmp_path, preferences_service=service, live_language_switching=True)
    install_dialog_button_translations(app, translator=window._translator)
    window.show()
    window.set_current_page(window.page_index(window.settings_widget))
    app.processEvents()
    return app, window, service


@pytest.fixture(params=[Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier],
                ids=["plain", "shift-active"])
def selection_input_state(session, request):
    app, window, _ = session
    original = app.keyboardModifiers()
    # QWindow delivery, unlike QWidget-only key events, updates Qt's shared
    # modifier state. Restore it even when a selection assertion fails.
    try:
        QTest.keyPress(window.windowHandle(), Qt.Key.Key_Shift, request.param)
        assert app.keyboardModifiers() == request.param
        yield
    finally:
        QTest.keyRelease(window.windowHandle(), Qt.Key.Key_Shift, original)
        assert app.keyboardModifiers() == original


def assert_applied(window, locale):
    translator = window._translator
    assert translator.locale == locale
    assert all(control.text() == translator.text(identifier) for control, identifier in labels(window))
    assert window.settings_widget.status_label.text() == translator.text("settings.saved.applied")
    assert window.settings_widget.status_label.property("feedbackState") == "success"


def assert_deferred(window, translator):
    assert window._translator is translator
    assert window.settings_widget.status_label.text() == translator.text("settings.saved")
    assert window.settings_widget.status_label.property("feedbackState") == "success"


def test_idle_cycles_preserve_all_pages_inputs_results_focus_and_single_qt_translator(session, tmp_path, selection_input_state):
    app, window, service = session
    source = tmp_path / "synthetic.csv"
    source.write_text("Name,Note\nAlice,Synthetic\n", encoding="utf-8")
    mask = window.anonymization_widget
    mask.load_csv(str(source))
    mask._action_fields[0].setCurrentIndex(mask._action_fields[0].findData("mask"))
    mask._prefix_fields[0].setText("NAME")
    mask._output_name_fields[0].setText("Original <Name>")
    mask.transfer_controls.checkbox.setChecked(True)
    mask.transfer_controls.password.setText("Synthetic pass")
    mask.transfer_controls.confirmation.setText("Synthetic pass")
    window.batch_widget.add_paths([source])
    window.batch_widget.output_field.setText(str(tmp_path / "published"))
    published = tmp_path / "published" / "synthetic_masked.csv"
    published.parent.mkdir()
    published.write_bytes(b"NAME\nNAME-SYNTHETIC\n")
    item = window.batch_widget.files[0]
    item.status = BatchFileStatus.COMPLETED
    item.output_path = published
    item.result_message = "Literal published result"
    window.batch_widget._output_directory = published.parent
    window.batch_widget.open_output_button.setEnabled(True)
    window.batch_widget._refresh_table()
    # Select as a user would on the visible batch page. selectRow() consults
    # shared keyboard modifiers; Shift can extend from an invalid anchor and
    # leave an initially unselected table empty before any retranslation.
    window.set_current_page(window.page_index(window.batch_widget))
    app.processEvents()
    table = window.batch_widget.file_table
    row_index = table.model().index(0, 0)
    assert table.isVisible() and table.isEnabled()
    assert table.rowCount() == table.model().rowCount() == 1
    assert row_index.isValid()
    assert row_index.flags() & Qt.ItemFlag.ItemIsSelectable
    assert row_index.flags() & Qt.ItemFlag.ItemIsEnabled
    cell = table.visualRect(row_index)
    assert cell.isValid() and table.viewport().rect().contains(cell.center())
    selection = table.selectionModel()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, cell.center())
    assert [index.row() for index in selection.selectedRows()] == [0]
    selected_cells = [(index.row(), index.column()) for index in selection.selectedIndexes()]
    assert selected_cells == [(0, column) for column in range(table.columnCount())]
    window.set_current_page(window.page_index(window.settings_widget))
    app.processEvents()
    assert [index.row() for index in selection.selectedRows()] == [0]
    window.restoration_widget.progress_bar.setValue(57)
    window.restoration_widget._set_status("Literal service detail", is_error=False)
    fields = window.findChildren(QLineEdit)
    fields[0].setSelection(1, 3)
    values = [(field.text(), field.echoMode(), field.selectedText(), field.cursorPosition()) for field in fields]
    pages = window.page_widgets
    shells = tuple(window.page_shells)
    workers = worker_references(window)
    models = mask._inspection_result, tuple(mask._column_configs), tuple(window.batch_widget.files)
    selected = window.batch_widget.file_table.selectionModel().selectedRows()[0].row()
    spans = [(shell.scroll_area.horizontalScrollBar().value(), shell.scroll_area.verticalScrollBar().value())
             for shell in window.page_shells]
    window.settings_widget.language_combo.setFocus()
    focus = app.focusWidget()
    locale = QLocale().name()
    navigation = QSignalSpy(window.navigation.current_changed)
    choices = QSignalSpy(window.settings_widget.language_combo.currentIndexChanged)
    notifications = QSignalSpy(window.settings_widget.language_saved)
    original_qt = app.findChildren(QTranslator)
    assert len(original_qt) == 1
    qt = original_qt[0]
    for index, target in enumerate(("en", "pt-BR", "en", "pt-BR"), 1):
        save(window, target)
        assert_applied(window, target)
        assert service.load().preferences.language.value == target
        assert window.page_widgets == pages and tuple(window.page_shells) == shells
        assert [(field.text(), field.echoMode(), field.selectedText(), field.cursorPosition()) for field in fields] == values
        assert worker_references(window) == workers
        assert (mask._inspection_result, tuple(mask._column_configs), tuple(window.batch_widget.files)) == models
        assert window.batch_widget.files[0] is item and item.status is BatchFileStatus.COMPLETED
        assert item.output_path == published and published.read_bytes() == b"NAME\nNAME-SYNTHETIC\n"
        assert window.batch_widget.open_output_button.isEnabled()
        assert window.batch_widget._output_directory == published.parent
        assert table.selectionModel() is selection
        assert [(index.row(), index.column()) for index in selection.selectedIndexes()] == selected_cells
        assert window.batch_widget.file_table.selectionModel().selectedRows()[0].row() == selected
        for shell, (horizontal, vertical) in zip(window.page_shells, spans, strict=True):
            assert shell.scroll_area.horizontalScrollBar().value() == min(horizontal, shell.scroll_area.horizontalScrollBar().maximum())
            assert shell.scroll_area.verticalScrollBar().value() == min(vertical, shell.scroll_area.verticalScrollBar().maximum())
        assert window.current_page_index() == 7 and navigation.count() == 0
        assert app.focusWidget() is focus and QLocale().name() == locale
        assert window.restoration_widget.progress_bar.value() == 57
        assert window.restoration_widget.status_label.text() == "Literal service detail"
        assert app.findChildren(QTranslator) == original_qt
        assert qt._translator is window._translator
        assert QCoreApplication.translate("QPlatformTheme", "&No") == ("&No" if target == "en" else "&Não")
        assert QCoreApplication.translate("QLineEdit", "&Copy") == "&Copy"
        assert choices.count() == notifications.count() == index
        assert not window.settings_widget.save_button.isEnabled()


@pytest.mark.parametrize("languages,expected", [
    (("pt-BR",), "pt-BR"), (("pt-Latn-BR",), "pt-BR"), (("en-US",), "en"),
    (("pt-PT",), "en"), (("fr-FR", "pt-BR"), "en"), (None, "en"),
])
def test_system_save_reuses_existing_resolution_policy(session, monkeypatch, languages, expected):
    _, window, service = session
    calls = []
    monkeypatch.setattr(service, "_ui_languages_provider", lambda: calls.append(True) or languages)
    save(window, "system")
    assert_applied(window, expected)
    assert calls == [True] and service.load().preferences.language.value == "system"


def test_failed_system_detection_uses_english(session, monkeypatch):
    _, window, service = session
    def fail():
        raise OSError("Synthetic OS failure")
    monkeypatch.setattr(service, "_ui_languages_provider", fail)
    save(window, "system")
    assert_applied(window, "en")


OWNERS = (
    ("anonymization_widget", "_worker"), ("anonymization_widget", "_detection_worker"),
    ("batch_widget", "_validation_worker"), ("batch_widget", "_processing_worker"),
    ("restoration_widget", "_worker"), ("html_restoration_widget", "_worker"),
    ("batch_restoration_widget", "_analysis_worker"), ("batch_restoration_widget", "_processing_worker"),
    ("backup_widget", "_worker"), ("maintenance_widget", "_worker"),
)


@pytest.mark.parametrize("page,attribute", OWNERS)
def test_owned_worker_even_not_running_defers_without_cancel_or_later_retry(session, monkeypatch, page, attribute):
    app, window, service = session
    translator = window._translator
    worker = QThread()
    assert not worker.isRunning()
    with monkeypatch.context() as guard:
        guard.setattr(getattr(window, page), attribute, worker)
        guard.setattr(service, "resolve_language", lambda *_: pytest.fail("Unsafe session must not resolve/apply"))
        save(window, "en")
        assert_deferred(window, translator)
        assert getattr(window, page).__dict__[attribute] is worker
    app.processEvents()
    assert_deferred(window, translator)
    assert service.load().preferences.language.value == "en"


class WaitingWorker(QThread):
    def __init__(self):
        super().__init__()
        self.entered, self.release = Event(), Event()

    def run(self):
        self.entered.set()
        self.release.wait(5)


def test_running_worker_is_not_interrupted_and_deferred_save_applies_on_next_bootstrap(session, monkeypatch, tmp_path):
    app, window, service = session
    worker = WaitingWorker()
    worker.start()
    assert worker.entered.wait(2)
    translator = window._translator
    try:
        with monkeypatch.context() as guard:
            guard.setattr(window.anonymization_widget, "_worker", worker)
            guard.setattr(worker, "request_cancel", lambda: pytest.fail("Do not cancel"), raising=False)
            save(window, "en")
            assert worker.isRunning() and not worker.isInterruptionRequested()
            assert_deferred(window, translator)
    finally:
        worker.release.set()
        assert worker.wait(3000)
    app.processEvents()
    assert_deferred(window, translator)
    window.close()
    root = tmp_path / "next-launch"
    root.mkdir()
    windows = []
    def initialize(**kwargs):
        next_window = build_localized_window(root, **kwargs)
        windows.append(next_window)
        return next_window
    monkeypatch.setattr(app_module, "create_application", lambda **_: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", lambda: 0)
    assert app_module.run(preferences_service=service) == 0
    assert windows[0]._translator.locale == "en"


def test_open_destructive_confirmation_is_unchanged_and_not_answered_by_save(session):
    app, window, service = session
    translator = window._translator
    box = QMessageBox(QMessageBox.Icon.Warning, "Aviso sintético", "Não iniciar operação.",
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, window)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    box.setEscapeButton(QMessageBox.StandardButton.No)
    original_buttons = box.buttons()
    def respond(dialog):
        assert dialog is box and app.activeModalWidget() is box
        focus = app.focusWidget()
        save(window, "en")
        assert_deferred(window, translator)
        assert box.windowTitle() == "Aviso sintético" and box.text() == "Não iniciar operação."
        assert box.buttons() == original_buttons and app.focusWidget() is focus
        assert box.defaultButton() is box.escapeButton() is box.button(QMessageBox.StandardButton.No)
        assert box.isVisible()
        QTest.keyClick(box, Qt.Key.Key_Escape)
    assert dialog_result(app, box.exec, respond) == QMessageBox.StandardButton.No
    box.deleteLater()
    QCoreApplication.sendPostedEvents(box, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert_deferred(window, translator)
    assert service.load().preferences.language.value == "en"


def test_native_file_dialog_wrapper_blocks_even_without_widget_visibility(session, tmp_path):
    app, window, _ = session
    translator = window._translator
    # Qt's native helper belongs to this QFileDialog object as well. The gate
    # must not depend on offscreen/native visibility or activeModalWidget().
    dialog = QFileDialog(window, "Synthetic file selection", str(tmp_path))
    assert dialog in app.topLevelWidgets() and not dialog.isVisible()
    assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
    try:
        save(window, "en")
        assert_deferred(window, translator)
        assert dialog.result() == 0 and not dialog.isVisible()
    finally:
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    assert_deferred(window, translator)
    save(window, "pt-BR")
    assert_applied(window, "pt-BR")


def test_modal_qwindow_and_open_about_defer_but_closed_cached_about_allows_switch(session):
    app, window, _ = session
    translator = window._translator
    modal = QWindow()
    modal.setModality(Qt.WindowModality.ApplicationModal)
    modal.show()
    try:
        assert app.modalWindow() is modal
        save(window, "en")
        assert_deferred(window, translator)
    finally:
        modal.close()
        modal.destroy()
    window.show_about_dialog()
    about = window.about_dialog
    save(window, "en")
    assert_deferred(window, translator)
    about.close()
    window.settings_widget.language_combo.setCurrentIndex(0)
    save(window, "en")
    assert_applied(window, "en")
    assert window.about_dialog is about and not about.isVisible()


def test_failed_write_and_invalid_storage_never_apply_or_overwrite(session, monkeypatch):
    _, window, service = session
    translator = window._translator
    with monkeypatch.context() as guard:
        guard.setattr(service, "save_language", lambda *_: PreferencesResult(error=PreferencesError.WRITE_FAILED))
        guard.setattr(window, "retranslate", lambda *_: pytest.fail("Failed save must not apply"))
        save(window, "en")
    assert window._translator is translator
    assert window.settings_widget.status_label.text() == translator.text("settings.error.write_failed")
    service.path.write_bytes(b"INVALID_SYNTHETIC_PREFERENCES")
    window.settings_widget.load_preferences()
    assert not window.settings_widget.save_button.isEnabled()
    window.settings_widget.save_preference()
    assert service.path.read_bytes() == b"INVALID_SYNTHETIC_PREFERENCES"
    assert window._translator is translator


def test_authoritative_injected_startup_translator_remains_next_launch_only(app, tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    translator = Translator("pt-BR")
    windows = []
    def initialize(**kwargs):
        assert kwargs["translator"] is translator and not kwargs["live_language_switching"]
        window = build_localized_window(tmp_path, **kwargs)
        windows.append(window)
        return window
    def interact():
        window = windows[0]
        window.set_current_page(window.page_index(window.settings_widget))
        app.processEvents()
        save(window, "en")
        assert_deferred(window, translator)
        assert window.settings_widget.notice_label.text() == translator.text("settings.notice")
        return 0
    monkeypatch.setattr(app_module, "create_application", lambda **_: app)
    monkeypatch.setattr(app_module, "MainWindow", initialize)
    monkeypatch.setattr(app, "exec", interact)
    assert app_module.run(translator=translator, preferences_service=service) == 0
    assert service.load().preferences.language.value == "en"


def test_live_render_has_no_io_or_operations_beyond_preference_save_and_catalog_acquisition(session, monkeypatch):
    _, window, service = session
    # Catalog construction and preference persistence are separate from rendering.
    english = Translator("en")
    monkeypatch.setattr("data_mask_studio.gui.main_window.Translator", lambda locale: english)
    real_save = service.save_language
    def forbidden(*args, **kwargs):
        pytest.fail("Presentation must not read/write files, query SQLite, or start processing")
    with monkeypatch.context() as guard:
        def save_then_guard(language):
            result = real_save(language)
            for name in ("open", "read_bytes", "read_text", "write_bytes", "write_text", "stat", "resolve", "exists", "mkdir"):
                guard.setattr(Path, name, forbidden)
            guard.setattr(sqlite3, "connect", forbidden)
            guard.setattr(QThread, "start", forbidden)
            guard.setattr(window.anonymization_widget, "validate_current_configuration", forbidden)
            guard.setattr(window.batch_widget, "refresh_profiles", forbidden)
            return result
        guard.setattr(service, "save_language", save_then_guard)
        save(window, "en")
        assert_applied(window, "en")


def test_popup_or_nonsettings_page_is_not_a_safe_application_boundary(session):
    app, window, _ = session
    translator = window._translator
    window.settings_widget.language_combo.showPopup()
    try:
        assert app.activePopupWidget() is not None
        save(window, "en")
        assert_deferred(window, translator)
    finally:
        window.settings_widget.language_combo.hidePopup()
    window.set_current_page(0)
    window.settings_widget.language_combo.setCurrentIndex(0)
    window.settings_widget.save_preference()
    assert_deferred(window, translator)
    window.set_current_page(7)
    app.processEvents()
    # Returning to Settings never automatically applies a previously saved value.
    assert window._translator is translator
    save(window, "en")
    assert_applied(window, "en")


def test_storage_invalidated_since_display_preserves_bytes_and_active_locale(session):
    _, window, service = session
    translator = window._translator
    raw = b"SYNTHETIC_INVALID_PREFERENCES"
    service.path.write_bytes(raw)
    save(window, "en")
    assert service.path.read_bytes() == raw
    assert window._translator is translator
    assert window.settings_widget.status_label.text() == translator.text("settings.error.invalid_file")
    assert not window.settings_widget.save_button.isEnabled()


def test_catalogs_remain_strictly_paired():
    catalogs = load_catalogs()
    validate_catalogs(catalogs)
    assert len(catalogs["en"]) == len(catalogs["pt-BR"]) == 836
    for locale in catalogs:
        assert "settings.notice.live" in catalogs[locale] and "settings.saved.applied" in catalogs[locale]
