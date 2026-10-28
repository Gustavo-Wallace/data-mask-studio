"""Temporary-file presentation must not change ownership or deletion authority."""

import os
import time
from pathlib import Path
from threading import Event, Thread

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QHeaderView, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.maintenance_worker import TemporaryCleanupWorker, TemporaryScanWorker
from data_mask_studio.i18n import Translator, load_catalogs
from data_mask_studio.maintenance import CleanupResult, MaintenanceError, TemporaryItem
from data_mask_studio.gui import maintenance_worker as worker_module
from data_mask_studio.maintenance.temporary_cleanup import locate_temporaries
from data_mask_studio.preferences import PreferencesService
from test_dialog_consistency import modal_reply
from test_navigation_localization import build_localized_window
from test_maintenance_interface import prepare, widget_for, wait


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.fixture
def page(tmp_path):
    app = create_application([])
    paths, _ = prepare(tmp_path)
    widget = widget_for(paths)
    widget.sections.setCurrentIndex(2)
    widget.resize(1100, 700)
    widget.show()
    app.processEvents()
    return app, widget, paths


def scan(app, widget):
    widget.start_temporary_scan()
    wait(app, widget)
    assert widget._worker is None


def old_file(path):
    path.write_bytes(b"synthetic disposable artifact")
    os.utime(path, (0, 0))
    return path


def test_english_temporary_controls_are_explicitly_localized(page):
    _, widget, _ = page
    widget.retranslate(Translator("en"))
    assert widget.locate_button.text() == "Find temporary files"
    assert widget.cleanup_button.text() == "Delete selected"
    assert widget.temporary_table.empty_text == "No temporary files found."


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_temporary_terminal_failure_or_cancellation_is_not_success(page, monkeypatch, terminal):
    _, widget, _ = page
    monkeypatch.setattr(TemporaryScanWorker, "start", lambda self: None)
    widget.start_temporary_scan()
    worker = widget._worker
    if terminal == "failed":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    else:
        widget.cancel()
        worker.cancelled.emit()
    worker.finished.emit()
    assert widget._worker is None
    assert widget.progress.maximum() == 1 and widget.progress.value() == 0
    assert widget.status_label.property("feedbackState") == ("error" if terminal == "failed" else "warning")


def test_retired_scan_cannot_overwrite_current_worker_feedback(page, monkeypatch):
    _, widget, paths = page
    monkeypatch.setattr(TemporaryScanWorker, "start", lambda self: None)
    widget.start_temporary_scan()
    old = widget._worker
    old.completed.emit([])
    old.finished.emit()
    widget.start_temporary_scan()
    current = widget._worker
    before = widget.status_label.text()
    old.completed.emit([TemporaryItem(paths.directory / ".dms-write-abcdefgh", 1, 7200, False, False)])
    assert widget._worker is current and widget.temporary_items == []
    assert widget.status_label.text() == before
    current.completed.emit([])
    current.finished.emit()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_initial_labels_accessibility_and_strict_catalog_parity(page, locale):
    _, widget, _ = page
    translator = Translator(locale)
    widget.retranslate(translator)
    for control, identifier in ((widget.locate_button, "maintenance.temporary.locate"),
            (widget.cleanup_button, "maintenance.temporary.delete"),
            (widget.temporary_info, "maintenance.temporary.instructions")):
        assert control.text() == control.accessibleName() == translator.text(identifier)
    assert widget.temporary_table.accessibleName() == translator.text("maintenance.temporary.table_accessible")
    assert widget.temporary_table.accessibleDescription() == translator.text("maintenance.temporary.instructions")
    assert widget.temporary_table.empty_text == translator.text("maintenance.temporary.empty")
    assert [widget.temporary_table.horizontalHeaderItem(column).text() for column in range(5)] == [
        translator.text(identifier) for identifier in ("masking.composite.delete", "batch.table.path",
            "maintenance.temporary.size", "maintenance.temporary.age", "batch.table.status")]
    assert not widget.cleanup_button.isEnabled() and widget.temporary_table.rowCount() == 0
    assert widget._worker is None and widget.sections.currentIndex() == 2
    assert not widget.progress.isVisible() and not widget.status_label.isVisible()
    catalogs = load_catalogs()
    assert set(catalogs["en"]) == set(catalogs["pt-BR"])
    assert QCoreApplication.translate("QPlatformTheme", "&Yes") == "&Sim"


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_scan_keeps_ownership_eligibility_and_literal_paths(page, locale, monkeypatch):
    app, widget, paths = page
    owned = old_file(paths.directory / ".dms-write-abcdefgh")
    foreign = old_file(paths.directory / "other-app.tmp")
    almost = old_file(paths.directory / ".dms-write-abcdefg")
    recent = paths.directory / ".dms-access-ab12_cd3"
    recent.write_bytes(b"recent synthetic file")
    before = {path: path.read_bytes() for path in (owned, foreign, almost, recent, paths.vault_database_path)}
    now = time.time()
    expected = locate_temporaries(paths.directory, now=now)
    monkeypatch.setattr(worker_module, "locate_temporaries",
        lambda directory, sessions: locate_temporaries(directory, sessions, now=now))
    widget.retranslate(Translator(locale))
    scan(app, widget)
    assert widget.temporary_items == expected
    assert {item.path for item in widget.temporary_items} == {owned, recent}
    assert widget.status_label.text() == Translator(locale).text("maintenance.temporary.status.found", count=2)
    assert widget.status_label.property("feedbackState") == "success" and widget.progress.value() == 1
    for row, item in enumerate(widget.temporary_items):
        assert widget.temporary_table.item(row, 1).text() == str(item.path)
        assert widget.temporary_table.item(row, 1).toolTip() == str(item.path)
        assert widget.temporary_table.cellWidget(row, 0).isEnabled() is item.removable
        assert not widget.temporary_table.cellWidget(row, 0).isChecked()
        identifier = "maintenance.temporary.recent" if item.recent else "maintenance.temporary.available"
        assert widget.temporary_table.item(row, 4).text() == Translator(locale).text(identifier)
    assert not widget.cleanup_button.isEnabled()
    assert {path: path.read_bytes() for path in before} == before


