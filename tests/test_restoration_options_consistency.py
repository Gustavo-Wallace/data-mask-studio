"""HTML and batch disclosures preserve the visible workspace and existing policies."""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.app import create_application
from data_mask_studio.batch_restoration import BatchMissingCodePolicy, BatchRestorationStatus
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.html_restoration_widget import HTMLRestorationWidget
from data_mask_studio.gui.html_restoration_worker import HTMLRestorationWorker
from data_mask_studio.gui.visual_tokens import COLORS, METRICS
from data_mask_studio.html_restoration import HTMLMissingCodePolicy, HTMLRestorationError
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import MappingCandidate
from test_main_navigation import build_window


CODE = TokenGenerator(b"H" * 32).generate("PERSON", "Synthetic Person")


@pytest.fixture(params=["html", "batch"])
def page(request, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated"))
    app = create_application([])
    window = build_window(tmp_path)
    widget = window.html_restoration_widget if request.param == "html" else window.batch_restoration_widget
    source = tmp_path / ("synthetic.html" if request.param == "html" else "synthetic.csv")
    source.write_text(
        f"<p>{CODE}</p>" if request.param == "html" else f"Name,Kind\n{CODE},SYNTHETIC\n",
        encoding="utf-8",
    )
    window.set_current_page(window.page_index(widget))
    window.show()
    app.processEvents()
    window.resize(1100, 760)
    app.processEvents()
    return app, window, widget, source


def load(page):
    app, _, widget, source = page
    if isinstance(widget, HTMLRestorationWidget):
        widget.load_html(str(source))
    else:
        widget.add_paths([source])
    app.processEvents()
    return widget


def values(widget):
    return widget.missing_policy_combo.currentData(), widget.representation_combo.currentData()


def assert_primary_workspace_present(widget):
    if isinstance(widget, HTMLRestorationWidget):
        controls = (
            widget.select_button, widget.path_field, widget.file_name_label, widget.encoding_label,
            widget.analyze_button, widget.generate_button, widget.summary, widget.status_label,
        )
    else:
        controls = (
            widget.add_files_button, widget.add_folder_button, widget.remove_button, widget.clear_button,
            widget.file_table, widget.column_table, widget.select_candidates_button, widget.unselect_columns_button,
            widget.output_field, widget.choose_output_button, widget.analyze_button, widget.start_button,
            widget.current_progress, widget.overall_progress, widget.current_progress_label,
            widget.summary_output, widget.status_label,
        )
    assert all(control.isVisible() for control in controls)


def seed_vault(window):
    vault = window._vault_repository_factory()
    with vault.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(
            CODE, "PERSON", "  Synthetic Person  ", "Name", canonical_value="Synthetic Person",
            normalization_rule=NormalizationRule.COLLAPSE_WHITESPACE,
        )])


def analyze_batch(page):
    app, _, widget, _ = page
    widget.analyze_button.click()
    worker = widget._analysis_worker
    assert worker is not None and not widget.options_toggle.isEnabled()
    assert worker.wait(5000)
    app.processEvents()
    assert widget._analysis_worker is None and widget.options_toggle.isEnabled()
    widget.file_table.selectRow(0)
    app.processEvents()


def test_initial_workspace_is_present_with_options_collapsed_and_safe_summary(page):
    _, window, widget, _ = page
    assert_primary_workspace_present(widget)
    assert widget.options_toggle.isVisible() and widget.options_toggle.isEnabled()
    assert widget.options_toggle.text() == "Opções de restauração"
    assert widget.options_toggle.accessibleName() == "Mostrar ou ocultar opções de restauração"
    assert not widget.options_toggle.isChecked() and not widget.options_controls.isVisible()
    assert widget.options_summary.isVisible() and widget.options_summary.wordWrap()
    assert widget.options_summary.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert widget.options_summary.text() == window.restoration_widget.options_summary.text()
    assert COLORS["muted_text"] in widget.options_summary.styleSheet()
    assert f"{METRICS['description_font_size']}px" in widget.options_summary.styleSheet()
    assert values(widget) == ("keep", RepresentationPolicy.FIRST_ORIGINAL.value)
    assert widget.analyze_button.property("role") == widget.options_toggle.property("role") == "secondary"
    assert not widget.analyze_button.isEnabled()
    assert not hasattr(widget, "package_controls")
    if isinstance(widget, HTMLRestorationWidget):
        assert widget._inspection is None and widget.path_field.isReadOnly()
        assert not widget.path_field.text() and widget.file_name_label.text() == "—"
        assert not widget.missing_policy_combo.isEnabled() and not widget.representation_combo.isEnabled()
        assert not widget.generate_button.isEnabled() and widget.generate_button.property("role") == "primary"
    else:
        assert widget.file_table.rowCount() == widget.column_table.rowCount() == 0
        assert widget.output_field.isEnabled() and widget.choose_output_button.isEnabled()
        assert widget.missing_policy_combo.isEnabled() and widget.representation_combo.isEnabled()
        assert not widget.start_button.isEnabled() and widget.start_button.property("role") == "primary"


