"""Real Qt drop delivery selects inputs through the existing GUI inspectors only."""

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QThread, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui import anonymization_widget as masking_module
from data_mask_studio.gui import restoration_widget as csv_module
from data_mask_studio.gui import html_restoration_widget as html_module
from data_mask_studio.gui.styles import apply_widget_palette, application_stylesheet
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.i18n import Translator
from data_mask_studio.restoration import MissingCodePolicy
from test_main_navigation import build_window


@pytest.fixture
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    window = build_window(tmp_path)
    window.resize(1280, 820)
    window.show()
    app.processEvents()
    paths = {}
    for name in ("Pessoas #23%.CSV", "second.csv", "one.html", "two.HTM", "other.txt"):
        path = tmp_path / name
        path.write_text("Name,Note\nSynthetic Person,Only synthetic data\n"
                        if path.suffix.lower() == ".csv" else
                        "<!doctype html><html><body><p>TEST_ABCDEFGH</p></body></html>",
                        encoding="utf-8")
        paths[name] = path
    return app, window, paths


def mime_for(*paths):
    mime = QMimeData()
    mime.setUrls([path if isinstance(path, QUrl) else QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def enter(target, mime, actions=Qt.DropAction.CopyAction | Qt.DropAction.MoveAction):
    event = QDragEnterEvent(QPoint(5, 5), actions, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, event)
    return event


def drop(target, mime, actions=Qt.DropAction.CopyAction | Qt.DropAction.MoveAction):
    event = QDropEvent(QPointF(5, 5), actions, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, event)
    return event


def deliver(target, *paths):
    mime = mime_for(*paths)
    entered = enter(target, mime)
    completed = drop(target, mime)
    return entered, completed


def forbidden(*args, **kwargs):
    pytest.fail("A drop must not start processing or bypass its availability gate")


@pytest.mark.parametrize("target_name", ["select_button", "path_field"])
def test_mask_drop_uses_real_csv_inspection_without_starting_processing(gui, monkeypatch, target_name):
    app, window, paths = gui
    widget = window.anonymization_widget
    path = paths["Pessoas #23%.CSV"]
    monkeypatch.setattr(QThread, "start", forbidden)
    monkeypatch.setattr(widget._key_provider, "get_key", forbidden)
    source_bytes = path.read_bytes()
    target = getattr(widget, target_name)
    events = deliver(target, path)
    assert all(event.isAccepted() and event.dropAction() == Qt.DropAction.CopyAction for event in events)
    assert not target.property("fileDropActive")
    assert widget._inspection_result.path == path
    assert widget._inspection_result.headers == ["Name", "Note"]
    assert widget.path_field.text() == str(path) and widget.config_table.rowCount() == 2
    assert widget._worker is None and widget._detection_worker is None
    assert not widget.generate_button.isEnabled()  # Existing validation still required.
    assert widget._last_output_path is None and path.read_bytes() == source_bytes


@pytest.mark.parametrize("filename", ["Pessoas #23%.CSV", "one.html", "two.HTM"])
def test_restore_drop_dispatches_supported_formats_without_workers_or_vault_access(gui, monkeypatch, filename):
    app, window, paths = gui
    widget = window.file_restoration_widget
    window.set_current_page(2)
    app.processEvents()
    monkeypatch.setattr(QThread, "start", forbidden)
    monkeypatch.setattr(widget.csv_widget._service, "_repository_factory", forbidden)
    monkeypatch.setattr(widget.html_widget._service, "_repository_factory", forbidden)
    events = deliver(widget.select_button, paths[filename])
    assert all(event.isAccepted() for event in events)
    active = widget.csv_widget if filename.endswith(".CSV") else widget.html_widget
    assert widget.stack.currentWidget() is active
    assert active.path_field.text() == str(paths[filename]) and active._inspection is not None
    assert widget.csv_widget._worker is widget.html_widget._worker is None
    assert not active.summary.toPlainText() and active._last_output_path is None


@pytest.mark.parametrize("initial,target_name", [("one.html", "select_button"), ("two.HTM", "path_field"),
                                               ("Pessoas #23%.CSV", "select_button"),
                                               ("Pessoas #23%.CSV", "path_field")])
def test_loaded_restoration_selection_area_reuses_unified_format_switching(gui, initial, target_name):
    app, window, paths = gui
    widget = window.file_restoration_widget
    window.set_current_page(2)
    assert widget.load_file(paths[initial])
    app.processEvents()
    previous = widget.stack.currentWidget()
    next_path = paths["one.html" if initial.endswith(".CSV") else "Pessoas #23%.CSV"]
    events = deliver(getattr(previous, target_name), next_path)
    assert all(event.isAccepted() for event in events)
    assert widget.stack.currentWidget() is not previous
    assert previous._inspection is None and previous._last_output_path is None
    assert not previous.summary.toPlainText()
    assert widget.stack.currentWidget().path_field.text() == str(next_path)


@pytest.mark.parametrize("page", ["mask", "restore"])
@pytest.mark.parametrize("kind", ["text", "empty", "multiple", "extension", "directory", "missing",
                                 "remote", "network", "relative", "query", "fragment", "move-only"])
def test_invalid_drops_are_ignored_without_changing_selection_or_feedback(gui, tmp_path, monkeypatch, page, kind):
    app, window, paths = gui
    mask = page == "mask"
    owner = window.anonymization_widget if mask else window.file_restoration_widget
    window.set_current_page(0 if mask else 2)
    if mask:
        owner.load_csv(str(paths["Pessoas #23%.CSV"]))
    else:
        assert owner.load_file(paths["Pessoas #23%.CSV"])
    widget = owner if mask else owner.csv_widget
    app.processEvents()
    before = (widget.path_field.text(), widget.status_label.text(),
              widget._inspection_result if mask else widget._inspection)
    directory = tmp_path / "directory.csv"
    directory.mkdir()
    options = {
        "multiple": (paths["Pessoas #23%.CSV"], paths["second.csv"]),
        "extension": (paths["other.txt"],), "directory": (directory,),
        "missing": (tmp_path / "missing.csv",),
        "remote": (QUrl("https://example.invalid/remote.csv"),),
        "network": (QUrl("file://example.invalid/share/remote.csv"),),
        "relative": (QUrl("file:relative.csv"),),
        "query": (QUrl(QUrl.fromLocalFile(str(paths["second.csv"])).toString() + "?query"),),
        "fragment": (QUrl(QUrl.fromLocalFile(str(paths["second.csv"])).toString() + "#fragment"),),
        "move-only": (paths["second.csv"],),
    }
    mime = mime_for(*options.get(kind, ()))
    if kind == "text":
        mime.setText(str(paths["second.csv"]))
    monkeypatch.setattr(masking_module, "inspect_csv", forbidden)
    monkeypatch.setattr(csv_module, "inspect_csv", forbidden)
    monkeypatch.setattr(html_module, "inspect_html", forbidden)
    actions = Qt.DropAction.MoveAction if kind == "move-only" else Qt.DropAction.CopyAction
    target = widget.path_field
    assert not enter(target, mime, actions).isAccepted()
    assert not drop(target, mime, actions).isAccepted()
    assert not target.property("fileDropActive")
    assert (widget.path_field.text(), widget.status_label.text(),
            widget._inspection_result if mask else widget._inspection) == before


@pytest.mark.parametrize("page", ["mask", "csv", "html"])
def test_drop_and_keyboard_file_dialog_selection_produce_equivalent_state(gui, monkeypatch, page):
    app, window, paths = gui
    mask = page == "mask"
    owner = window.anonymization_widget if mask else window.file_restoration_widget
    window.set_current_page(0 if mask else 2)
    app.processEvents()
    path = paths["one.html" if page == "html" else "Pessoas #23%.CSV"]
    widget = owner if mask else (owner.csv_widget if page == "csv" else owner.html_widget)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    monkeypatch.setattr(QThread, "start", forbidden)
    owner.select_button.setFocus()
    QTest.keyClick(owner.select_button, Qt.Key.Key_Space)
    def state():
        return (widget.path_field.text(), widget.encoding_label.text(), widget.status_label.text(),
                (widget._inspection_result if mask else widget._inspection).headers if page != "html" else None,
                widget._last_output_path)
    selected = state()
    owner.clear_selection()
    app.processEvents()
    assert all(event.isAccepted() for event in deliver(owner.select_button, path))
    assert state() == selected


@pytest.mark.parametrize("owner_name,attribute", [
    ("anonymization_widget", "_worker"), ("anonymization_widget", "_detection_worker"),
    ("restoration_widget", "_worker"), ("html_restoration_widget", "_worker"),
])
def test_owned_worker_blocks_drop_even_after_thread_stops_and_is_rechecked_at_drop(
    gui, monkeypatch, owner_name, attribute,
):
    app, window, paths = gui
    mask = owner_name == "anonymization_widget"
    owner = window.anonymization_widget if mask else window.file_restoration_widget
    window.set_current_page(0 if mask else 2)
    app.processEvents()
    target = owner.path_field if mask else owner.select_button
    mime = mime_for(paths["Pessoas #23%.CSV"])
    assert enter(target, mime).isAccepted()
    worker = QThread()
    assert not worker.isRunning()
    with monkeypatch.context() as guard:
        guard.setattr(getattr(window, owner_name), attribute, worker)
        guard.setattr(masking_module, "inspect_csv", forbidden)
        guard.setattr(csv_module, "inspect_csv", forbidden)
        assert not drop(target, mime).isAccepted()
        assert not enter(target, mime).isAccepted()
        assert not target.property("fileDropActive")
        assert not drop(target, mime).isAccepted()
    assert all(event.isAccepted() for event in deliver(target, paths["Pessoas #23%.CSV"]))


def test_package_mode_drop_preserves_source_policy_and_requires_normal_authentication(gui, monkeypatch):
    app, window, paths = gui
    owner = window.file_restoration_widget
    window.set_current_page(2)
    assert owner.load_file(paths["Pessoas #23%.CSV"])
    widget = owner.csv_widget
    widget.source_combo.setCurrentIndex(1)
    widget.package_controls.path.setText("synthetic-package.dmspackage")
    widget.package_controls.password.setText("transient synthetic password")
    app.processEvents()
    monkeypatch.setattr(QThread, "start", forbidden)
    events = deliver(widget.path_field, paths["second.csv"])
    assert all(event.isAccepted() for event in events)
    assert widget.source_combo.currentData() == "package"
    assert widget.missing_policy_combo.currentData() == MissingCodePolicy.ABORT.value
    assert not any(checkbox.isChecked() for checkbox in widget._checkboxes)
    widget._checkboxes[0].setChecked(True)
    assert widget._configuration().missing_code_policy is MissingCodePolicy.ABORT
    assert not widget.missing_policy_combo.isEnabled() and not widget.analyze_button.isEnabled()
    assert widget.package_controls.path.text() == "synthetic-package.dmspackage"
    assert not widget.package_controls.password.text()  # Same lifecycle as file selection.
    assert widget._worker is None and widget._last_output_path is None


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_drag_highlight_is_scoped_theme_aware_and_cleared_without_focus_or_geometry_changes(gui, colors):
    app, window, paths = gui
    widget = window.anonymization_widget
    apply_widget_palette(window, colors)
    app.processEvents()
    target = widget.path_field
    target.setFocus()
    geometry, focus, text = target.geometry(), app.focusWidget(), target.text()
    mime = mime_for(paths["Pessoas #23%.CSV"])
    assert enter(target, mime).isAccepted() and target.property("fileDropActive") is True
    move = QDragMoveEvent(QPoint(7, 7), Qt.DropAction.CopyAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, move)
    assert move.isAccepted() and move.dropAction() == Qt.DropAction.CopyAction
    assert f'QLineEdit[fileDropActive="true"] {{ border-color: {colors.focus}; }}' in application_stylesheet(colors)
    assert target.geometry() == geometry and app.focusWidget() is focus and target.text() == text
    QApplication.sendEvent(target, QDragLeaveEvent())
    assert not target.property("fileDropActive")
    assert enter(target, mime).isAccepted()
    widget._set_processing_state(True)
    assert not target.property("fileDropActive")
    assert not drop(target, mime).isAccepted()
    widget._set_processing_state(False)


@pytest.mark.parametrize("page", ["mask", "restore"])
def test_modal_dialog_rejects_drops_and_tooltips_retranslate_without_selection_changes(gui, page):
    app, window, paths = gui
    owner = window.anonymization_widget if page == "mask" else window.file_restoration_widget
    window.set_current_page(0 if page == "mask" else 2)
    app.processEvents()
    target = owner.select_button
    message_id = "masking.file.drop_hint" if page == "mask" else "restoration.file.drop_hint"
    for locale in ("en", "pt-BR"):
        translator = Translator(locale)
        owner.retranslate(translator)
        assert target.toolTip() == target.accessibleDescription() == translator.text(message_id)
    dialog = QMessageBox(window)
    dialog.setModal(True)
    dialog.open()
    app.processEvents()
    assert app.activeModalWidget() is dialog
    assert not enter(target, mime_for(paths["Pessoas #23%.CSV"])).isAccepted()
    dialog.reject()


@pytest.mark.parametrize("page", ["mask", "restore"])
def test_drop_with_valid_extension_still_runs_existing_invalid_content_validation(gui, tmp_path, page):
    app, window, _ = gui
    owner = window.anonymization_widget if page == "mask" else window.file_restoration_widget
    window.set_current_page(0 if page == "mask" else 2)
    app.processEvents()
    source = tmp_path / "empty.csv"
    source.write_bytes(b"")
    assert all(event.isAccepted() for event in deliver(owner.select_button, source))
    widget = owner if page == "mask" else owner.csv_widget
    assert (widget._inspection_result if page == "mask" else widget._inspection) is None
    assert widget.status_label.property("feedbackState") == "error"
    assert widget._worker is None and source.read_bytes() == b""


def test_missing_file_and_busy_state_are_rechecked_before_selection(gui, monkeypatch):
    _, window, paths = gui
    widget = window.anonymization_widget
    target = widget.path_field
    mime = mime_for(paths["second.csv"])
    assert enter(target, mime).isAccepted()
    paths["second.csv"].unlink()  # Only the isolated synthetic fixture file.
    monkeypatch.setattr(masking_module, "inspect_csv", forbidden)
    assert not drop(target, mime).isAccepted()
    assert not target.property("fileDropActive")
    mime = mime_for(paths["Pessoas #23%.CSV"])
    assert enter(target, mime).isAccepted()
    with monkeypatch.context() as guard:
        guard.setattr(widget, "_analyzing", True)
        assert not drop(target, mime).isAccepted()
    assert widget._inspection_result is None


@pytest.mark.parametrize("page", ["mask", "restore"])
def test_package_files_are_not_treated_as_input_data(gui, tmp_path, page):
    app, window, paths = gui
    owner = window.anonymization_widget if page == "mask" else window.file_restoration_widget
    window.set_current_page(0 if page == "mask" else 2)
    app.processEvents()
    package = tmp_path / "synthetic.dmspackage"
    package.write_bytes(b"synthetic unsupported input")
    assert not enter(owner.select_button, mime_for(package)).isAccepted()
    assert not drop(owner.select_button, mime_for(package)).isAccepted()
    if page == "mask":
        assert not enter(owner.select_button, mime_for(paths["one.html"])).isAccepted()
        assert owner._inspection_result is None
    else:
        assert owner.stack.currentIndex() == 0