def test_retranslate_reuses_rows_results_selections_focus_scroll_and_does_no_io(page, monkeypatch):
    app, widget, paths = page
    widget.temporary_items = [TemporaryItem(paths.directory / f"literal-ação-#-{number}.tmp", 2048, 7200,
        number % 4 == 0, number % 4 == 1, selected=number % 4 == 2,
        result="Resultado livre & <b>literal</b> {count}" if number == 3 else "") for number in range(60)]
    widget._refresh_temporary_table()
    widget._set_temporary_status_id("maintenance.temporary.status.found", state="warning", count=60)
    widget.progress.setRange(0, 8)
    widget.progress.setValue(3)
    table = widget.temporary_table
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    table.setColumnWidth(1, 1200)
    app.processEvents()
    table.selectRow(22)
    checkbox = table.cellWidget(22, 0)
    checkbox.setFocus()
    table.verticalScrollBar().setValue(18)
    table.horizontalScrollBar().setValue(30)
    app.processEvents()
    identities = (widget.temporary_items, tuple(widget.temporary_items),
        tuple(table.cellWidget(row, 0) for row in range(60)),
        tuple(table.item(row, column) for row in range(60) for column in range(1, 5)))
    model = [tuple(getattr(item, name) for name in ("path", "size", "age_seconds", "recent", "in_use", "selected", "result"))
             for item in widget.temporary_items]
    selected = [(index.row(), index.column()) for index in table.selectedIndexes()]
    scroll = table.verticalScrollBar().value(), table.horizontalScrollBar().value()
    assert all(value > 0 for value in scroll), (scroll, table.verticalScrollBar().maximum(), table.horizontalScrollBar().maximum())
    focus = app.focusWidget()
    assert focus is checkbox
    signals = [QSignalSpy(table.itemChanged), QSignalSpy(table.itemSelectionChanged),
               QSignalSpy(widget.busy_changed), QSignalSpy(widget.sections.currentChanged)]
    checkbox_signals = [QSignalSpy(box.toggled) for box in identities[2]]
    enabled = widget.cleanup_button.isEnabled()
    clipboard = app.clipboard().text()
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not scan, touch files, start a worker or confirm deletion")

    with monkeypatch.context() as isolated:
        for name in ("start_temporary_scan", "start_cleanup", "_refresh_temporary_table"):
            isolated.setattr(widget, name, forbidden)
        for name in ("locate_temporaries", "cleanup_temporaries"):
            isolated.setattr(worker_module, name, forbidden)
        isolated.setattr("data_mask_studio.gui.maintenance_widget.confirm_destructive_action", forbidden)
        for name in ("open", "stat", "iterdir", "unlink", "rglob"):
            isolated.setattr(Path, name, forbidden)
        for translator in translators:
            widget.retranslate(translator)
            app.processEvents()
            assert widget.temporary_items is identities[0] and tuple(widget.temporary_items) == identities[1]
            assert tuple(table.cellWidget(row, 0) for row in range(60)) == identities[2]
            assert tuple(table.item(row, column) for row in range(60) for column in range(1, 5)) == identities[3]
            assert [(index.row(), index.column()) for index in table.selectedIndexes()] == selected
            assert (table.verticalScrollBar().value(), table.horizontalScrollBar().value()) == scroll
            assert app.focusWidget() is focus and widget.sections.currentIndex() == 2
            assert widget.cleanup_button.isEnabled() is enabled
            assert widget.progress.maximum() == 8 and widget.progress.value() == 3
            assert widget.status_label.property("feedbackState") == "warning"
            assert widget.status_label.text() == translator.text("maintenance.temporary.status.found", count=60)
            assert table.item(3, 4).text() == widget.temporary_items[3].result
    assert all(spy.count() == 0 for spy in signals + checkbox_signals)
    assert model == [tuple(getattr(item, name) for name in ("path", "size", "age_seconds", "recent", "in_use", "selected", "result"))
                     for item in widget.temporary_items]
    assert app.clipboard().text() == clipboard


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("action", ["yes", "no", "escape", "enter", "close"])
def test_real_confirmation_preserves_semantic_buttons_and_cleanup_authority(page, locale, action):
    app, widget, paths = page
    owned = old_file(paths.directory / ".dms-write-abcdefgh")
    foreign = old_file(paths.directory / "other-app.tmp")
    original = paths.vault_database_path.read_bytes()
    widget.retranslate(Translator(locale))
    scan(app, widget)
    widget.temporary_table.cellWidget(0, 0).setChecked(True)
    assert widget.cleanup_button.isEnabled()
    seen = []

    def reply():
        box = app.activeModalWidget()
        seen.append(box)
        assert isinstance(box, QMessageBox) and box.textFormat() == Qt.TextFormat.PlainText
        assert box.windowTitle() == Translator(locale).text("maintenance.temporary.confirm.title")
        assert box.text() == Translator(locale).text("maintenance.temporary.confirm.text", count=1)
        assert box.standardButtons() == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        assert box.standardButton(box.defaultButton()) == box.standardButton(box.escapeButton()) == QMessageBox.StandardButton.No
        yes, no = box.button(QMessageBox.StandardButton.Yes), box.button(QMessageBox.StandardButton.No)
        assert box.buttonRole(yes) == QMessageBox.ButtonRole.YesRole
        assert box.buttonRole(no) == QMessageBox.ButtonRole.NoRole
        assert yes.text() == Translator(locale).text("dialog.button.yes")
        assert no.text() == Translator(locale).text("dialog.button.no")
        assert yes.property("role") == "destructive"
        assert owned.exists() and widget._worker is None
        if action in ("yes", "no"):
            (yes if action == "yes" else no).click()
        elif action == "close":
            box.close()
        else:
            QTest.keyClick(box, Qt.Key.Key_Escape if action == "escape" else Qt.Key.Key_Return)

    with modal_reply(widget, reply):
        widget.start_cleanup()
    assert len(seen) == 1
    if action == "yes":
        wait(app, widget)
        assert not owned.exists()
        assert widget.status_label.text() == Translator(locale).text("maintenance.temporary.status.cleaned",
            removed=1, preserved=0, failed=0)
        assert widget.status_label.property("feedbackState") == "success" and widget.progress.value() == 1
        assert widget.temporary_items[0].result == widget.temporary_table.item(0, 4).text() == "Removido"
        assert not widget.temporary_table.cellWidget(0, 0).isEnabled()
    else:
        assert widget._worker is None and owned.exists()
        assert widget.progress.value() == 1  # Completed scan, not a claimed cleanup.
        assert widget.status_label.text() == Translator(locale).text("maintenance.temporary.status.found", count=1)
    assert foreign.read_bytes() == b"synthetic disposable artifact"
    assert paths.vault_database_path.read_bytes() == original


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_cleanup_does_nothing_without_eligible_selection_or_while_busy(page, locale, monkeypatch):
    _, widget, paths = page
    widget.retranslate(Translator(locale))
    recent = old_file(paths.directory / ".dms-write-abcdefgh")
    widget.temporary_items = [TemporaryItem(recent, 1, 30, True, False, selected=True)]
    widget._refresh_temporary_table()
    monkeypatch.setattr("data_mask_studio.gui.maintenance_widget.confirm_destructive_action",
                        lambda *args, **kwargs: pytest.fail("No eligible selection"))
    widget.start_cleanup()
    assert widget._worker is None and recent.exists()
    widget.temporary_items[0].recent = False
    monkeypatch.setattr(widget, "_prepare_operation", lambda: False)
    widget.start_cleanup()
    assert widget._worker is None and recent.exists()
    assert widget.status_label.text() == Translator(locale).text("maintenance.overview.error.busy")
    assert widget.status_label.property("feedbackState") == "error"


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("fault", ["unsafe", "recent", "locked", "permission", "missing"])
def test_real_cleanup_revalidates_targets_and_retains_freeform_outcomes(page, monkeypatch, locale, fault, tmp_path):
    app, widget, paths = page
    widget.retranslate(Translator(locale))
    good = old_file(paths.directory / ".dms-write-abcdefgh")
    target = old_file((tmp_path if fault == "unsafe" else paths.directory) / ".dms-access-ab12_cd3")
    widget.temporary_items = [TemporaryItem(path, 29, 7200, False, False, selected=True) for path in (good, target)]
    if fault == "recent":
        target.touch()
    elif fault == "missing":
        target.unlink()
    elif fault == "locked":
        from data_mask_studio.maintenance import temporary_cleanup
        monkeypatch.setattr(temporary_cleanup.os, "access", lambda path, mode: path != target)
    elif fault == "permission":
        unlink = Path.unlink
        def deny_target(path, *args, **kwargs):
            if path == target:
                raise PermissionError("Synthetic denied file")
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", deny_target)
    widget._refresh_temporary_table()
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.start_cleanup()
    wait(app, widget)
    assert not good.exists()
    assert target.exists() is (fault != "missing")
    failed = int(fault == "permission")
    assert widget.status_label.text() == Translator(locale).text("maintenance.temporary.status.cleaned",
        removed=1, preserved=1 - failed, failed=failed)
    assert widget.status_label.property("feedbackState") == ("error" if failed else "warning")
    assert widget.progress.value() == (0 if failed else 1)
    raw = widget.temporary_items[1].result
    assert raw and widget.temporary_table.item(1, 4).text() == raw
    widget.retranslate(Translator("pt-BR" if locale == "en" else "en"))
    assert widget.temporary_table.item(1, 4).text() == raw


