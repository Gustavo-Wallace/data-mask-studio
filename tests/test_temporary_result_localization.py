"""Per-file cleanup presentation is independent of deletion authority."""

from dataclasses import fields, replace
import os
from pathlib import Path
import pickle
from threading import Thread

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QHeaderView, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.i18n import Translator
from data_mask_studio.maintenance import CleanupResult, TemporaryItem, TemporaryResultReason
from data_mask_studio.maintenance import temporary_cleanup as cleanup_module
from data_mask_studio.gui import maintenance_worker as worker_module
from data_mask_studio.gui.maintenance_worker import TemporaryCleanupWorker, TemporaryScanWorker
from test_maintenance_interface import wait
from test_maintenance_temporary_localization import page, isolated_appdata, old_file, scan


@pytest.mark.parametrize("outcome,expected", [
    ("removed", "Removed"),
    ("preserved", "Preserved"),
    ("failed", "Failed to remove"),
])
def test_real_cleanup_result_renders_in_english_without_changing_legacy_result(
    page, monkeypatch, outcome, expected,
):
    app, widget, paths = page
    target = old_file(paths.directory / ".dms-write-abcdefgh")
    scan(app, widget)
    if outcome == "preserved":
        # Another selected item authorizes the operation; target remains unselected.
        selected = old_file(paths.directory / ".dms-access-ab12_cd3")
        scan(app, widget)
        next(item for item in widget.temporary_items if item.path == selected).selected = True
    else:
        widget.temporary_items[0].selected = True
    if outcome == "failed":
        unlink = type(target).unlink

        def denied(path, *args, **kwargs):
            if path == target:
                raise PermissionError("Synthetic denial; not a display message")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(type(target), "unlink", denied)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.start_cleanup()
    wait(app, widget)
    row, item = next((row, item) for row, item in enumerate(widget.temporary_items) if item.path == target)
    raw = {"removed": "Removido", "preserved": "Preservado", "failed": "Falha ao remover"}[outcome]
    assert item.result == raw
    widget.retranslate(Translator("en"))
    assert widget.temporary_table.item(row, 4).text() == expected
    assert item.result == raw
    assert target.exists() is (outcome != "removed")


OUTCOMES = [
    ("unselected", TemporaryResultReason.PRESERVED, "Preservado", "Preserved"),
    ("not_removable", TemporaryResultReason.PRESERVED, "Preservado", "Preserved"),
    ("unsafe", TemporaryResultReason.PRESERVED_FOR_SAFETY, "Preservado por segurança", "Preserved for safety"),
    ("foreign", TemporaryResultReason.PRESERVED_FOR_SAFETY, "Preservado por segurança", "Preserved for safety"),
    ("missing", TemporaryResultReason.ALREADY_MISSING, "Já não existe", "Already missing"),
    ("recent", TemporaryResultReason.RECENT, "Preservado por ser recente", "Preserved because it is recent"),
    ("locked", TemporaryResultReason.IN_USE, "Possivelmente em uso", "Possibly in use"),
    ("file", TemporaryResultReason.REMOVED, "Removido", "Removed"),
    ("directory", TemporaryResultReason.REMOVED, "Removido", "Removed"),
    ("failure", TemporaryResultReason.REMOVAL_FAILED, "Falha ao remover", "Failed to remove"),
]


@pytest.mark.parametrize("case,reason,raw,english", OUTCOMES, ids=[row[0] for row in OUTCOMES])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_cleanup_branches_emit_explicit_reasons_but_preserve_legacy_contract(
    page, tmp_path, monkeypatch, case, reason, raw, english, locale,
):
    _, widget, paths = page
    target = (tmp_path if case == "unsafe" else paths.directory) / (
        ".dms-backup-ab12_cd3" if case == "directory" else
        "other-app.tmp" if case == "foreign" else ".dms-write-abcdefgh")
    if case == "directory":
        target.mkdir()
        (target / "synthetic.txt").write_bytes(b"synthetic")
        os.utime(target, (0, 0))
    else:
        old_file(target)
    item = TemporaryItem(target, 29, 7200, case == "not_removable", False, case != "unselected")
    if case == "missing":
        exists = Path.exists

        def disappears_after_ownership(path, *args, **kwargs):
            if path == target:
                path.unlink()
                return False
            return exists(path, *args, **kwargs)

        monkeypatch.setattr(Path, "exists", disappears_after_ownership)
    elif case == "recent":
        target.touch()
    elif case == "locked":
        monkeypatch.setattr(cleanup_module, "_probably_in_use", lambda path: True)
    elif case == "failure":
        unlink = Path.unlink

        def deny(path, *args, **kwargs):
            if path == target:
                raise PermissionError("Synthetic error; do not expose it")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", deny)
    widget.retranslate(Translator(locale))
    result = cleanup_module.cleanup_temporaries([item], paths.directory)
    if case == "missing":
        monkeypatch.setattr(Path, "exists", exists)
    assert item.result == raw and item.result_reason is reason
    removed, failed = int(reason is TemporaryResultReason.REMOVED), int(reason is TemporaryResultReason.REMOVAL_FAILED)
    assert result == CleanupResult(removed, 1 - removed - failed, failed, 29 * removed)
    assert target.exists() is (case not in ("missing", "file", "directory"))
    assert widget._temporary_status(item) == (english if locale == "en" else raw)
    assert item.recent is (case in ("recent", "not_removable"))
    assert item.in_use is (case == "locked")
    assert (item.path, item.size, item.age_seconds, item.selected) == (target, 29, 7200, case != "unselected")


