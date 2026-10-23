"""Unified navigation dispatches to the real, independent restoration widgets."""

import pytest
from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.file_restoration_widget import FileRestorationWidget
from data_mask_studio.gui.html_restoration_widget import HTMLRestorationWidget
from data_mask_studio.gui.html_restoration_worker import HTMLRestorationWorker
from data_mask_studio.gui.restoration_worker import CSVRestorationWorker
from data_mask_studio.restoration import MissingCodePolicy
from data_mask_studio.html_restoration import HTMLRestorationError
from test_main_navigation import build_window
from test_restoration_options_consistency import CODE, seed_vault
from test_package_csv_restoration import make_case, PASSWORD


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    window = build_window(tmp_path)
    unified = window.file_restoration_widget
    seed_vault(window)
    paths = {}
    for name in ("one_masked.csv", "old_anonimizado.csv", "upper.CSV", "one.html", "two.HTM"):
        path = tmp_path / name
        path.write_text(f"Name,Kind\n{CODE},SYNTHETIC\n" if path.suffix.lower() == ".csv"
                        else f"<!doctype html><html><body><p>{CODE}</p></body></html>", encoding="utf-8")
        paths[name] = path
    window.set_current_page(window.page_index(unified))
    window.show()
    app.processEvents()
    window.resize(1280, 820)
    app.processEvents()
    return app, window, unified, paths


def finish(app, widget):
    worker = widget._worker
    assert worker is not None and worker.wait(15000)
    app.processEvents()
    assert widget._worker is None
    return worker


def assert_no_feedback(widget):
    assert not widget.summary.toPlainText()
    assert widget._last_output_path is None and widget._last_error is None
    assert widget.progress_bar.value() == 0 and widget.progress_bar.isHidden()
    assert widget.progress_label.isHidden() and not widget.progress_label.text()
    assert widget.open_folder_button.isHidden()
    assert widget.status_label.property("feedbackState") != "success"


def test_single_restoration_destination_has_clear_initial_state_and_keyboard_access(page):
    app, window, unified, _ = page
    assert isinstance(unified, FileRestorationWidget)
    assert [button.text() for button in window.navigation.buttons] == [
        "Mascarar arquivo", "Mascaramento em lote", "Restaurar arquivo", "Restauração em lote",
        "Consultar cofre", "Backup e recuperação", "Cofre e manutenção",
    ]
    assert window.page_stack.count() == 7
    assert window.page_index(window.restoration_widget) == window.page_index(window.html_restoration_widget) == 2
    assert unified.stack.currentIndex() == 0 and unified.select_button.isVisible()
    assert not unified.csv_widget.isVisible() and not unified.html_widget.isVisible()
    assert "CSV" in unified.format_label.text() and "HTM" in unified.format_label.text()
    unified.select_button.setFocus()
    assert unified.select_button.hasFocus()
    QTest.keyClick(window.navigation.buttons[2], Qt.Key.Key_Down)
    app.processEvents()
    assert window.current_page_index() == 3 and window.navigation.buttons[3].hasFocus()


@pytest.mark.parametrize("filename", ["one_masked.csv", "old_anonimizado.csv", "upper.CSV", "one.html", "two.HTM"])
def test_supported_file_dispatch_preserves_inspectors_and_format_specific_controls(page, filename):
    app, _, unified, paths = page
    assert unified.load_file(paths[filename])
    app.processEvents()
    csv = paths[filename].suffix.lower() == ".csv"
    widget = unified.csv_widget if csv else unified.html_widget
    other = unified.html_widget if csv else unified.csv_widget
    assert unified.stack.currentWidget() is widget and widget.isVisible() and not other.isVisible()
    assert widget.path_field.text() == str(paths[filename])
    assert widget.encoding_label.text() == "utf-8" and widget._inspection is not None
    assert widget.select_button.text() == "Selecionar arquivo mascarado"
    assert widget.select_button.isVisible() and not unified.select_button.isVisible()
    assert not widget.summary.toPlainText() and widget.open_folder_button.isHidden()
    if csv:
        assert widget.source_combo.isVisible() and widget.table.rowCount() == 2
        assert widget.delimiter_label.text() == "Vírgula (,)"
    else:
        assert not unified.csv_widget.package_controls.isVisible()
        assert not hasattr(widget, "package_controls")
        assert "cofre local" in unified.format_label.text()