def test_expanding_and_collapsing_preserve_all_existing_policy_combinations(page):
    widget = load(page)
    expected_missing = {"keep", "abort"} if isinstance(widget, HTMLRestorationWidget) else {"keep", "abort_file", "abort_batch"}
    assert {widget.missing_policy_combo.itemData(index) for index in range(widget.missing_policy_combo.count())} == expected_missing
    for missing in range(widget.missing_policy_combo.count()):
        for representation in range(widget.representation_combo.count()):
            widget.options_toggle.setChecked(True)
            assert widget.options_controls.isVisible() and not widget.options_summary.isVisible()
            assert widget.missing_policy_combo.isVisible() and widget.representation_combo.isVisible()
            assert widget.missing_policy_combo.isEnabled() and widget.representation_combo.isEnabled()
            widget.missing_policy_combo.setCurrentIndex(missing)
            widget.representation_combo.setCurrentIndex(representation)
            selected = values(widget)
            widget.options_toggle.setChecked(False)
            assert values(widget) == selected
            assert widget.options_summary.isVisible() and not widget.options_controls.isVisible()
            assert widget.missing_policy_combo.currentText() in widget.options_summary.text()
            assert widget.representation_combo.currentText() in widget.options_summary.text()
            assert "pacote" not in widget.options_summary.text()
            assert_primary_workspace_present(widget)


def test_disclosure_does_not_steal_focus_and_tab_skips_hidden_options(page):
    app, _, _, _ = page
    widget = load(page)
    widget.options_toggle.setFocus()
    widget.options_toggle.click()
    app.processEvents()
    assert widget.options_toggle.hasFocus() and widget.options_controls.isVisible()
    first, second = ((widget.missing_policy_combo, widget.representation_combo)
                     if isinstance(widget, HTMLRestorationWidget)
                     else (widget.representation_combo, widget.missing_policy_combo))
    QTest.keyClick(widget.options_toggle, Qt.Key.Key_Tab)
    assert first.hasFocus()
    QTest.keyClick(first, Qt.Key.Key_Tab)
    assert second.hasFocus()
    widget.options_toggle.setChecked(False)
    app.processEvents()
    assert widget.options_toggle.hasFocus()
    QTest.keyClick(widget.options_toggle, Qt.Key.Key_Tab)
    assert widget.analyze_button.hasFocus()
    QTest.keyClick(widget.analyze_button, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert widget.options_toggle.hasFocus()
    primary = widget.select_button if isinstance(widget, HTMLRestorationWidget) else widget.output_field
    primary.setFocus()
    widget.options_toggle.setChecked(True)
    app.processEvents()
    assert primary.hasFocus()


@pytest.mark.parametrize("page", ["html"], indirect=True)
def test_html_selection_reselection_and_cancel_preserve_options(page, tmp_path, monkeypatch):
    app, _, widget, source = page
    widget.options_toggle.click()
    widget.missing_policy_combo.setCurrentIndex(1)
    widget.representation_combo.setCurrentIndex(1)
    selected = values(widget)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "HTML"))
    widget.select_button.click()
    app.processEvents()
    assert widget.file_name_label.text() == source.name and widget.path_field.text() == str(source)
    assert widget.encoding_label.text() == "utf-8" and widget.generate_button.isEnabled()
    other = tmp_path / "other.htm"
    other.write_text("<p>Synthetic text</p>", encoding="utf-8")
    widget.load_html(str(other))
    assert widget._inspection.path == other and values(widget) == selected
    assert widget.options_controls.isVisible()
    before = widget._inspection
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", "HTML"))
    widget.select_button.click()
    assert widget._inspection == before and values(widget) == selected
    widget.load_html(str(tmp_path / "missing.html"))
    assert widget._inspection == before and values(widget) == selected
    assert_primary_workspace_present(widget)


