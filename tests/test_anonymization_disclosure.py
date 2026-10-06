"""Only profiles collapse; the pre-4A CSV page and its workflows remain present."""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLabel, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import AnonymizationResult, ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
from data_mask_studio.detection import DetectionError
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.gui.detection_worker import DetectionWorker
from data_mask_studio.normalization import NormalizationRule
from test_composite_gui import create as create_composite
from test_main_navigation import build_window


@pytest.fixture
def page(tmp_path):
    app = create_application([])
    window = build_window(tmp_path)
    source = tmp_path / "synthetic.csv"
    source.write_text("NOME,CPF,IDADE\nSynthetic Person,999.999.999-99,24\nOther Person,888.888.888-88,30\n",
                      encoding="utf-8")
    window.show()
    app.processEvents()
    window.resize(1100, 760)
    app.processEvents()
    return app, window, window.anonymization_widget, source


def load(page):
    app, _, widget, source = page
    widget.load_csv(str(source))
    app.processEvents()
    return widget


def assert_page_sections_present(widget):
    controls = (
        widget.select_button, widget.clear_button, widget.analyze_button,
        widget.file_name_label, widget.path_field, widget.encoding_label,
        widget.delimiter_label, widget.column_count_label,
        widget.select_all_button, widget.unselect_all_button, widget.selected_count_label,
        widget.config_table, widget.composite_section, widget.transfer_controls.checkbox,
        widget.validate_button, widget.generate_button, widget.status_label,
    )
    assert all(control.isVisible() for control in controls)
    assert any(label.text() == "Configuração das colunas" and label.isVisible()
               for label in widget.findChildren(QLabel))


def test_initial_page_sections_remain_present_with_profiles_collapsed(page):
    app, _, widget, _ = page
    assert_page_sections_present(widget)
    assert widget._inspection_result is None
    assert widget.select_button.isEnabled()
    assert widget.status_label.text() == "Selecione um arquivo CSV para começar."
    assert widget.path_field.isReadOnly() and not widget.path_field.text()
    assert widget.path_field.placeholderText() == "Nenhum arquivo selecionado"
    assert all(label.text() == "—" for label in (
        widget.file_name_label, widget.encoding_label, widget.delimiter_label, widget.column_count_label,
    ))
    assert widget.config_table.rowCount() == 0 and widget.config_table.isEnabled()
    assert not widget.clear_button.isEnabled() and not widget.analyze_button.isEnabled()
    assert not widget.select_all_button.isEnabled() and not widget.unselect_all_button.isEnabled()
    assert not widget.composite_section.isEnabled() and not widget.transfer_controls.checkbox.isEnabled()
    assert not widget.transfer_controls.fields.isVisible()
    assert widget.profile_toggle.isVisible() and widget.profile_toggle.isEnabled()
    assert widget.profile_toggle.text() == "Perfis de configuração (opcional)"
    assert widget.profile_toggle.accessibleName() == "Mostrar ou ocultar perfis de configuração"
    assert not widget.profile_toggle.isChecked() and not widget.profile_controls.isVisible()
    assert not widget.validate_button.isEnabled() and not widget.generate_button.isEnabled()
    widget.path_field.setFocus()
    QTest.keyClick(widget.path_field, Qt.Key.Key_Tab)
    app.processEvents()
    assert widget.profile_toggle.hasFocus()
    QTest.keyClick(widget.profile_toggle, Qt.Key.Key_Tab)
    app.processEvents()
    assert app.focusWidget() is not None and app.focusWidget().isVisible()
    assert not widget.profile_controls.isAncestorOf(app.focusWidget())


def test_profiles_expand_and_collapse_without_changing_the_initial_page(page):
    app, _, widget, _ = page
    widget.profile_toggle.click()
    app.processEvents()
    assert widget.profile_controls.isVisible() and widget.profile_toggle.text() == "Ocultar perfis"
    assert widget.profile_combo.isVisible() and widget.profile_combo.isEnabled()
    for button in (widget.apply_profile_button, widget.save_profile_button, widget.update_profile_button,
                   widget.rename_profile_button, widget.delete_profile_button):
        assert button.isVisible() and not button.isEnabled()
    assert_page_sections_present(widget)
    widget.profile_combo.setFocus()
    widget.profile_toggle.setChecked(False)
    app.processEvents()
    assert widget.profile_toggle.hasFocus() and not widget.profile_controls.isVisible()
    assert widget.config_table.rowCount() == 0 and widget._inspection_result is None
    assert_page_sections_present(widget)