def test_optional_reason_preserves_positional_construction_equality_and_pickle(tmp_path):
    legacy = TemporaryItem(tmp_path / "file", 7, 7200, False, False, True, "Removido")
    typed = replace(legacy, result_reason=TemporaryResultReason.REMOVED)
    assert legacy.result_reason is None and legacy == typed
    assert pickle.loads(pickle.dumps(typed)) == legacy
    assert pickle.loads(pickle.dumps(typed)).result_reason is TemporaryResultReason.REMOVED
    assert fields(TemporaryItem)[-1].name == "result_reason"
    assert fields(TemporaryItem)[-1].compare is False
    assert tuple(reason.value for reason in TemporaryResultReason) == (
        "preserved", "preserved_for_safety", "already_missing", "recent", "in_use", "removed", "removal_failed")


@pytest.mark.parametrize("reason", [None, "removed", "future_reason", 123])
@pytest.mark.parametrize("raw", ["Removido", "Removed", "Mensagem segura & <b>literal</b> {count}"])
def test_unknown_metadata_and_known_looking_text_stay_literal(page, reason, raw):
    _, widget, paths = page
    item = TemporaryItem(paths.directory / "synthetic", 1, 2, False, False, result=raw, result_reason=reason)
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert widget._temporary_status(item) == raw


def test_reason_is_authoritative_and_never_inferred_from_result_text(page):
    _, widget, paths = page
    # Legacy text is deliberately unrelated: no string recognition is allowed.
    item = TemporaryItem(paths.directory / "synthetic", 1, 2, False, False,
                         result="unclassified-looking text", result_reason=TemporaryResultReason.REMOVED)
    widget.retranslate(Translator("en"))
    assert widget._temporary_status(item) == "Removed"
    assert item.result == "unclassified-looking text"
    item.result = ""
    assert widget._temporary_status(item) == "Available for selection"