@pytest.mark.parametrize("page", ["html"], indirect=True)
def test_html_analysis_remains_available_with_options_collapsed(page):
    app, window, _, _ = page
    seed_vault(window)
    widget = load(page)
    widget.analyze_button.click()
    worker = widget._worker
    assert worker is not None and not widget.options_toggle.isEnabled()
    assert widget.progress_bar.isVisible() and widget.cancel_button.isVisible()
    assert worker.wait(5000)
    app.processEvents()
    assert widget._worker is None and widget.options_toggle.isEnabled()
    assert "Códigos existentes no cofre: 1" in widget.summary.toPlainText()
    assert widget.generate_button.isEnabled() and widget.generate_button.property("role") == "primary"
    assert not widget.options_controls.isVisible() and widget.options_summary.isVisible()


@pytest.mark.parametrize("page", ["html"], indirect=True)
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_html_restoration_uses_hidden_selected_policies(page, tmp_path, representation):
    app, window, _, _ = page
    seed_vault(window)
    widget = load(page)
    widget.options_toggle.click()
    widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(HTMLMissingCodePolicy.ABORT.value))
    widget.representation_combo.setCurrentIndex(widget.representation_combo.findData(representation.value))
    widget.options_toggle.click()
    selected = values(widget)
    output = tmp_path / "restored.html"
    widget.start_restoration(output, overwrite=False)
    worker = widget._worker
    assert worker is not None
    assert worker._missing_code_policy is HTMLMissingCodePolicy.ABORT
    assert worker._representation_policy is representation
    assert worker.wait(5000)
    app.processEvents()
    expected = "  Synthetic Person  " if representation is RepresentationPolicy.FIRST_ORIGINAL else "Synthetic Person"
    assert output.read_text(encoding="utf-8") == f"<p>{expected}</p>"
    assert widget._worker is None and widget._last_error is None
    assert widget.open_folder_button.isVisible() and "sucesso" in widget.status_label.text()
    assert widget.options_toggle.isEnabled() and not widget.options_controls.isVisible()
    assert values(widget) == selected


@pytest.mark.parametrize("page", ["html"], indirect=True)
@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_html_failed_or_cancelled_worker_restores_disclosure_enablement(page, tmp_path, monkeypatch, outcome):
    app, _, _, _ = page
    widget = load(page)
    widget.options_toggle.click()
    widget.missing_policy_combo.setCurrentIndex(1)
    selected = values(widget)
    monkeypatch.setattr(HTMLRestorationWorker, "start", lambda self: None)
    widget.start_restoration(tmp_path / "unused.html", overwrite=False)
    worker = widget._worker
    assert worker is not None and not widget.options_toggle.isEnabled()
    assert not widget.missing_policy_combo.isEnabled() and not widget.representation_combo.isEnabled()
    if outcome == "failure":
        worker.failed.emit(HTMLRestorationError("Erro sintético seguro."))
    else:
        worker.cancelled.emit()
    worker.finished.emit()
    app.processEvents()
    assert widget._worker is None and widget.options_toggle.isEnabled()
    assert widget.options_controls.isVisible() and values(widget) == selected
    assert widget.missing_policy_combo.isEnabled() and widget.representation_combo.isEnabled()
    assert widget.progress_bar.isHidden() and not widget.cancel_button.isVisible()