def test_selector_filters_formats_and_cancel_preserves_valid_selection_and_password(page, monkeypatch):
    _, _, unified, paths = page
    calls = []

    def choose(*args):
        calls.append(args)
        return str(paths["one_masked.csv"]), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    unified.select_button.click()
    assert "*.csv *.html *.htm" in calls[0][3]
    csv = unified.csv_widget
    csv.source_combo.setCurrentIndex(1)
    csv.package_controls.password.setText("transient synthetic password")
    before = csv._inspection
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    csv.select_button.click()
    assert csv._inspection is before
    assert csv.package_controls.password.text() == "transient synthetic password"


@pytest.mark.parametrize("invalid", ["empty.csv", "empty.html", "missing.csv", "missing.htm", "data.xlsx"])
def test_invalid_file_never_switches_to_unvalidated_workflow(page, tmp_path, invalid):
    _, _, unified, paths = page
    unified.load_file(paths["one_masked.csv"])
    before = unified.csv_widget._inspection
    path = tmp_path / invalid
    if not invalid.startswith("missing"):
        path.write_bytes(b"")
    assert not unified.load_file(path)
    assert unified.stack.currentWidget() is unified.csv_widget
    assert unified.csv_widget._inspection is before
    assert not unified.selection_status.isHidden()
    assert unified.selection_status.property("feedbackState") == "error"
    assert unified.html_widget._inspection is None


def test_csv_html_csv_clears_old_results_and_output_actions_after_real_operations(page, tmp_path):
    app, _, unified, paths = page
    csv, html = unified.csv_widget, unified.html_widget
    unified.load_file(paths["old_anonimizado.csv"])
    csv._checkboxes[0].setChecked(True)
    csv.start_restoration(tmp_path / "restored.csv", overwrite=False)
    finish(app, csv)
    assert "  Synthetic Person  " in (tmp_path / "restored.csv").read_text(encoding="utf-8-sig")
    assert csv.open_folder_button.isVisible()
    unified.load_file(paths["one.html"])
    assert_no_feedback(csv)
    assert not html.summary.toPlainText() and html.open_folder_button.isHidden()
    html.start_restoration(tmp_path / "restored.html", overwrite=False)
    finish(app, html)
    assert "  Synthetic Person  " in (tmp_path / "restored.html").read_text(encoding="utf-8")
    assert html.open_folder_button.isVisible()
    unified.load_file(paths["one_masked.csv"])
    assert_no_feedback(html)
    assert not csv.summary.toPlainText() and csv.open_folder_button.isHidden()
    assert not any(box.isChecked() for box in csv._checkboxes)
    assert (tmp_path / "restored.csv").exists() and (tmp_path / "restored.html").exists()


def test_format_switch_masks_and_clears_password_without_crossing_options(page):
    _, _, unified, paths = page
    csv, html = unified.csv_widget, unified.html_widget
    unified.load_file(paths["one_masked.csv"])
    csv.source_combo.setCurrentIndex(1)
    csv.representation_combo.setCurrentIndex(1)
    csv.package_controls.password.setText("synthetic transient secret")
    csv.package_controls.show_password.setChecked(True)
    unified.load_file(paths["one.html"])
    assert not csv.package_controls.password.text() and not csv.package_controls.show_password.isChecked()
    assert csv.package_controls.password.echoMode() == QLineEdit.EchoMode.Password
    assert html.representation_combo.currentIndex() == 0
    html.missing_policy_combo.setCurrentIndex(1)
    unified.load_file(paths["one_masked.csv"])
    assert csv.source_combo.currentData() == "package"  # Per-format configuration is preserved.
    assert csv.missing_policy_combo.currentData() == MissingCodePolicy.ABORT.value
    assert not csv.missing_policy_combo.isEnabled() and not csv.analyze_button.isEnabled()
    assert csv.representation_combo.currentIndex() == 1 and not csv.package_controls.password.text()
    unified.clear_selection()
    assert unified.stack.currentIndex() == 0 and unified.select_button.isVisible()
    assert_no_feedback(csv)
    assert_no_feedback(html)