@pytest.mark.parametrize("stage", ["discovered", "removed", "partial", "failed"])
def test_retranslation_preserves_existing_rows_state_and_never_touches_filesystem(page, monkeypatch, stage):
    app, widget, paths = page
    reason = {"discovered": None, "removed": TemporaryResultReason.REMOVED,
              "partial": TemporaryResultReason.PRESERVED, "failed": TemporaryResultReason.REMOVAL_FAILED}[stage]
    raw = {"discovered": "", "removed": "Removido", "partial": "Preservado", "failed": "Falha ao remover"}[stage]
    widget.temporary_items = [TemporaryItem(paths.directory / f"synthetic-ação-#-{row}", row + 1,
        7200, row % 3 == 0, False, row % 3 == 1, raw, reason) for row in range(60)]
    widget._refresh_temporary_table()
    table = widget.temporary_table
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    table.setColumnWidth(1, 1200)
    app.processEvents()
    table.selectRow(22)
    checkbox = table.cellWidget(22, 0)
    focus_target = table if stage == "removed" else checkbox
    focus_target.setFocus()
    table.verticalScrollBar().setValue(18)
    table.horizontalScrollBar().setValue(30)
    widget._set_temporary_status_id("maintenance.temporary.status.cleaned", state="warning",
                                  removed=4, preserved=2, failed=0)
    widget.progress.setRange(0, 8)
    widget.progress.setValue(3)
    app.processEvents()
    original_items = widget.temporary_items
    values = [tuple(getattr(item, field.name) for field in fields(item)) for item in original_items]
    checkboxes = [table.cellWidget(row, 0) for row in range(60)]
    cells = [table.item(row, col) for row in range(60) for col in range(1, 5)]
    enabled = [box.isEnabled() for box in checkboxes]
    selected = [(index.row(), index.column()) for index in table.selectedIndexes()]
    scroll = table.verticalScrollBar().value(), table.horizontalScrollBar().value()
    focus, worker = app.focusWidget(), widget._worker
    signals = [QSignalSpy(table.itemChanged), QSignalSpy(table.itemSelectionChanged),
               *(QSignalSpy(box.toggled) for box in checkboxes)]
    assert focus is focus_target and all(value > 0 for value in scroll)
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en", "pt-BR")]

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must be presentation-only")

    # Catalogs are already loaded; block filesystem and all worker/service entry points.
    with monkeypatch.context() as guard:
        for name in ("stat", "exists", "is_file", "is_dir", "iterdir", "open", "unlink", "resolve"):
            guard.setattr(Path, name, forbidden)
        guard.setattr(worker_module, "locate_temporaries", forbidden)
        guard.setattr(worker_module, "cleanup_temporaries", forbidden)
        guard.setattr(cleanup_module, "_controlled_roots", forbidden)
        guard.setattr(TemporaryCleanupWorker, "start", forbidden)
        guard.setattr(TemporaryScanWorker, "start", forbidden)
        for translator in translators:
            widget.retranslate(translator)
            app.processEvents()
            assert widget.temporary_items is original_items
            assert app.focusWidget() is focus and widget._worker is worker
            assert table.verticalScrollBar().value() == scroll[0]
            assert table.horizontalScrollBar().value() == scroll[1]
            assert [(index.row(), index.column()) for index in table.selectedIndexes()] == selected
            assert [table.cellWidget(row, 0) for row in range(60)] == checkboxes
            assert [table.item(row, col) for row in range(60) for col in range(1, 5)] == cells
            assert [box.isEnabled() for box in checkboxes] == enabled
            assert [table.item(row, 1).text() for row in range(60)] == [str(item.path) for item in original_items]
            assert [table.item(row, 1).toolTip() for row in range(60)] == [str(item.path) for item in original_items]
            assert widget.progress.value() == 3 and widget.progress.maximum() == 8
            assert widget.status_label.property("feedbackState") == "warning"
    assert values == [tuple(getattr(item, field.name) for field in fields(item)) for item in original_items]
    assert all(signal.count() == 0 for signal in signals)


def test_new_cleanup_replaces_prior_reason_at_the_actual_branch(page):
    _, _, paths = page
    target = old_file(paths.directory / ".dms-write-abcdefgh")
    item = TemporaryItem(target, 29, 7200, False, False, True)
    assert cleanup_module.cleanup_temporaries([item], paths.directory).removed == 1
    assert item.result_reason is TemporaryResultReason.REMOVED
    assert cleanup_module.cleanup_temporaries([item], paths.directory).preserved == 1
    assert item.result_reason is TemporaryResultReason.PRESERVED_FOR_SAFETY
    assert item.result == "Preservado por segurança"


@pytest.mark.parametrize("terminal", ["failure", "cancelled"])
def test_queued_retired_cleanup_cannot_replace_current_translated_results(page, monkeypatch, terminal):
    app, widget, paths = page
    old_file(paths.directory / ".dms-write-abcdefgh")
    scan(app, widget)
    widget.temporary_items[0].selected = True
    monkeypatch.setattr(TemporaryCleanupWorker, "start", lambda self: None)
    monkeypatch.setattr(TemporaryScanWorker, "start", lambda self: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.start_cleanup()
    retired = widget._worker
    if terminal == "failure":
        retired.failed.emit(RuntimeError("Synthetic private error"))
    else:
        widget.cancel()
        retired.cancelled.emit()
    retired.finished.emit()
    widget.start_temporary_scan()
    current = widget._worker
    item = TemporaryItem(paths.directory / ".dms-access-ab12_cd3", 3, 7200, False, False,
        result="Preservado", result_reason=TemporaryResultReason.PRESERVED)
    current.completed.emit([item])
    widget.retranslate(Translator("en"))
    cells = tuple(widget.temporary_table.item(0, col) for col in range(1, 5))
    before = widget.status_label.text()

    def delayed():
        retired.completed.emit(CleanupResult(20, 0, 0, 1000))
        retired.failed.emit(RuntimeError("Synthetic private error"))
        retired.cancelled.emit()
        retired.finished.emit()

    thread = Thread(target=delayed)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    app.processEvents()
    assert widget._worker is current and widget.temporary_items == [item]
    assert widget.temporary_items[0] is item
    assert tuple(widget.temporary_table.item(0, col) for col in range(1, 5)) == cells
    assert widget.temporary_table.item(0, 4).text() == "Preserved"
    assert widget.status_label.text() == before
    current.finished.emit()
    assert widget._worker is None