@pytest.mark.parametrize("page", ["batch"], indirect=True)
def test_batch_queue_analysis_review_output_and_clear_remain_visible(page, tmp_path, monkeypatch):
    app, window, widget, source = page
    seed_vault(window)
    widget.options_toggle.click()
    widget.missing_policy_combo.setCurrentIndex(2)
    widget.representation_combo.setCurrentIndex(1)
    selected = values(widget)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(source)], "CSV"))
    widget.add_files_button.click()
    assert widget.analyze_button.isEnabled() and not widget.start_button.isEnabled()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(output))
    widget.choose_output_button.click()
    assert widget.output_field.text() == str(output) and not widget.start_button.isEnabled()
    analyze_batch(page)
    assert widget.files[0].status is BatchRestorationStatus.COMPATIBLE
    assert widget.file_table.item(0, 2).text() == "utf-8" and widget.column_table.rowCount() == 2
    widget.unselect_columns_button.click()
    assert not widget.start_button.isEnabled()
    widget.select_candidates_button.click()
    assert widget.files[0].columns[0].selected and not widget.files[0].columns[1].selected
    assert widget.start_button.isEnabled() and widget.start_button.property("role") == "primary"
    assert values(widget) == selected and widget.options_controls.isVisible()
    assert_primary_workspace_present(widget)
    widget.clear_button.click()
    app.processEvents()
    assert widget.file_table.rowCount() == widget.column_table.rowCount() == 0
    assert not widget.analyze_button.isEnabled() and not widget.start_button.isEnabled()
    assert values(widget) == selected and widget.output_field.text() == str(output)
    assert_primary_workspace_present(widget)


@pytest.mark.parametrize("page", ["batch"], indirect=True)
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_batch_real_restoration_uses_hidden_options_and_preserves_column_review(page, tmp_path, monkeypatch, representation):
    app, window, _, source = page
    seed_vault(window)
    widget = load(page)
    widget.options_toggle.click()
    widget.representation_combo.setCurrentIndex(widget.representation_combo.findData(representation.value))
    widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(BatchMissingCodePolicy.ABORT_FILE.value))
    widget.options_toggle.click()
    selected = values(widget)
    analyze_batch(page)
    widget.select_candidates_button.click()
    column_selection = [column.selected for column in widget.files[0].columns]
    output = tmp_path / "output"
    output.mkdir()
    widget.output_field.setText(str(output))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.start_button.click()
    worker = widget._processing_worker
    assert worker is not None and not widget.options_toggle.isEnabled()
    assert worker._options.missing_code_policy is BatchMissingCodePolicy.ABORT_FILE
    assert worker._options.representation_policy is representation
    assert worker.wait(5000)
    app.processEvents()
    item = widget.files[0]
    assert item.status is BatchRestorationStatus.COMPLETED and item.path == source
    with item.output_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    expected = "  Synthetic Person  " if representation is RepresentationPolicy.FIRST_ORIGINAL else "Synthetic Person"
    assert rows[1] == [expected, "SYNTHETIC"]
    assert [column.selected for column in item.columns] == column_selection
    assert widget.options_toggle.isEnabled() and not widget.options_controls.isVisible()
    assert values(widget) == selected and widget.open_output_button.isEnabled()
    assert "Concluídos: 1" in widget.summary_output.toPlainText()
    assert_primary_workspace_present(widget)


def test_closed_option_combos_do_not_consume_page_scrolling_and_popup_remains_usable(page):
    app, window, _, _ = page
    widget = load(page)
    widget.options_toggle.click()
    assert all(isinstance(combo, ScrollSafeComboBox) for combo in (
        widget.missing_policy_combo, widget.representation_combo,
    ))
    window.resize(960, 640)
    app.processEvents()
    shell = window.page_shells[window.page_index(widget)]
    area = shell.scroll_area
    for combo in (widget.missing_policy_combo, widget.representation_combo):
        before = combo.currentIndex()
        area.ensureWidgetVisible(combo)
        app.processEvents()
        scroll_before = area.verticalScrollBar().value()
        delta = QPoint(0, 120) if scroll_before else QPoint(0, -120)
        QTest.wheelEvent(window.windowHandle(), combo.mapTo(window, combo.rect().center()), delta)
        app.processEvents()
        assert combo.currentIndex() == before
        if area.verticalScrollBar().maximum() > 0:
            assert area.verticalScrollBar().value() != scroll_before
        area.ensureWidgetVisible(combo)
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert combo.view().isVisible()
        QTest.keyClick(combo.view(), Qt.Key.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key.Key_Return)
        assert combo.currentIndex() != before
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    app.processEvents()
    primary = widget.generate_button if isinstance(widget, HTMLRestorationWidget) else widget.start_button
    for control in (primary, widget.status_label):
        top = control.mapTo(area.viewport(), QPoint(0, 0)).y()
        assert 0 <= top and top + control.height() <= area.viewport().height()