def test_real_package_restoration_stays_package_only_in_unified_page(page, tmp_path, monkeypatch):
    app, _, unified, _ = page
    directory = tmp_path / "package-case"
    directory.mkdir()
    _, config, package, _, output = make_case(directory)
    unified.load_file(config.source_path)
    csv = unified.csv_widget
    csv.source_combo.setCurrentIndex(1)
    csv._checkboxes[0].setChecked(True)
    csv._checkboxes[1].setChecked(True)
    csv.package_controls.path.setText(str(package))
    csv.package_controls.password.setText(PASSWORD)

    def forbidden(*args, **kwargs):
        pytest.fail("Package restoration cannot consult a local vault")

    monkeypatch.setattr(csv._service, "restore", forbidden)
    csv.start_restoration(output, overwrite=False)
    finish(app, csv)
    assert output.is_file() and csv._last_error is None
    assert "sucesso" in csv.status_label.text() and not csv.package_controls.password.text()


@pytest.mark.parametrize("kind", ["csv", "html"])
@pytest.mark.parametrize("outcome", ["finished", "failure", "cancel"])
def test_switching_waits_for_worker_retirement_and_respects_terminal_state(page, tmp_path, monkeypatch, kind, outcome):
    _, _, unified, paths = page
    is_csv = kind == "csv"
    unified.load_file(paths["one_masked.csv" if is_csv else "one.html"])
    widget = unified.csv_widget if is_csv else unified.html_widget
    if is_csv:
        widget.select_all_columns()
    worker_type = CSVRestorationWorker if is_csv else HTMLRestorationWorker
    monkeypatch.setattr(worker_type, "start", lambda self: None)
    widget.start_restoration(tmp_path / ("out.csv" if is_csv else "out.html"), overwrite=False)
    old = widget._worker
    assert old is not None and not unified.select_button.isEnabled()
    assert not unified.load_file(paths["one.html" if is_csv else "one_masked.csv"])
    unified.clear_selection()
    assert widget._worker is old and unified.stack.currentWidget() is widget
    # Even a thread which is no longer running retains ownership until its
    # queued finished signal is delivered; no timeout/sleep is used here.
    if outcome == "cancel":
        old.cancelled.emit()
    elif outcome == "failure":
        old.failed.emit(HTMLRestorationError("Synthetic safe failure"))
    old.finished.emit()
    assert widget._worker is None and unified.select_button.isEnabled()
    assert unified.load_file(paths["one.html" if is_csv else "one_masked.csv"])
    assert_no_feedback(widget)


def test_retired_html_worker_cannot_repopulate_csv_after_queued_signals(page, tmp_path, monkeypatch):
    app, _, unified, paths = page
    unified.load_file(paths["one.html"])
    html = unified.html_widget
    monkeypatch.setattr(HTMLRestorationWorker, "start", lambda self: None)
    html.start_restoration(tmp_path / "out.html", overwrite=False)
    old = html._worker

    class LateEmitter(QThread):
        def run(self):
            old.failed.emit(HTMLRestorationError("OLD_FAILURE_SENTINEL"))
            old.cancelled.emit()
            old.finished.emit()

    emitter = LateEmitter(html)
    emitter.start()
    assert emitter.wait(5000)
    old.finished.emit()
    unified.load_file(paths["one_masked.csv"])
    before = html.status_label.text(), unified.csv_widget.status_label.text()
    app.processEvents()
    assert (html.status_label.text(), unified.csv_widget.status_label.text()) == before
    assert_no_feedback(html)


def test_shared_selector_switches_both_directions_and_keeps_focus_on_visible_controls(page, monkeypatch):
    app, _, unified, paths = page
    unified.load_file(paths["one_masked.csv"])
    for filename, widget in (("one.html", unified.html_widget), ("one_masked.csv", unified.csv_widget)):
        previous = unified.stack.currentWidget()
        previous.select_button.setFocus()
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(paths[filename]), ""))
        previous.select_button.click()
        app.processEvents()
        assert unified.stack.currentWidget() is widget and widget.isVisible()
        assert not previous.isVisible()
        focused = app.focusWidget()
        assert focused is not None and focused.isVisible()
        assert widget.isAncestorOf(focused) or focused is widget


def test_real_html_completion_is_delivered_on_gui_thread(page, tmp_path, monkeypatch):
    app, _, unified, paths = page
    unified.load_file(paths["one.html"])
    html = unified.html_widget
    calls = []
    completed = html._restoration_completed

    def on_completed(result):
        calls.append(QThread.currentThread() == app.thread())
        completed(result)

    monkeypatch.setattr(html, "_restoration_completed", on_completed)
    html.start_restoration(tmp_path / "thread-check.html", overwrite=False)
    finish(app, html)
    assert calls == [True] and html._last_output_path.is_file()