def test_csv_picker_updates_existing_sections_with_profiles_still_collapsed(page, monkeypatch):
    app, _, widget, source = page
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "CSV"))
    widget.select_button.click()
    app.processEvents()
    assert widget._inspection_result.path == source
    assert_page_sections_present(widget)
    assert widget.path_field.isReadOnly()
    assert widget.file_name_label.text() == source.name
    assert widget.path_field.text() == widget.path_field.toolTip() == str(source)
    assert widget.encoding_label.text() == "utf-8"
    assert widget.delimiter_label.text() == "Vírgula (,)"
    assert widget.column_count_label.text() == "3"
    assert widget.clear_button.isVisible() and widget.analyze_button.isVisible()
    assert widget.config_table.isVisible() and widget.config_table.rowCount() == 3
    assert widget.composite_section.isVisible() and widget.composite_section.add_button.isEnabled()
    assert widget.profile_toggle.isVisible() and widget.profile_toggle.isEnabled()
    assert not widget.profile_controls.isVisible() and widget.profile_combo.isEnabled()
    assert widget.validate_button.isEnabled() and not widget.generate_button.isEnabled()
    assert widget.transfer_controls.isVisible() and not widget.transfer_controls.checkbox.isEnabled()


def test_validation_and_existing_package_specific_disclosure_are_preserved(page):
    widget = load(page)
    widget.validate_button.click()
    assert widget._configuration_validated and widget.generate_button.isEnabled()
    assert widget.transfer_controls.isVisible() and not widget.transfer_controls.checkbox.isEnabled()
    widget.select_all_columns()
    assert not widget._configuration_validated and not widget.generate_button.isEnabled()
    controls = widget.transfer_controls
    assert controls.isVisible() and controls.checkbox.isEnabled()
    assert controls.checkbox.text() == "Gerar pacote de transferência"
    assert not controls.fields.isVisible() and not controls.fields.isEnabled()
    widget.validate_button.click()
    assert widget.generate_button.isEnabled()
    controls.checkbox.setChecked(True)
    assert controls.fields.isVisible() and controls.password.isEnabled()
    widget.unselect_all_columns()
    assert controls.isVisible() and not controls.checkbox.isChecked()
    assert not controls.checkbox.isEnabled() and not controls.fields.isEnabled()


def test_composite_mask_enables_package_without_any_scalar_mask(page):
    widget = load(page)
    create_composite(widget, mask=False)
    assert widget.composite_section.table.isVisible()
    assert widget.transfer_controls.isVisible() and not widget.transfer_controls.checkbox.isEnabled()
    widget.composite_section.table.cellWidget(0, 0).setCurrentIndex(1)
    widget.composite_section.table.cellWidget(0, 3).setText("PAIR")
    assert not any(column.anonymize for column in widget._column_configs)
    assert widget.transfer_controls.isVisible() and widget.transfer_controls.checkbox.isEnabled()
    widget.validate_button.click()
    assert widget.generate_button.isEnabled()


def test_profiles_remain_manageable_without_csv_and_apply_after_selection(page, monkeypatch):
    app, _, widget, source = page
    profile = widget._profile_service.create("Synthetic profile", [
        ColumnConfig("NOME"), ColumnConfig("CPF", True, "DOC", NormalizationRule.CPF),
        ColumnConfig("IDADE"),
    ], inspection=inspect_csv(source))
    widget.refresh_profiles(profile.identifier)
    assert widget.profile_toggle.isVisible() and not widget.profile_controls.isVisible()
    widget.profile_toggle.click()
    assert widget.profile_controls.isVisible() and widget.profile_combo.isEnabled()
    assert not widget.apply_profile_button.isEnabled() and not widget.save_profile_button.isEnabled()
    assert widget.rename_profile_button.isEnabled() and widget.delete_profile_button.isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed synthetic profile", True))
    widget.rename_profile_button.click()
    assert widget.profile_combo.currentText() == "Renamed synthetic profile"
    load(page)
    assert not widget.profile_toggle.isChecked() and not widget.profile_controls.isVisible()
    assert widget.profile_combo.currentData() == profile.identifier
    widget.profile_toggle.click()
    widget.apply_profile_button.click()
    app.processEvents()
    assert widget._column_configs[1].prefix == "DOC"
    assert widget._column_configs[1].normalization_rule == NormalizationRule.CPF
    assert widget.generate_button.isEnabled() and widget.transfer_controls.isVisible()


