"""Restore CSV keeps its workspace visible and collapses only secondary options."""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.restoration_worker import CSVRestorationWorker
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.restoration import (
    MissingCodePolicy,
    RepresentationPolicy,
    RestorationError,
    RestorationResult,
)
from data_mask_studio.vault import MappingCandidate
from test_main_navigation import build_window
from test_restoration_interface import CODE


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated"))
    app = create_application([])
    window = build_window(tmp_path)
    widget = window.restoration_widget
    window.set_current_page(window.page_index(widget))
    source = tmp_path / "synthetic.csv"
    source.write_text(f"CPF;Tipo\n{CODE};SYNTHETIC\n", encoding="utf-8")
    window.show()
    app.processEvents()
    window.resize(1100, 760)
    app.processEvents()
    return app, window, widget, source


def load(page):
    app, _, widget, source = page
    widget.load_csv(str(source))
    widget._checkboxes[0].setChecked(True)
    app.processEvents()
    return widget


def assert_primary_workspace_present(widget):
    assert all(control.isVisible() for control in (
        widget.source_combo, widget.select_button, widget.path_field,
        widget.file_name_label, widget.encoding_label, widget.delimiter_label,
        widget.select_all_button, widget.unselect_all_button, widget.selected_count_label,
        widget.table, widget.analyze_button, widget.generate_button, widget.summary, widget.status_label,
    ))


def test_initial_workspace_is_present_and_safe_defaults_remain_visible(page):
    _, _, widget, _ = page
    assert_primary_workspace_present(widget)
    assert widget.source_combo.currentData() == "vault"
    assert widget._inspection is None and widget.table.rowCount() == 0
    assert widget.table.accessibleDescription() == "Selecione um CSV anonimizado para revisar as colunas."
    assert widget.path_field.isReadOnly() and not widget.path_field.text()
    assert all(label.text() == "—" for label in (
        widget.file_name_label, widget.encoding_label, widget.delimiter_label,
    ))
    assert widget.select_button.isEnabled()
    assert not widget.analyze_button.isEnabled() and not widget.generate_button.isEnabled()
    assert widget.generate_button.property("role") == "primary"
    assert widget.analyze_button.property("role") == widget.options_toggle.property("role") == "secondary"
    assert widget.options_toggle.isVisible() and widget.options_toggle.isEnabled()
    assert not widget.options_toggle.isChecked() and not widget.options_controls.isVisible()
    assert not widget.missing_policy_combo.isEnabled() and not widget.representation_combo.isEnabled()
    assert widget.options_summary.isVisible() and widget.options_summary.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert "Manter código original" in widget.options_summary.text()
    assert "Primeira representação original" in widget.options_summary.text()
    assert widget.package_controls.isHidden() and not widget.package_controls.isEnabled()
    assert not widget.progress_bar.isVisible() and not widget.cancel_button.isVisible()


@pytest.mark.parametrize("missing", list(MissingCodePolicy))
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_options_disclosure_preserves_every_existing_policy(page, missing, representation):
    app, _, _, _ = page
    widget = load(page)
    widget.options_toggle.click()
    assert widget.options_controls.isVisible() and not widget.options_summary.isVisible()
    assert widget.options_toggle.text() == "Ocultar opções de restauração"
    assert widget.missing_policy_combo.isVisible() and widget.missing_policy_combo.isEnabled()
    assert widget.representation_combo.isVisible() and widget.representation_combo.isEnabled()
    widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(missing.value))
    widget.representation_combo.setCurrentIndex(widget.representation_combo.findData(representation.value))
    before = widget._configuration()
    widget.options_toggle.click()
    app.processEvents()
    assert not widget.options_controls.isVisible() and widget.options_summary.isVisible()
    assert widget.options_toggle.text() == "Opções de restauração"
    assert widget._configuration() == before
    assert before.missing_code_policy is missing and before.representation_policy is representation
    assert widget.missing_policy_combo.currentText() in widget.options_summary.text()
    assert widget.representation_combo.currentText() in widget.options_summary.text()
    widget.options_toggle.click()
    assert widget._configuration() == before
    assert_primary_workspace_present(widget)


