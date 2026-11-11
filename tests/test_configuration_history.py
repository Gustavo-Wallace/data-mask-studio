"""Configuration history is local, bounded and independent of executed work."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import builtins
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QLineEdit, QMessageBox, QPlainTextEdit, QTextEdit, QVBoxLayout,
)

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.csv_tools.models import CSVHeaderReplacement, CSVInspectionResult
from data_mask_studio.detection import ColumnSuggestion, ConfidenceLevel, SuggestedType
from data_mask_studio.gui import anonymization_widget as module
from data_mask_studio.gui.configuration_history import ConfigurationHistory
from data_mask_studio.gui.styles import apply_widget_palette
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.profiles.models import ProfileColumn
from data_mask_studio.processing.planner import PlanningError
from test_column_search import page


@pytest.fixture
def gui(page):
    app, window, widget = page
    window.activateWindow()
    widget.select_all_button.setFocus()
    app.processEvents()
    assert window.isActiveWindow()
    return app, window, widget


def action(widget, row, value):
    field = widget._action_fields[row]
    field.setCurrentIndex(field.findData(value.value))


def normalize(widget, row, value):
    field = widget._normalization_fields[row]
    field.setCurrentIndex(field.findData(value.value))


def key(app, value):
    target = app.focusWidget()
    assert target is not None
    QTest.keyClick(target, value, Qt.KeyboardModifier.ControlModifier)
    QTest.keyRelease(target, Qt.Key.Key_Control)
    app.processEvents()


def seed(widget):
    action(widget, 0, ColumnAction.MASK)
    action(widget, 1, ColumnAction.EXCLUDE)
    widget.undo_configuration()
    assert widget._configuration_history.can_undo and widget._configuration_history.can_redo


@pytest.mark.parametrize("value", [ColumnAction.MASK, ColumnAction.EXCLUDE])
def test_individual_action_restores_dependent_prefix_widgets_and_review_flags(gui, value):
    app, _, widget = gui
    widget._unreviewed_sources = {0, 2}
    before = widget._configuration_snapshot()
    fields = tuple(widget._action_fields + widget._prefix_fields + widget._output_name_fields + widget._normalization_fields)
    action(widget, 0, value)
    after = widget._configuration_snapshot()
    assert len(widget._configuration_history._undo) == 1
    assert after.manually_changed_rows == frozenset({0})
    assert after.unreviewed_sources == frozenset({2})
    if value is ColumnAction.MASK:
        assert widget._prefix_fields[0].text() == "NOME"
    widget.undo_configuration()
    assert widget._configuration_snapshot() == before
    assert widget._prefix_fields[0].text() == ""
    assert widget._action_fields[0].property("columnAction") == "preserve"
    assert not widget._prefix_fields[0].isEnabled() and widget._output_name_fields[0].isEnabled()
    assert widget._normalization_fields[0].isEnabled()
    with pytest.raises(PlanningError):
        widget._build_current_plan()  # Undo cannot silently approve unknown sources.
    widget.redo_configuration()
    assert widget._configuration_snapshot() == after
    assert fields == tuple(widget._action_fields + widget._prefix_fields + widget._output_name_fields + widget._normalization_fields)
    assert not widget._configuration_validated and not widget.generate_button.isEnabled()


def test_bulk_operations_are_single_steps_and_cover_filtered_and_excluded_rows(gui):
    _, _, widget = gui
    action(widget, 2, ColumnAction.EXCLUDE)
    widget._output_name_fields[0].setText("PERSON")  # Untracked text is a boundary.
    before = widget._configuration_snapshot()
    widget.column_search_field.setText("PERSON")
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData("preserve"))
    assert widget.config_table.isRowHidden(1) and widget.config_table.isRowHidden(2)
    widget.select_all_columns()
    masked = widget._configuration_snapshot()
    assert all(column.action is ColumnAction.MASK for column in widget._column_configs)
    assert len(widget._configuration_history._undo) == 1
    assert all(widget.config_table.isRowHidden(row) for row in range(4))
    widget.undo_configuration()
    assert widget._configuration_snapshot() == before
    assert not widget.config_table.isRowHidden(0)
    widget.redo_configuration()
    assert widget._configuration_snapshot() == masked
    widget.unselect_all_columns()
    assert len(widget._configuration_history._undo) == 2
    assert all(column.action is ColumnAction.PRESERVE for column in widget._column_configs)
    widget.undo_configuration()
    assert widget._configuration_snapshot() == masked
    assert widget.column_search_field.text() == "PERSON"
    assert widget.column_action_filter.currentData() == "preserve"


def test_normalization_order_redo_invalidation_and_noop_actions(gui):
    _, _, widget = gui
    original = widget._configuration_snapshot()
    normalize(widget, 0, NormalizationRule.COLLAPSE_WHITESPACE)
    normalized = widget._configuration_snapshot()
    action(widget, 1, ColumnAction.MASK)
    widget.undo_configuration()
    assert widget._configuration_snapshot() == normalized
    widget.undo_configuration()
    assert widget._configuration_snapshot() == original
    widget.redo_configuration()
    assert widget._configuration_snapshot() == normalized
    action(widget, 3, ColumnAction.EXCLUDE)
    assert not widget._configuration_history.can_redo
    count = len(widget._configuration_history._undo)
    action(widget, 3, ColumnAction.EXCLUDE)
    assert len(widget._configuration_history._undo) == count
    widget.redo_configuration()
    assert len(widget._configuration_history._undo) == count


def test_immutable_snapshots_bounded_to_thirty_and_fail_closed_on_untracked_changes(gui):
    _, _, widget = gui
    initial = widget._configuration_snapshot()
    with pytest.raises(FrozenInstanceError):
        initial.columns[0].prefix = "NO"
    history = ConfigurationHistory()
    states = [initial]
    for index in range(35):
        column = replace(initial.columns[0], output_name=str(index))
        state = replace(initial, columns=(column, *initial.columns[1:]), dirty=True)
        history.record(states[-1], state)
        states.append(state)
    assert len(history._undo) == 30
    current = states[-1]
    for expected in reversed(states[5:-1]):
        current = history.undo(current)
        assert current == expected
    assert current == states[5] and history.undo(current) is None
    for expected in states[6:]:
        current = history.redo(current)
        assert current == expected
    assert len(history._undo) == 30 and not history.can_redo
    assert history.undo(initial) is None
    assert not history.can_undo and not history.can_redo


def test_duplicate_and_generated_headers_use_physical_identity_and_csv_generation(gui, tmp_path):
    _, _, widget = gui
    inspection = CSVInspectionResult(tmp_path / "synthetic.csv", "utf-8", ",", ["Name", "Name", "column_3"],
                                     (CSVHeaderReplacement(3, "column_3"),))
    widget._show_result(inspection)
    before = widget._configuration_snapshot()
    action(widget, 1, ColumnAction.MASK)
    widget.undo_configuration()
    assert widget._configuration_snapshot() == before
    widget.redo_configuration()
    assert [column.action for column in widget._column_configs] == [ColumnAction.PRESERVE, ColumnAction.MASK, ColumnAction.PRESERVE]
    old = widget._configuration_snapshot()
    widget._show_result(inspection)
    current = widget._configuration_snapshot()
    assert old.generation != current.generation
    widget._restore_configuration_snapshot(old)
    assert widget._configuration_snapshot() == current and not widget._configuration_history.can_undo


@pytest.mark.parametrize("boundary", ["clear", "reload", "prefix", "output", "profile", "suggestion", "composite", "processing"])
def test_reset_boundaries_clear_both_stacks(gui, boundary):
    _, _, widget = gui
    seed(widget)
    if boundary == "clear":
        widget.clear_selection()
    elif boundary == "reload":
        widget.load_csv(str(widget._inspection_result.path))
    elif boundary == "prefix":
        widget._prefix_fields[0].setText("MANUAL")
    elif boundary == "output":
        widget._output_name_fields[0].setText("Renamed")
    elif boundary == "profile":
        widget._apply_profile_configurations(tuple(ProfileColumn(column.header, "", NormalizationRule.EXACT)
                                                   for column in widget._column_configs))
    elif boundary == "suggestion":
        widget._suggestions = (ColumnSuggestion("Nome", SuggestedType.UNKNOWN, False, "", NormalizationRule.EXACT,
                                               ConfidenceLevel.UNDEFINED, "Synthetic", 0, 0),)
        widget._apply_detection_rows((0,))
    elif boundary == "composite":
        composite = CompositeColumnConfig("PAIR", components=tuple(
            CompositeSource(source_ref_at(widget._inspection_result, index)) for index in (0, 1)))
        widget.composite_section.set_configurations((composite,), notify=True)
    else:
        widget._set_processing_state(True)
        widget._set_processing_state(False)
    assert not widget._configuration_history.can_undo and not widget._configuration_history.can_redo


@pytest.mark.parametrize("field_type", [QLineEdit, QTextEdit, QPlainTextEdit])
def test_native_text_undo_and_empty_editor_history_never_activate_configuration_shortcuts(gui, field_type):
    app, window, widget = gui
    seed(widget)
    before = widget._configuration_snapshot()
    field = field_type(widget)
    widget.layout().addWidget(field)
    field.show()
    field.setFocus()
    app.processEvents()
    undo = QSignalSpy(widget._undo_configuration_shortcut.activated)
    redo = QSignalSpy(widget._redo_configuration_shortcut.activated)
    assert not widget._undo_configuration_shortcut.isEnabled()
    assert not widget._redo_configuration_shortcut.isEnabled()
    key(app, Qt.Key.Key_Z)
    key(app, Qt.Key.Key_Y)
    QTest.keyClicks(field, "Synthetic")
    key(app, Qt.Key.Key_Z)
    assert (field.text() if isinstance(field, QLineEdit) else field.toPlainText()) == ""
    key(app, Qt.Key.Key_Y)
    assert (field.text() if isinstance(field, QLineEdit) else field.toPlainText()) == "Synthetic"
    assert undo.count() == redo.count() == 0
    assert widget._configuration_snapshot() == before


def test_actual_prefix_text_edits_keep_native_undo_while_resetting_configuration_history(gui):
    app, _, widget = gui
    action(widget, 0, ColumnAction.MASK)
    field = widget._prefix_fields[0]
    field.setFocus()
    field.selectAll()
    QTest.keyClicks(field, "MANUAL")
    assert not widget._configuration_history.can_undo
    key(app, Qt.Key.Key_Z)
    assert field.text() == "NOME"
    assert not widget._configuration_history.can_undo


def test_ctrl_z_y_page_isolation_busy_and_disabled_states(gui):
    app, window, widget = gui
    action(widget, 0, ColumnAction.MASK)
    current = widget._configuration_snapshot()
    window.navigation.buttons[0].setFocus()
    key(app, Qt.Key.Key_Z)
    assert widget._column_configs[0].action is ColumnAction.PRESERVE
    key(app, Qt.Key.Key_Y)
    assert widget._configuration_snapshot() == current
    widget._set_detection_state(True)
    widget.undo_configuration()
    assert widget._configuration_snapshot() == current
    assert not widget._undo_configuration_shortcut.isEnabled()
    widget._set_detection_state(False)
    widget.setEnabled(False)
    widget.undo_configuration()
    assert widget._configuration_snapshot() == current
    widget.setEnabled(True)
    window.set_current_page(1)
    window.navigation.buttons[1].setFocus()
    key(app, Qt.Key.Key_Z)
    assert not widget._undo_configuration_shortcut.isEnabled()
    assert widget._configuration_snapshot() == current
    window.set_current_page(0)
    widget.select_all_button.setFocus()
    key(app, Qt.Key.Key_Z)
    assert widget._column_configs[0].action is ColumnAction.PRESERVE


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_history_survives_filters_locale_theme_and_validation_without_io(gui, monkeypatch, colors):
    app, window, widget = gui
    action(widget, 0, ColumnAction.MASK)
    expected = widget._configuration_snapshot()
    widget.validate_current_configuration()
    assert widget.generate_button.isEnabled()
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    widget.column_search_field.setText("Nome")
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData("mask"))
    apply_widget_palette(window, colors)
    for translator in translators:
        window.retranslate(translator)
    widget.select_all_button.setFocus()
    app.processEvents()

    def forbidden(*args, **kwargs):
        pytest.fail("Undo/redo must not access files, keys, vaults or processing services")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        guard.setattr(widget._key_provider, "get_key", forbidden)
        guard.setattr(widget, "_vault_repository_factory", forbidden)
        guard.setattr(module.AnonymizationWorker, "start", forbidden)
        widget.undo_configuration()
        assert not widget._configuration_validated and not widget.generate_button.isEnabled()
        widget.redo_configuration()
    assert widget._configuration_snapshot() == expected
    assert widget.column_search_field.text() == "Nome"
    assert widget.column_action_filter.currentData() == "mask"
    assert widget.select_all_button.hasFocus()


@pytest.mark.parametrize("operation", ["save", "update", "rename", "delete"])
def test_successful_profile_operations_are_boundaries_not_undoable_storage(gui, monkeypatch, operation):
    _, _, widget = gui
    profile = widget._profile_service.create("Initial", widget._column_configs, inspection=widget._inspection_result)
    widget._refresh_profiles(profile.identifier)
    seed(widget)
    widget.validate_current_configuration()
    monkeypatch.setattr(widget, "_ask_profile_name", lambda **kwargs: ("New profile", True))
    monkeypatch.setattr(widget, "_confirm", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(module, "confirm_destructive_action", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    {"save": widget.save_as_profile, "update": widget.update_selected_profile,
     "rename": widget.rename_selected_profile, "delete": widget.delete_selected_profile}[operation]()
    assert not widget._configuration_history.can_undo and not widget._configuration_history.can_redo
    profiles = widget._profile_service.list_profiles()
    assert len(profiles) == (2 if operation == "save" else 0 if operation == "delete" else 1)


@pytest.mark.parametrize("dialog", ["csv", "output", "save_profile", "rename_profile", "update_profile",
                                   "delete_profile", "apply_profile", "composite"])
def test_cancelled_dialogs_preserve_both_stacks_and_configuration(gui, monkeypatch, dialog):
    _, _, widget = gui
    profile = widget._profile_service.create("Initial", widget._column_configs, inspection=widget._inspection_result)
    widget._refresh_profiles(profile.identifier)
    seed(widget)
    widget.validate_current_configuration()
    before = widget._configuration_snapshot()
    entries = tuple(widget._configuration_history._undo), tuple(widget._configuration_history._redo)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    monkeypatch.setattr(widget, "_ask_profile_name", lambda **kwargs: ("", False))
    monkeypatch.setattr(widget, "_confirm", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    monkeypatch.setattr(module, "confirm_destructive_action", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    from data_mask_studio.gui.composite_dialog import CompositeDialog
    monkeypatch.setattr(CompositeDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    {"csv": widget._select_csv, "output": widget._choose_output_file,
     "save_profile": widget.save_as_profile, "rename_profile": widget.rename_selected_profile,
     "update_profile": widget.update_selected_profile, "delete_profile": widget.delete_selected_profile,
     "apply_profile": widget.apply_selected_profile, "composite": widget.composite_section.edit}[dialog]()
    assert widget._configuration_snapshot() == before
    assert entries == (tuple(widget._configuration_history._undo), tuple(widget._configuration_history._redo))


@pytest.mark.parametrize("change", ["action", "prefix", "remove"])
def test_changes_to_existing_composites_clear_physical_history(gui, change):
    _, _, widget = gui
    composite = CompositeColumnConfig("PAIR", "PAIR", tuple(
        CompositeSource(source_ref_at(widget._inspection_result, index)) for index in (0, 1)), action=ColumnAction.MASK)
    widget.composite_section.set_configurations((composite,), notify=True)
    seed(widget)
    if change == "action":
        widget.composite_section.table.cellWidget(0, 0).setCurrentIndex(0)
    elif change == "prefix":
        widget.composite_section.table.cellWidget(0, 3).setText("OTHER")
    else:
        widget.composite_section.remove(0)
    assert not widget._configuration_history.can_undo and not widget._configuration_history.can_redo


def test_restoration_blocks_editor_signals_and_preserves_unchanged_native_text_history(gui):
    app, _, widget = gui
    output = widget._output_name_fields[0]
    output.setFocus()
    QTest.keyClicks(output, "PERSON")
    assert output.isUndoAvailable()
    action(widget, 0, ColumnAction.MASK)
    widget.select_all_button.setFocus()
    app.processEvents()
    signals = [QSignalSpy(widget._action_fields[0].currentIndexChanged),
               QSignalSpy(widget._normalization_fields[0].currentIndexChanged),
               QSignalSpy(widget._prefix_fields[0].textChanged), QSignalSpy(output.textChanged)]
    widget.undo_configuration()
    widget.redo_configuration()
    assert all(signal.count() == 0 for signal in signals)
    assert len(widget._configuration_history._undo) == 1
    assert output.isUndoAvailable() and output.text() == "PERSON"
    output.setFocus()
    key(app, Qt.Key.Key_Z)
    assert output.text() == ""
    assert not widget._configuration_history.can_undo and not widget._configuration_history.can_redo


@pytest.mark.parametrize("worker_attribute", ["_worker", "_detection_worker"])
def test_finished_but_owned_worker_reference_still_blocks_history(gui, worker_attribute):
    from PySide6.QtCore import QThread
    app, _, widget = gui
    action(widget, 0, ColumnAction.MASK)
    before = widget._configuration_snapshot()
    worker = QThread(widget)
    setattr(widget, worker_attribute, worker)
    widget._update_history_shortcuts()
    assert not widget._undo_configuration_shortcut.isEnabled()
    widget.undo_configuration()
    assert widget._configuration_snapshot() == before
    (widget._worker_finished if worker_attribute == "_worker" else widget._detection_worker_finished)()
    widget.undo_configuration()
    assert widget._column_configs[0].action is ColumnAction.PRESERVE


def test_modal_editor_does_not_undo_configuration_and_does_not_clear_history(gui):
    app, window, widget = gui
    seed(widget)
    before = widget._configuration_snapshot()
    dialog = QDialog(window)
    dialog.setModal(True)
    field = QLineEdit(dialog)
    QVBoxLayout(dialog).addWidget(field)
    dialog.show()
    field.setFocus()
    app.processEvents()
    key(app, Qt.Key.Key_Z)
    widget.undo_configuration()
    assert widget._configuration_snapshot() == before
    assert widget._configuration_history.can_undo and widget._configuration_history.can_redo
    dialog.reject()
    dialog.deleteLater()
    window.activateWindow()
    widget.select_all_button.setFocus()
    app.processEvents()
    widget.undo_configuration()
    assert widget._column_configs[0].action is ColumnAction.PRESERVE


def test_processing_clears_history_and_undo_never_changes_published_files(gui, tmp_path):
    import csv
    app, window, widget = gui
    original = widget._build_current_plan()
    action(widget, 0, ColumnAction.MASK)
    widget.undo_configuration()
    assert widget._build_current_plan() == original
    widget.validate_current_configuration()
    output = tmp_path / "published.csv"
    widget._start_processing(output, overwrite=False)
    worker = widget._worker
    assert worker is not None and worker.wait(5000)
    for _ in range(3):
        app.processEvents()
    assert widget._worker is None and output.exists()
    with output.open(encoding="utf-8-sig", newline="") as stream:
        published_rows = list(csv.reader(stream))
    with widget._inspection_result.path.open(encoding="utf-8-sig", newline="") as stream:
        assert published_rows == list(csv.reader(stream))
    assert not widget._configuration_history.can_undo and not widget._configuration_history.can_redo
    published = output.read_bytes()
    controls = widget._last_output_path, widget.output_path_label.text(), widget.open_folder_button.isEnabled()
    widget.undo_configuration()
    normalize(widget, 0, NormalizationRule.COLLAPSE_WHITESPACE)
    widget.undo_configuration()
    assert widget._build_current_plan() == original
    assert output.read_bytes() == published
    assert controls == (widget._last_output_path, widget.output_path_label.text(), widget.open_folder_button.isEnabled())


def test_snapshots_exclude_passwords_destinations_workers_and_results(gui, tmp_path):
    _, _, widget = gui
    action(widget, 0, ColumnAction.MASK)
    widget.transfer_controls.checkbox.setChecked(True)
    widget.transfer_controls.password.setText("PASSWORD_NOT_CAPTURED")
    widget.transfer_controls.confirmation.setText("PASSWORD_NOT_CAPTURED")
    widget.transfer_controls.destination.setText(str(tmp_path / "private-destination.dmspackage"))
    normalize(widget, 1, NormalizationRule.COLLAPSE_WHITESPACE)
    snapshot = widget._configuration_snapshot()
    assert set(snapshot.__dataclass_fields__) == {
        "generation", "columns", "manually_changed_rows", "unreviewed_sources", "dirty",
    }
    evidence = repr(widget._configuration_history._undo)
    assert "PASSWORD_NOT_CAPTURED" not in evidence and "private-destination" not in evidence
    assert "QThread" not in evidence and "AnonymizationResult" not in evidence


def test_undoing_the_last_mask_keeps_password_clearing_and_never_restores_secrets(gui):
    _, _, widget = gui
    action(widget, 0, ColumnAction.MASK)
    controls = widget.transfer_controls
    controls.checkbox.setChecked(True)
    controls.password.setText("Synthetic password")
    controls.confirmation.setText("Synthetic password")
    controls.show_passwords.setChecked(True)
    widget.undo_configuration()
    assert not controls.checkbox.isEnabled() and not controls.checkbox.isChecked()
    assert not controls.password.text() and not controls.confirmation.text()
    assert not controls.show_passwords.isChecked()
    widget.redo_configuration()
    assert controls.checkbox.isEnabled() and not controls.checkbox.isChecked()
    assert not controls.password.text() and not controls.confirmation.text()
