"""Individual restoration views adapt without hiding their existing workspace."""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.restoration import MissingCodePolicy
from test_main_navigation import build_window
from test_package_csv_restoration import make_case, PASSWORD
from test_restoration_options_consistency import CODE, seed_vault


@pytest.fixture(params=["csv", "package", "html"])
def page(request, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    widget = window.html_restoration_widget if request.param == "html" else window.restoration_widget
    window.show()
    window.resize(1280, 820)
    window.set_current_page(window.page_index(widget))
    if request.param == "package":
        widget.source_combo.setCurrentIndex(1)
    settle(app)
    return app, window, widget, request.param


def settle(app):
    # A parent resize can post a second layout request for child views.
    app.processEvents()
    app.processEvents()


def load(page, tmp_path, *, columns=30):
    app, _, widget, kind = page
    if kind == "html":
        source = tmp_path / "synthetic.html"
        source.write_text(f"<p>{CODE}</p><p>{CODE}</p>", encoding="utf-8")
        widget.load_html(str(source))
    else:
        source = tmp_path / "synthetic.csv"
        with source.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows([
                ["Name"] + [f"Synthetic header {index}" for index in range(1, columns)],
                [CODE] + ["SYNTHETIC"] * (columns - 1),
            ])
        widget.load_csv(str(source))
    settle(app)
    return source


def assert_workspace(widget, kind):
    assert all(control.isVisible() for control in (
        widget.select_button, widget.path_field, widget.file_name_label, widget.encoding_label,
        widget.options_toggle, widget.analyze_button, widget.generate_button,
        widget.summary, widget.status_label,
    ))
    assert widget.analyze_button.property("role") == "secondary"
    assert widget.generate_button.property("role") == "primary"
    assert widget.path_field.isReadOnly() and widget.summary.isReadOnly()
    assert widget.status_label.geometry().top() > widget.summary.geometry().bottom()
    if kind != "html":
        assert all(control.isVisible() for control in (
            widget.source_combo, widget.delimiter_label, widget.select_all_button,
            widget.unselect_all_button, widget.selected_count_label, widget.table,
        ))
        assert widget.table.height() >= widget.table.minimumHeight()
        assert widget.table.viewport().height() >= 2 * widget.table.verticalHeader().defaultSectionSize()
        assert widget.package_controls.isVisible() == (kind == "package")
        assert widget.package_controls.isEnabled() == (kind == "package")
        if kind == "package":
            assert not widget.analyze_button.isEnabled()
            assert not widget.missing_policy_combo.isEnabled()
            assert widget.missing_policy_combo.currentData() == MissingCodePolicy.ABORT.value


def test_empty_views_are_compact_without_concealing_primary_controls(page):
    _, _, widget, kind = page
    assert_workspace(widget, kind)
    assert widget.summary.maximumHeight() == widget.summary.minimumHeight()
    assert widget.summary.height() == widget.summary.minimumHeight()
    assert widget.summary.accessibleDescription() == widget.summary.empty_text
    assert not widget.options_controls.isVisible() and widget.options_summary.isVisible()
    assert not widget.generate_button.isEnabled() and not widget.analyze_button.isEnabled()
    assert not widget.progress_bar.isVisible() and not widget.cancel_button.isVisible()
    if kind != "html":
        assert widget.table.rowCount() == 0
        assert widget.table.maximumHeight() == widget.table.minimumHeight()
        assert widget.table.empty_text == "Selecione um CSV anonimizado para revisar as colunas."


def test_populated_results_expand_scroll_and_compact_again(page, tmp_path):
    app, window, widget, kind = page
    window.resize(1600, 1000)
    settle(app)
    empty_height = widget.summary.height()
    load(page, tmp_path)
    widget.summary.setPlainText("Synthetic aggregate report\n" * 120)
    settle(app)
    assert_workspace(widget, kind)
    assert widget.summary.maximumHeight() == 16_777_215
    assert widget.summary.height() > empty_height
    assert widget.summary.verticalScrollBar().maximum() > 0
    if kind != "html":
        assert widget.table.verticalScrollBar().maximum() > 0
        assert widget.layout().stretch(widget.layout().indexOf(widget.table)) == 1
    assert widget.layout().stretch(widget.layout().indexOf(widget.summary)) == 1
    populated_height = widget.summary.height()
    window.resize(1600, 1200)
    settle(app)
    assert widget.summary.height() > populated_height
    widget.summary.clear()
    settle(app)
    assert widget.summary.height() == empty_height
    assert widget.summary.maximumHeight() == widget.summary.minimumHeight()


@pytest.mark.parametrize("page", ["csv", "package"], indirect=True)
def test_loaded_columns_get_space_keep_keyboard_selection_and_compact_when_cleared(page, tmp_path):
    app, _, widget, kind = page
    empty_height = widget.table.height()
    source = load(page, tmp_path)
    assert widget.table.rowCount() == 30
    assert widget.table.maximumHeight() == 16_777_215 and widget.table.height() > empty_height
    assert widget.file_name_label.text() == source.name and widget.encoding_label.text() == "utf-8"
    assert widget.delimiter_label.text() == "Vírgula (,)"
    assert not any(checkbox.isChecked() for checkbox in widget._checkboxes)
    checkbox = widget._checkboxes[0]
    checkbox.setFocus()
    QTest.keyClick(checkbox, Qt.Key.Key_Space)
    assert checkbox.isChecked() and widget.selected_count_label.text() == "1 de 30 colunas selecionadas"
    widget.select_all_button.click()
    assert all(checkbox.isChecked() for checkbox in widget._checkboxes)
    widget.unselect_all_button.click()
    assert not any(checkbox.isChecked() for checkbox in widget._checkboxes)
    widget.clear_selection()
    settle(app)
    assert widget.table.rowCount() == 0 and widget.table.height() == empty_height
    assert not widget.path_field.text() and not widget.generate_button.isEnabled()
    assert_workspace(widget, kind)


def test_disclosure_keyboard_order_and_focus_preserve_mode_restrictions(page, tmp_path):
    app, _, widget, kind = page
    load(page, tmp_path)
    widget.options_toggle.setFocus()
    widget.options_toggle.click()
    settle(app)
    assert widget.options_toggle.hasFocus() and widget.options_controls.isVisible()
    QTest.keyClick(widget.options_toggle, Qt.Key.Key_Tab)
    expected = widget.representation_combo if kind == "package" else widget.missing_policy_combo
    assert expected.hasFocus()
    widget.options_toggle.setChecked(False)
    settle(app)
    assert widget.options_toggle.hasFocus() and not widget.options_controls.isVisible()
    QTest.keyClick(widget.options_toggle, Qt.Key.Key_Tab)
    expected = widget.generate_button if kind == "package" else widget.analyze_button
    assert expected.hasFocus()
    QTest.keyClick(expected, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert widget.options_toggle.hasFocus()


@pytest.mark.parametrize("size", [(960, 640), (1280, 820), (1600, 1000), "maximized"])
def test_primary_actions_results_and_footer_remain_accessible_at_supported_sizes(page, tmp_path, size):
    app, window, widget, kind = page
    shell = window.page_shells[window.page_index(widget)]
    if size == "maximized":
        window.showMaximized()
        assert window.isMaximized()
    else:
        window.resize(*size)
    for state in ("empty", "loaded", "result", "expanded"):
        if state == "loaded":
            load(page, tmp_path)
        elif state == "result":
            widget.summary.setPlainText("Synthetic aggregate report\n" * 100)
        elif state == "expanded":
            widget.options_toggle.setChecked(True)
        settle(app)
        assert_workspace(widget, kind)
        if size in ((1280, 820), (1600, 1000)):
            assert shell.scroll_area.verticalScrollBar().maximum() == 0
            assert shell.scroll_area.horizontalScrollBar().maximum() == 0
        for control in (widget.select_button, widget.options_toggle, widget.generate_button,
                        widget.summary, widget.status_label):
            shell.scroll_area.ensureWidgetVisible(control)
            settle(app)
            top = control.mapTo(shell.scroll_area.viewport(), QPoint()).y()
            if control is widget.summary:
                assert top < shell.scroll_area.viewport().height() and top + control.height() > 0
            else:
                assert 0 <= top <= shell.scroll_area.viewport().height() - control.height()
        if state in ("result", "expanded"):
            assert widget.summary.verticalScrollBar().maximum() > 0
        if kind != "html" and state != "empty":
            assert widget.table.verticalScrollBar().maximum() > 0


def test_real_workers_preserve_results_sources_and_password_lifecycle(page, tmp_path):
    app, window, widget, kind = page
    if kind == "package":
        package_directory = tmp_path / "package-fixture"
        package_directory.mkdir()
        _, config, package, _, destination = make_case(package_directory)
        source = config.source_path
        widget.load_csv(str(source))
        widget._checkboxes[0].setChecked(True)
        widget._checkboxes[1].setChecked(True)
        widget.package_controls.path.setText(str(package))
        widget.package_controls.password.setText(PASSWORD)
    else:
        seed_vault(page[1])
        source = load(page, tmp_path, columns=2)
        destination = tmp_path / ("restored.html" if kind == "html" else "restored.csv")
        if kind == "csv":
            widget._checkboxes[0].setChecked(True)
        widget.start_analysis()
        worker = widget._worker
        assert worker is not None and worker.wait(15000)
        settle(app)
        assert widget._worker is None and widget.summary.toPlainText()
    original = source.read_bytes()
    widget.start_restoration(destination, overwrite=False)
    worker = widget._worker
    assert worker is not None and not widget.select_button.isEnabled()
    assert worker.wait(15000)
    settle(app)
    assert widget._worker is None and not widget.has_running_worker()
    assert widget._last_error is None and destination.is_file()
    assert source.read_bytes() == original
    assert widget.open_folder_button.isVisible() and widget.generate_button.isEnabled()
    assert widget.summary.maximumHeight() == 16_777_215
    assert "sucesso" in widget.status_label.text()
    assert "Synthetic Person" not in widget.summary.toPlainText()
    assert_workspace(widget, kind)
    shell = window.page_shells[window.page_index(widget)]
    assert shell.scroll_area.verticalScrollBar().maximum() == 0
    assert shell.scroll_area.horizontalScrollBar().maximum() == 0
    if kind == "package":
        assert not widget.package_controls.password.text()
        assert not widget.package_controls.show_password.isChecked()
        assert widget.package_controls.password.echoMode() is QLineEdit.EchoMode.Password
        assert PASSWORD not in widget.summary.toPlainText() + widget.status_label.text()
    if kind != "html":
        assert widget.progress_bar.maximum() == widget.progress_bar.value() == 1