def test_option_focus_and_tab_order_skip_collapsed_controls(page):
    app, _, _, _ = page
    widget = load(page)
    widget.options_toggle.setFocus()
    QTest.keyClick(widget.options_toggle, Qt.Key.Key_Tab)
    assert widget.analyze_button.hasFocus()
    QTest.keyClick(widget.analyze_button, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert widget.options_toggle.hasFocus()
    widget.options_toggle.click()
    widget.options_toggle.setFocus()
    QTest.keyClick(widget.options_toggle, Qt.Key.Key_Tab)
    assert widget.missing_policy_combo.hasFocus()
    QTest.keyClick(widget.missing_policy_combo, Qt.Key.Key_Tab)
    assert widget.representation_combo.hasFocus()
    widget.options_toggle.setChecked(False)
    app.processEvents()
    assert widget.options_toggle.hasFocus() and not widget.options_controls.isVisible()
    QTest.keyClick(widget.options_toggle, Qt.Key.Key_Tab)
    assert widget.analyze_button.hasFocus()


@pytest.mark.parametrize("expanded", [False, True])
def test_source_switch_preserves_local_options_and_shows_package_restrictions(page, expanded):
    app, _, widget, source = page
    load(page)
    widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(MissingCodePolicy.EMPTY.value))
    widget.representation_combo.setCurrentIndex(widget.representation_combo.findData(RepresentationPolicy.CANONICAL.value))
    widget.options_toggle.setChecked(expanded)
    widget.source_combo.setCurrentIndex(1)
    controls = widget.package_controls
    assert controls.isVisible() and controls.isEnabled()
    assert all(field.isVisible() for field in (controls.path, controls.browse, controls.password, controls.show_password))
    assert widget._configuration().missing_code_policy is MissingCodePolicy.ABORT
    assert widget.missing_policy_combo.currentData() == MissingCodePolicy.ABORT.value
    assert not widget.missing_policy_combo.isEnabled() and not widget.analyze_button.isEnabled()
    assert widget.representation_combo.isEnabled() and widget.generate_button.isEnabled()
    assert widget.options_controls.isVisible() == expanded
    if not expanded:
        assert "Interromper restauração" in widget.options_summary.text()
        assert "obrigatório no pacote" in widget.options_summary.text()
    controls.password.setText("synthetic secret")
    controls.show_password.setChecked(True)
    controls.password.setFocus()
    widget.source_combo.setCurrentIndex(0)
    app.processEvents()
    assert controls.isHidden() and not controls.isEnabled()
    assert not controls.password.text() and not controls.show_password.isChecked()
    assert app.focusWidget() is not None and app.focusWidget().isVisible()
    assert not controls.isAncestorOf(app.focusWidget())
    assert widget.missing_policy_combo.currentData() == MissingCodePolicy.EMPTY.value
    assert widget.representation_combo.currentData() == RepresentationPolicy.CANONICAL.value
    assert widget.missing_policy_combo.isEnabled() and widget.analyze_button.isEnabled()
    assert "obrigatório no pacote" not in widget.options_summary.text()
    assert widget._inspection.path == source and widget._checkboxes[0].isChecked()
    assert_primary_workspace_present(widget)


@pytest.mark.parametrize("package", [False, True])
def test_csv_selection_and_clear_keep_workspace_and_current_options(page, monkeypatch, package):
    app, _, widget, source = page
    widget.source_combo.setCurrentIndex(int(package))
    widget.options_toggle.click()
    widget.representation_combo.setCurrentIndex(1)
    if not package:
        widget.missing_policy_combo.setCurrentIndex(1)
    policies = widget.missing_policy_combo.currentData(), widget.representation_combo.currentData()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "CSV"))
    widget.select_button.click()
    app.processEvents()
    assert widget.table.rowCount() == 2 and widget.file_name_label.text() == source.name
    assert widget.path_field.text() == str(source) and widget.encoding_label.text() == "utf-8"
    assert widget.delimiter_label.text() == "Ponto e vírgula (;)"
    widget.select_all_button.click()
    assert all(checkbox.isChecked() for checkbox in widget._checkboxes)
    widget.unselect_all_button.click()
    assert not any(checkbox.isChecked() for checkbox in widget._checkboxes)
    widget.clear_selection()
    app.processEvents()
    assert_primary_workspace_present(widget)
    assert widget._inspection is None and widget.table.rowCount() == 0
    assert not widget.path_field.text() and widget.file_name_label.text() == "—"
    assert not widget.analyze_button.isEnabled() and not widget.generate_button.isEnabled()
    assert not widget.missing_policy_combo.isEnabled() and not widget.representation_combo.isEnabled()
    assert widget.options_controls.isVisible() and widget.options_toggle.isChecked()
    assert (widget.missing_policy_combo.currentData(), widget.representation_combo.currentData()) == policies
    assert widget.package_controls.isVisible() == package


def test_cancelled_picker_leaves_options_and_selected_columns_unchanged(page, monkeypatch):
    widget = load(page)
    widget.options_toggle.click()
    before = widget._configuration()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", "CSV"))
    widget.select_button.click()
    assert widget._configuration() == before and widget.options_controls.isVisible()
    assert_primary_workspace_present(widget)


def test_secondary_analysis_runs_real_worker_with_options_collapsed(page):
    app, window, _, _ = page
    widget = load(page)
    vault = window._vault_repository_factory()
    with vault.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(CODE, "CPF", "Synthetic original", "CPF")])
    widget.analyze_button.click()
    worker = widget._worker
    assert worker is not None and not widget.options_toggle.isEnabled()
    assert widget.progress_bar.isVisible() and widget.cancel_button.isVisible()
    assert worker.wait(5000)
    app.processEvents()
    assert widget._worker is None and widget.options_toggle.isEnabled()
    assert "Encontrados no cofre: 1" in widget.summary.toPlainText()
    assert widget.progress_bar.value() == widget.progress_bar.maximum() == 1
    assert widget.generate_button.isEnabled() and widget.generate_button.property("role") == "primary"
    assert not widget.options_controls.isVisible()