@pytest.mark.parametrize("worker_type", [TemporaryScanWorker, TemporaryCleanupWorker])
@pytest.mark.parametrize("terminal", ["failure", "cancelled"])
def test_terminal_signal_rejects_late_success_and_never_inherits_previous_success(page, monkeypatch, worker_type, terminal):
    app, widget, paths = page
    old_file(paths.directory / ".dms-write-abcdefgh")
    scan(app, widget)
    assert widget.progress.value() == 1
    widget.temporary_items[0].selected = True
    monkeypatch.setattr(worker_type, "start", lambda self: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    (widget.start_temporary_scan if worker_type is TemporaryScanWorker else widget.start_cleanup)()
    worker = widget._worker
    assert widget.progress.maximum() == 0 and not widget.sections.isEnabled()
    if terminal == "failure":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    else:
        widget.cancel()
        assert worker._cancellation.is_set()
        worker.cancelled.emit()
    before = widget.status_label.text()
    worker.completed.emit([] if worker_type is TemporaryScanWorker else CleanupResult(1, 0, 0, 1))
    assert widget.status_label.text() == before and "PRIVATE_SENTINEL" not in before
    worker.finished.emit()
    assert widget.progress.value() == 0 and widget.sections.isEnabled() and widget._worker is None
    state = widget.status_label.property("feedbackState")
    widget.retranslate(Translator("en"))
    assert widget.status_label.property("feedbackState") == state
    assert widget.progress.value() == 0


@pytest.mark.parametrize("worker_type", [TemporaryScanWorker, TemporaryCleanupWorker])
def test_real_active_worker_survives_retranslation_without_restarting_or_losing_cancellation(page, monkeypatch, worker_type):
    app, widget, paths = page
    owned = old_file(paths.directory / ".dms-write-abcdefgh")
    scan(app, widget)
    widget.temporary_table.cellWidget(0, 0).setChecked(True)
    function_name = "locate_temporaries" if worker_type is TemporaryScanWorker else "cleanup_temporaries"
    original = getattr(worker_module, function_name)
    entered, release = Event(), Event()
    calls = []

    def paused(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(5), "Synthetic worker was not released"
        return original(*args, **kwargs)

    monkeypatch.setattr(worker_module, function_name, paused)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    (widget.start_temporary_scan if worker_type is TemporaryScanWorker else widget.start_cleanup)()
    worker = widget._worker
    try:
        assert entered.wait(5) and worker.isRunning()
        # Existing services do not cooperatively interrupt these operations.
        # Preserve the request; do not invent cancellation or suppress real outcomes.
        widget.cancel()
        for locale in ("en", "pt-BR", "en"):
            widget.retranslate(Translator(locale))
            assert widget._worker is worker and worker.isRunning() and worker._cancellation.is_set()
            assert not widget.cancel_button.isEnabled() and not widget.sections.isEnabled()
            assert widget.progress.maximum() == 0
            assert widget.status_label.text() == Translator(locale).text("masking.status.cancelling")
        assert owned.exists() and calls == [True]
    finally:
        release.set()
        assert worker.wait(10000)
        app.processEvents()
    assert widget._worker is None and calls == [True]
    assert owned.exists() is (worker_type is TemporaryScanWorker)
    assert widget.status_label.property("feedbackState") == "success"


def test_delayed_queued_cleanup_signals_cannot_mutate_new_scan(page, monkeypatch):
    app, widget, paths = page
    old_file(paths.directory / ".dms-write-abcdefgh")
    scan(app, widget)
    widget.temporary_items[0].selected = True
    monkeypatch.setattr(TemporaryCleanupWorker, "start", lambda self: None)
    monkeypatch.setattr(TemporaryScanWorker, "start", lambda self: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.start_cleanup()
    old = widget._worker
    old.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    old.finished.emit()
    widget.start_temporary_scan()
    current = widget._worker
    before = widget.status_label.text()

    def late():
        old.completed.emit(CleanupResult(1, 0, 0, 1))
        old.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
        old.cancelled.emit()
        old.finished.emit()

    thread = Thread(target=late)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    app.processEvents()
    assert widget._worker is current and widget.progress.maximum() == 0
    assert widget.status_label.text() == before
    current.completed.emit([])
    current.finished.emit()


def test_literal_safe_service_errors_are_not_heuristically_translated(page, monkeypatch):
    _, widget, _ = page
    monkeypatch.setattr(TemporaryScanWorker, "start", lambda self: None)
    widget.start_temporary_scan()
    worker = widget._worker
    error = MaintenanceError("Mensagem segura livre & <literal> maintenance.temporary.status.found {count}")
    worker.failed.emit(error)
    worker.finished.emit()
    for locale in ("en", "pt-BR"):
        widget.retranslate(Translator(locale))
        assert widget.status_label.text() == str(error)
        assert widget.status_label.textFormat() == Qt.TextFormat.PlainText
        assert widget.status_label.property("feedbackState") == "error" and widget.progress.value() == 0


def test_hidden_temporary_feedback_retranslates_without_overwriting_other_tabs(page):
    app, widget, _ = page
    scan(app, widget)
    for tab in (0, 1, 3):
        widget.sections.setCurrentIndex(tab)
        widget._set_status("Literal feedback from another tab", False, state="warning")
        widget.progress.setRange(0, 7)
        widget.progress.setValue(4)
        widget._remember_feedback()
        original = widget._section_feedback[tab]
        widget.retranslate(Translator("en"))
        assert widget.sections.currentIndex() == tab and widget._section_feedback[tab] == original
        assert widget.compact_button.text() == Translator("en").text("maintenance.compaction.start")
        assert widget.compaction_result.empty_text == Translator("en").text("maintenance.compaction.empty")
        assert widget.progress.value() == 4 and widget.status_label.text() == original.message
        widget.sections.setCurrentIndex(2)
        assert widget.status_label.text() == Translator("en").text("maintenance.temporary.status.found", count=0)
        assert widget.progress.value() == 1 and widget.status_label.property("feedbackState") == "success"


def test_main_window_startup_ignores_saved_english_but_explicit_translation_relabels_same_tab(tmp_path):
    create_application([])
    preferences = PreferencesService(tmp_path / "preferences.json",
        ui_languages_provider=lambda: pytest.fail("No global language activation"))
    assert preferences.save_language("en").error is None
    original = preferences.path.read_bytes()
    window = build_localized_window(tmp_path, preferences_service=preferences)
    widget = window.maintenance_widget
    assert widget.locate_button.text() == "Localizar temporários"
    tab = widget.sections.widget(2)
    window.retranslate(Translator("en"))
    assert widget.sections.widget(2) is tab and widget.locate_button.text() == "Find temporary files"
    assert preferences.path.read_bytes() == original and widget._worker is None
