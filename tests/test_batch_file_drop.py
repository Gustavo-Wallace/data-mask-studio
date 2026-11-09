"""Batch drops use existing discovery/addition without analysis or processing."""

import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QPoint, QThread, Qt, QUrl
from PySide6.QtGui import QDragLeaveEvent, QDragMoveEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui import batch_widget as masking
from data_mask_studio.gui import batch_restoration_widget as restoration
from data_mask_studio.gui.styles import apply_widget_palette
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.i18n import Translator
from test_main_navigation import build_window
from test_single_file_drop import enter, drop, mime_for, forbidden


@pytest.fixture(params=["mask", "restore"])
def page(tmp_path, monkeypatch, request):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    window = build_window(tmp_path)
    restore = request.param == "restore"
    widget = window.batch_restoration_widget if restore else window.batch_widget
    window.set_current_page(3 if restore else 1)
    window.resize(1280, 820)
    window.show()
    app.processEvents()
    monkeypatch.setattr(QThread, "start", forbidden)
    first = tmp_path / "Pessoa #23%.CSV"
    first.write_text("Name\nSynthetic\n", encoding="utf-8")
    folder = tmp_path / "synthetic-folder"
    folder.mkdir()
    for name in ("z.csv", "A.CSV", "report.html", "other.HTM", "ignored.txt"):
        (folder / name).write_text("Name\nSynthetic\n", encoding="utf-8")
    (folder / "nested").mkdir()
    (folder / "nested" / "excluded.csv").write_text("Name\nSynthetic\n", encoding="utf-8")
    return app, window, widget, restore, first, folder


def deliver(target, *paths):
    mime = mime_for(*paths)
    assert enter(target, mime).isAccepted()
    event = drop(target, mime)
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.CopyAction


def module_for(restore):
    return restoration if restore else masking


def discovery_name(restore):
    return "discover_files" if restore else "discover_csv_files"


def test_mixed_files_folders_filters_and_duplicates_match_existing_buttons(page, monkeypatch):
    _, _, widget, restore, first, folder = page
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(first)], ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(folder))
    widget.add_files_button.setFocus()
    QTest.keyClick(widget.add_files_button, Qt.Key.Key_Space)
    widget.add_folder_button.setFocus()
    QTest.keyClick(widget.add_folder_button, Qt.Key.Key_Space)
    expected = [item.path for item in widget.files]
    assert len(expected) == (5 if restore else 3)
    widget.clear_files()
    deliver(widget.file_table.viewport(), first, folder, first, folder, folder / "ignored.txt")
    assert [item.path for item in widget.files] == expected
    assert all(item.status.value == "pending" and item.output_path is None for item in widget.files)
    assert not any(item.path.name == "excluded.csv" for item in widget.files)
    before = list(widget.files)
    deliver(widget.add_files_button, first, folder)
    assert widget.files == before and all(a is b for a, b in zip(widget.files, before))


def test_drag_never_discovers_folders_and_drop_uses_original_discovery_once(page, monkeypatch):
    _, _, widget, restore, _, folder = page
    module = module_for(restore)
    original = getattr(module, discovery_name(restore))
    calls = []
    def discover(path, **kwargs):
        calls.append((path, kwargs))
        return original(path, **kwargs)
    monkeypatch.setattr(module, discovery_name(restore), discover)
    mime = mime_for(folder)
    target = widget.file_table.viewport()
    assert enter(target, mime).isAccepted()
    move = QDragMoveEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, move)
    assert move.isAccepted() and calls == []
    assert drop(target, mime).isAccepted()
    assert calls == [(folder, {"follow_symlinks": False})]


def test_invalid_payloads_do_not_change_results_selection_or_output_state(page, tmp_path):
    _, _, widget, _, first, folder = page
    widget.add_paths([first])
    widget.file_table.selectRow(0)
    item = widget.files[0]
    output = tmp_path / "published.csv"
    output.write_bytes(b"synthetic published output")
    item.output_path = output
    widget._output_directory = tmp_path
    widget.open_output_button.setEnabled(True)
    selected = [index.row() for index in widget.file_table.selectionModel().selectedRows()]
    status = widget.status_label.text()
    plain = QMimeData()
    plain.setText(str(first))
    invalid = [QMimeData(), plain, mime_for(folder / "ignored.txt"),
               mime_for(tmp_path / "missing.csv"), mime_for(QUrl("https://example.invalid/file.csv")),
               mime_for(first, QUrl("file://example.invalid/share/file.csv"))]
    for mime in invalid:
        assert not enter(widget.file_table.viewport(), mime).isAccepted()
        assert not drop(widget.file_table.viewport(), mime).isAccepted()
    assert widget.files == [item] and item.output_path == output
    assert widget.status_label.text() == status and widget._output_directory == tmp_path
    assert widget.open_output_button.isEnabled() and output.read_bytes() == b"synthetic published output"
    assert [index.row() for index in widget.file_table.selectionModel().selectedRows()] == selected == [0]


@pytest.mark.parametrize("phase", ["inspection", "processing"])
def test_worker_ownership_blocks_drops_until_retired_and_rechecks_before_drop(page, monkeypatch, phase):
    _, _, widget, restore, first, folder = page
    attribute = ("_processing_worker" if phase == "processing" else
                 "_analysis_worker" if restore else "_validation_worker")
    mime = mime_for(first, folder)
    target = widget.file_table.viewport()
    assert enter(target, mime).isAccepted()
    worker = QThread()
    assert not worker.isRunning()
    with monkeypatch.context() as guard:
        guard.setattr(widget, attribute, worker)
        guard.setattr(module_for(restore), discovery_name(restore), forbidden)
        assert not drop(target, mime).isAccepted()
        assert not enter(target, mime).isAccepted()
        assert widget.files == [] and getattr(widget, attribute) is worker
    deliver(target, first)
    assert [item.path for item in widget.files] == [first]