@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_real_local_restoration_uses_the_selected_hidden_representation(page, tmp_path, representation):
    app, window, _, _ = page
    widget = load(page)
    vault = window._vault_repository_factory()
    with vault.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(
            CODE, "CPF", "  Synthetic original  ", "CPF", canonical_value="Synthetic original",
            normalization_rule=NormalizationRule.COLLAPSE_WHITESPACE,
        )])
    widget.options_toggle.click()
    widget.representation_combo.setCurrentIndex(widget.representation_combo.findData(representation.value))
    widget.options_toggle.click()
    output = tmp_path / "restored.csv"
    widget.start_restoration(output, overwrite=False)
    worker = widget._worker
    assert worker is not None and worker.wait(5000)
    app.processEvents()
    assert widget._worker is None and widget._last_error is None
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    expected = "  Synthetic original  " if representation is RepresentationPolicy.FIRST_ORIGINAL else "Synthetic original"
    assert rows[1] == [expected, "SYNTHETIC"]
    assert "sucesso" in widget.status_label.text() and widget.open_folder_button.isVisible()
    assert not widget.options_controls.isVisible() and widget.options_toggle.isEnabled()


@pytest.mark.parametrize("package", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_worker_lifecycle_preserves_options_and_mode_restrictions(page, tmp_path, monkeypatch, package, outcome):
    app, _, _, _ = page
    widget = load(page)
    widget.options_toggle.click()
    widget.representation_combo.setCurrentIndex(1)
    widget.source_combo.setCurrentIndex(int(package))
    if package:
        path = tmp_path / "synthetic.dmspackage"
        path.write_bytes(b"synthetic signal-test input")
        widget.package_controls.path.setText(str(path))
        widget.package_controls.password.setText("synthetic secret")
    before = widget._configuration()
    monkeypatch.setattr(CSVRestorationWorker, "start", lambda self: None)
    output = tmp_path / "out.csv"
    widget.start_restoration(output, overwrite=False)
    worker = widget._worker
    assert worker is not None and not widget.options_toggle.isEnabled()
    assert not widget.source_combo.isEnabled() and not widget.package_controls.isEnabled()
    assert not widget.missing_policy_combo.isEnabled() and not widget.representation_combo.isEnabled()
    assert widget.cancel_button.isVisible() and widget.progress_bar.isVisible()
    if outcome == "success":
        worker.completed.emit(RestorationResult(
            output, 1, 1, 0, 1, 0, 0.1, before.missing_code_policy, before.representation_policy,
        ))
    elif outcome == "failure":
        worker.failed.emit(RestorationError("Erro sintético seguro."))
    else:
        worker.cancelled.emit()
    worker.finished.emit()
    app.processEvents()
    assert widget._worker is None and widget._configuration() == before
    assert widget.options_toggle.isEnabled() and widget.options_controls.isVisible()
    assert widget.source_combo.isEnabled() and widget.generate_button.isEnabled()
    assert widget.missing_policy_combo.isEnabled() == (not package)
    assert widget.analyze_button.isEnabled() == (not package)
    assert widget.package_controls.isEnabled() == package
    assert not widget.cancel_button.isVisible()
    assert widget.progress_bar.value() == (1 if outcome == "success" else 0)
    assert "synthetic secret" not in widget.status_label.text() + widget.summary.toPlainText()


def test_scroll_safe_options_and_package_controls_keep_footer_reachable(page):
    app, window, _, _ = page
    widget = load(page)
    widget.source_combo.setCurrentIndex(1)
    widget.options_toggle.click()
    assert all(isinstance(combo, ScrollSafeComboBox) for combo in (
        widget.source_combo, widget.missing_policy_combo, widget.representation_combo,
    ))
    window.resize(960, 640)
    app.processEvents()
    shell = window.page_shells[window.page_index(widget)]
    area = shell.scroll_area
    area.ensureWidgetVisible(widget.representation_combo)
    app.processEvents()
    combo = widget.representation_combo
    combo.setFocus()
    before = combo.currentIndex()
    scroll_before = area.verticalScrollBar().value()
    QTest.wheelEvent(window.windowHandle(), combo.mapTo(window, combo.rect().center()), QPoint(0, 120))
    app.processEvents()
    assert combo.currentIndex() == before
    assert area.verticalScrollBar().value() < scroll_before
    area.ensureWidgetVisible(combo)
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert combo.view().isVisible()
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentIndex() != before
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    app.processEvents()
    for control in (widget.generate_button, widget.status_label):
        top = control.mapTo(area.viewport(), QPoint(0, 0)).y()
        assert 0 <= top and top + control.height() <= area.viewport().height()