def test_expanded_profile_controls_save_update_rename_and_delete_using_existing_service(page, monkeypatch):
    widget = load(page)
    widget.select_all_columns()
    widget.validate_button.click()
    widget.profile_toggle.click()
    names = iter((("Synthetic profile", True), ("Renamed synthetic profile", True)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(names))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.save_profile_button.click()
    identifier = widget.profile_combo.currentData()
    assert identifier is not None and widget.profile_controls.isVisible()
    assert all(button.isVisible() and button.isEnabled() for button in (
        widget.apply_profile_button, widget.save_profile_button, widget.update_profile_button,
        widget.rename_profile_button, widget.delete_profile_button,
    ))
    widget.rename_profile_button.click()
    widget._prefix_fields[1].setText("DOCUMENT")
    widget.validate_button.click()
    widget.update_profile_button.click()
    stored = widget._profile_service.list_profiles()[0]
    assert stored.identifier == identifier and stored.name == "Renamed synthetic profile"
    assert stored.columns[1].prefix == "DOCUMENT"
    widget.profile_toggle.click()
    assert not widget.profile_controls.isVisible() and widget.profile_combo.currentData() == identifier
    widget.profile_toggle.click()
    widget.delete_profile_button.click()
    assert widget._profile_service.list_profiles() == [] and widget.profile_combo.count() == 0
    assert widget.profile_controls.isVisible() and not widget.delete_profile_button.isEnabled()
    assert widget.save_profile_button.isEnabled()


def test_collapsing_profiles_preserves_values_and_restores_keyboard_focus(page):
    app, _, _, _ = page
    widget = load(page)
    widget.select_all_columns()
    widget._prefix_fields[0].setText("MANUAL")
    widget.profile_toggle.click()
    widget.save_profile_button.setFocus()
    assert widget.save_profile_button.hasFocus()
    widget.profile_toggle.setChecked(False)
    app.processEvents()
    assert widget.profile_toggle.hasFocus() and not widget.profile_controls.isVisible()
    assert widget._prefix_fields[0].text() == "MANUAL" and widget._configuration_dirty
    QTest.keyClick(widget.profile_toggle, Qt.Key.Key_Tab)
    app.processEvents()
    assert widget.select_all_button.hasFocus()
    QTest.keyClick(widget.select_all_button, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    app.processEvents()
    assert widget.profile_toggle.hasFocus()
    widget.profile_toggle.click()
    assert widget.profile_controls.isVisible() and widget._prefix_fields[0].text() == "MANUAL"


def test_clear_keeps_original_sections_present_and_resets_inputs_and_profiles(page):
    app, _, _, _ = page
    widget = load(page)
    widget.select_all_columns()
    create_composite(widget)
    widget.profile_toggle.click()
    controls = widget.transfer_controls
    controls.checkbox.setChecked(True)
    controls.destination.setText("synthetic-custom.dmspackage")
    controls.password.setText("synthetic password")
    controls.confirmation.setText("synthetic password")
    controls.show_passwords.setChecked(True)
    widget._prefix_fields[0].setFocus()
    widget.clear_selection()
    app.processEvents()
    assert widget._inspection_result is None and widget.config_table.rowCount() == 0
    assert_page_sections_present(widget)
    assert widget.composite_section.configurations == () and not widget.composite_section.isEnabled()
    assert widget.config_table.isEnabled() and not widget.path_field.text()
    assert all(label.text() == "—" for label in (
        widget.file_name_label, widget.encoding_label, widget.delimiter_label, widget.column_count_label,
    ))
    assert not widget.clear_button.isEnabled() and not widget.analyze_button.isEnabled()
    assert not widget.validate_button.isEnabled() and not widget.save_profile_button.isEnabled()
    assert not widget.profile_toggle.isChecked() and not widget.profile_controls.isVisible()
    assert controls.isVisible() and not controls.fields.isVisible()
    assert not controls.destination.text() and controls.output_path is None
    assert not controls.password.text() and not controls.confirmation.text()
    assert not controls.show_passwords.isChecked() and not controls.checkbox.isEnabled()
    assert not widget.generate_button.isEnabled() and not widget.cancel_button.isVisible()


def test_loading_another_csv_returns_to_review_without_old_execution_options(page, tmp_path):
    widget = load(page)
    widget.select_all_columns()
    widget.validate_button.click()
    widget.profile_toggle.click()
    widget.transfer_controls.checkbox.setChecked(True)
    widget.transfer_controls.password.setText("synthetic password")
    other = tmp_path / "another.csv"
    other.write_text("NOME,CPF,IDADE\nSynthetic,123,20\n", encoding="utf-8")
    widget.load_csv(str(other))
    assert widget.file_name_label.text() == other.name and widget.config_table.isVisible()
    assert not widget._configuration_validated and not widget.generate_button.isEnabled()
    assert not widget.profile_toggle.isChecked() and not widget.profile_controls.isVisible()
    assert_page_sections_present(widget)
    assert not widget.transfer_controls.fields.isVisible()
    assert not widget.transfer_controls.password.text() and not widget.transfer_controls.checkbox.isChecked()
    assert all(not column.anonymize for column in widget._column_configs)


@pytest.mark.parametrize("failure", ["empty", "missing"])
def test_rejected_csv_keeps_existing_page_and_error_context(page, tmp_path, failure):
    widget = load(page)
    rejected = tmp_path / "rejected.csv"
    if failure == "empty":
        rejected.touch()
    widget.load_csv(str(rejected))
    assert widget._inspection_result is None and widget.config_table.rowCount() == 0
    assert_page_sections_present(widget)
    assert widget.path_field.text() == str(rejected)
    assert widget.file_name_label.text() == rejected.name and widget.encoding_label.text() == "—"
    assert widget.clear_button.isVisible() and widget.clear_button.isEnabled()
    assert not widget.validate_button.isEnabled() and not widget.generate_button.isEnabled()
    assert widget.status_label.text() and widget.status_label.isVisible()


@pytest.mark.parametrize("loaded", [False, True])
def test_cancelled_file_picker_preserves_current_disclosure_and_configuration(page, monkeypatch, loaded):
    _, _, widget, _ = page
    if loaded:
        load(page)
        widget.select_all_columns()
        widget.validate_button.click()
        widget.profile_toggle.click()
    before = widget._inspection_result, widget._configuration_validated, widget.profile_toggle.isChecked()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", "CSV"))
    widget.select_button.click()
    assert (widget._inspection_result, widget._configuration_validated, widget.profile_toggle.isChecked()) == before
    assert_page_sections_present(widget)
    assert "cancelada" in widget.status_label.text()


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_analysis_busy_and_terminal_states_keep_disclosure_consistent(page, monkeypatch, outcome):
    app, _, _, _ = page
    widget = load(page)
    monkeypatch.setattr(DetectionWorker, "start", lambda self: None)
    widget.analyze_button.click()
    worker = widget._detection_worker
    assert worker is not None
    assert widget.config_table.isVisible() and not widget.config_table.isEnabled()
    assert widget.analyze_button.text() == "Cancelar análise"
    assert widget.progress_bar.isVisible() and not widget.profile_toggle.isEnabled()
    if outcome == "failure":
        worker.failed.emit(DetectionError("Erro sintético de análise."))
    else:
        worker.cancelled.emit()
    worker.finished.emit()
    app.processEvents()
    assert widget._detection_worker is None and widget.config_table.isVisible() and widget.config_table.isEnabled()
    assert widget.analyze_button.text() == "Analisar colunas" and widget.profile_toggle.isEnabled()
    assert not widget.progress_bar.isVisible() and not widget.generate_button.isEnabled()


def test_real_analysis_shows_suggestions_without_automatically_changing_configuration(page):
    app, _, _, _ = page
    widget = load(page)
    widget.analyze_button.click()
    worker = widget._detection_worker
    assert worker is not None and worker.wait(5000)
    app.processEvents()
    assert widget._detection_dialog is not None and widget._detection_dialog.isVisible()
    assert len(widget._suggestions) == 3 and widget.config_table.isVisible()
    assert widget.validate_button.isEnabled() and not widget.generate_button.isEnabled()
    assert widget.transfer_controls.isVisible() and not widget.transfer_controls.checkbox.isEnabled()
    assert not widget.profile_controls.isVisible()
    assert not any(column.anonymize for column in widget._column_configs)


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_processing_states_keep_visible_configuration_and_final_actions(page, tmp_path, monkeypatch, outcome):
    app, _, _, _ = page
    widget = load(page)
    widget.select_all_columns()
    widget.validate_button.click()
    monkeypatch.setattr(AnonymizationWorker, "start", lambda self: None)
    output = tmp_path / "out.csv"
    widget._start_processing(output, overwrite=False)
    worker = widget._worker
    assert worker is not None and widget.config_table.isVisible() and not widget.config_table.isEnabled()
    assert not widget.select_button.isEnabled() and not widget.profile_toggle.isEnabled()
    assert widget.cancel_button.isVisible() and widget.cancel_button.isEnabled()
    assert widget.progress_bar.isVisible() and widget.processed_count_label.isVisible()
    assert widget.transfer_controls.isVisible() and not widget.transfer_controls.isEnabled()
    assert not widget.validate_button.isEnabled() and not widget.generate_button.isEnabled()
    if outcome == "success":
        worker.completed.emit(AnonymizationResult(output, 2, 0.1))
    elif outcome == "failure":
        worker.failed.emit(CSVAnonymizationError("Erro sintético seguro."))
    else:
        worker.cancelled.emit()
    worker.finished.emit()
    app.processEvents()
    assert widget._worker is None and widget.config_table.isVisible() and widget.config_table.isEnabled()
    assert widget.select_button.isEnabled() and widget.profile_toggle.isEnabled()
    assert widget.validate_button.isEnabled() and widget.generate_button.isEnabled()
    assert not widget.cancel_button.isVisible() and not widget.cancel_button.isEnabled()
    assert widget.output_path_label.isVisible() == (outcome == "success")
    assert widget.progress_bar.isVisible() == (outcome == "success")
    assert widget._inspection_result is not None and widget.status_label.text()


def test_visible_validate_generate_path_runs_real_worker_and_preserves_csv_values(page, tmp_path, monkeypatch):
    app, _, _, source = page
    widget = load(page)
    output = tmp_path / "preserved.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), "CSV"))
    widget.validate_button.click()
    widget.generate_button.click()
    worker = widget._worker
    assert worker is not None and worker.wait(5000)
    app.processEvents()
    assert output.is_file() and widget.output_path_label.isVisible() and widget.open_folder_button.isVisible()
    with source.open(encoding="utf-8-sig", newline="") as original, output.open(encoding="utf-8-sig", newline="") as generated:
        assert list(csv.reader(original)) == list(csv.reader(generated))
    assert widget.transfer_controls.isVisible() and not widget.transfer_controls.checkbox.isEnabled()
    assert widget._worker is None


@pytest.mark.parametrize("size", [(960, 640), (1100, 760)])
def test_expanded_optional_sections_keep_final_actions_and_status_reachable(page, size):
    app, window, _, _ = page
    widget = load(page)
    widget.select_all_columns()
    widget.validate_button.click()
    widget.profile_toggle.click()
    widget.transfer_controls.checkbox.setChecked(True)
    window.resize(*size)
    app.processEvents()
    area = window.page_shells[0].scroll_area
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    app.processEvents()
    assert widget.profile_controls.isVisible() and widget.transfer_controls.fields.isVisible()
    for control in (widget.validate_button, widget.generate_button, widget.status_label):
        top = control.mapTo(area.viewport(), QPoint(0, 0)).y()
        assert 0 <= top and top + control.height() <= area.viewport().height()