def test_disabled_operations_and_empty_folders_keep_existing_state_coherent(page, tmp_path):
    _, _, widget, _, first, _ = page
    widget._set_busy(True)
    assert not enter(widget.file_table.viewport(), mime_for(first)).isAccepted()
    assert widget.files == []
    widget._set_busy(False)
    empty = tmp_path / "empty-folder"
    empty.mkdir()
    deliver(widget.add_folder_button, empty)
    assert widget.files == [] and not widget.open_output_button.isEnabled()
    deliver(widget.add_folder_button, first)
    assert len(widget.files) == 1


def test_folder_error_does_not_partially_add_files_or_start_work(page, monkeypatch):
    _, _, widget, restore, first, folder = page
    error_type = restoration.BatchRestorationError if restore else masking.BatchError
    def fail(*args, **kwargs):
        raise error_type("Synthetic safe discovery failure")
    monkeypatch.setattr(module_for(restore), discovery_name(restore), fail)
    deliver(widget.file_table.viewport(), first, folder)
    assert widget.files == []
    assert widget.status_label.text() == "Synthetic safe discovery failure"
    assert widget.status_label.property("feedbackState") == "error"


def test_junction_and_linked_ancestors_are_not_traversed(page, tmp_path, monkeypatch):
    _, _, widget, restore, _, folder = page
    outside = tmp_path / "synthetic-link-target"
    outside.mkdir()
    (outside / "excluded.csv").write_text("Name\nSynthetic\n", encoding="utf-8")
    link = folder / "linked-folder"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    original = Path.is_file
    def no_link_probe(path):
        assert path != link and link not in path.parents, "Must not follow the link target"
        return original(path)
    monkeypatch.setattr(Path, "is_file", no_link_probe)
    assert not enter(widget.file_table.viewport(), mime_for(link)).isAccepted()
    assert not enter(widget.file_table.viewport(), mime_for(link / "excluded.csv")).isAccepted()
    discover = getattr(module_for(restore), discovery_name(restore))
    assert discover(link, follow_symlinks=False) == []
    deliver(widget.file_table.viewport(), folder)
    assert len(widget.files) == (4 if restore else 2)
    assert all(item.path.parent == folder for item in widget.files)


def test_symbolic_file_entries_are_filtered_before_following_metadata(page, monkeypatch):
    _, _, widget, restore, _, folder = page
    link = folder / "pretend-link.csv"
    link.write_bytes(b"synthetic link stand-in")
    original_stat, original_link, original_file = Path.lstat, Path.is_symlink, Path.is_file
    def lstat(path):
        return SimpleNamespace(st_mode=stat.S_IFLNK) if path == link else original_stat(path)
    def is_file(path):
        assert path != link, "Discovery must skip symbolic links before is_file follows them"
        return original_file(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original_link(path))
    discover = getattr(module_for(restore), discovery_name(restore))
    assert link in discover(folder)  # Existing selector/default behavior is unchanged.
    monkeypatch.setattr(Path, "is_file", is_file)
    assert not enter(widget.file_table.viewport(), mime_for(link)).isAccepted()
    deliver(widget.file_table.viewport(), folder)
    assert len(widget.files) == (4 if restore else 2)
    assert all(item.path != link for item in widget.files)


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_scoped_drag_feedback_and_retranslation_preserve_rows_focus_and_state(page, colors, monkeypatch):
    app, window, widget, restore, first, folder = page
    apply_widget_palette(window, colors)
    widget.add_paths([first])
    widget.file_table.selectRow(0)
    widget.file_table.setFocus()
    app.processEvents()
    rows, item, cell = list(widget.files), widget.files[0], widget.file_table.item(0, 0)
    geometry, focus = widget.file_table.geometry(), app.focusWidget()
    selection = widget.file_table.selectionModel().selectedRows()
    target = widget.file_table.viewport()
    mime = mime_for(folder)
    assert enter(target, mime).isAccepted() and widget.file_table.property("fileDropActive") is True
    translators = [Translator(locale) for locale in ("en", "pt-BR")]
    with monkeypatch.context() as guard:
        guard.setattr(module_for(restore), discovery_name(restore), forbidden)
        guard.setattr(widget, "add_paths", forbidden)
        for translator in translators:
            widget.retranslate(translator)
            message_id = "batch_restoration.files.drop_hint" if restore else "batch.files.drop_hint"
            assert widget.file_table.toolTip() == translator.text(message_id)
    assert widget.files == rows and widget.files[0] is item and widget.file_table.item(0, 0) is cell
    assert widget.file_table.geometry() == geometry and app.focusWidget() is focus
    assert widget.file_table.selectionModel().selectedRows() == selection
    QApplication.sendEvent(target, QDragLeaveEvent())
    assert not widget.file_table.property("fileDropActive")
    # Addition itself must retain existing item state/selection, like the buttons.
    deliver(target, folder)
    assert widget.files[0] is item
    assert [index.row() for index in widget.file_table.selectionModel().selectedRows()] == [0]
