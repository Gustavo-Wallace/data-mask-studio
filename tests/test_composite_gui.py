import csv
import json
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from data_mask_studio.app import create_application
from data_mask_studio.anonymization import ColumnAction as Action
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.gui.composite_dialog import CompositeDialog
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing.models import CompositeSource
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository


@pytest.fixture
def gui(tmp_path):
    app = create_application([])
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    widget = AnonymizationWidget(profile_service=service)
    source = tmp_path / 'source.csv'
    source.write_text('NOME,CPF,IDADE\nGustavo Wallace,999.999.999-99,24\n', encoding='utf-8')
    widget.load_csv(str(source))
    yield app, widget, service
    widget.stop_worker()
    widget.close()
    widget.deleteLater()
    app.processEvents()


def editor(widget, existing=None):
    return CompositeDialog(widget._inspection_result,
                           lambda c: widget._build_current_plan((c,)), existing, widget)


def fill(dialog):
    dialog.name_field.setText('  PESSOA_CORRELACAO  ')
    dialog.rows[0].normalization.setCurrentIndex(dialog.rows[0].normalization.findData(Rule.PERSON_NAME))
    dialog.rows[1].normalization.setCurrentIndex(dialog.rows[1].normalization.findData(Rule.CPF))


def create(widget, *, mask=True):
    dialog = editor(widget)
    fill(dialog)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    config = dialog.result_config
    widget.composite_section.set_configurations((config,), notify=True)
    if mask:
        widget.composite_section.table.cellWidget(0, 0).setCurrentIndex(1)
        widget.composite_section.table.cellWidget(0, 3).setText('CORR')
    return widget.composite_section.configurations[0]


def test_empty_state_create_edit_cancel_delete_and_uuid(gui, monkeypatch):
    app, widget, _ = gui
    section = widget.composite_section
    assert not section.empty_label.isHidden() and section.table.isHidden()
    assert section.add_button.isEnabled()

    def create_dialog(dialog):
        fill(dialog)
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(CompositeDialog, 'exec', create_dialog)
    section.add_button.click()
    original = section.configurations[0]
    assert original.identifier.int and original.output_name == 'PESSOA_CORRELACAO'
    assert original.prefix == '' and original.action is Action.PRESERVE
    assert [s.reference.header for s in original.components] == ['NOME', 'CPF']
    assert [s.normalization_rule for s in original.components] == [Rule.PERSON_NAME, Rule.CPF]
    assert section.empty_label.isHidden() and section.table.rowCount() == 1

    def edit_dialog(dialog):
        dialog.name_field.setText('OUTRO NOME')
        dialog.rows[0].normalization.setCurrentIndex(0)
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(CompositeDialog, 'exec', edit_dialog)
    section.edit(0)
    edited = section.configurations[0]
    assert edited.identifier == original.identifier and edited.output_name == 'OUTRO NOME'
    assert edited.components[0].normalization_rule is Rule.EXACT

    def cancel_dialog(dialog):
        dialog.name_field.setText('CANCELADO')
        dialog.remove_component(dialog.rows[0])
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(CompositeDialog, 'exec', cancel_dialog)
    section.edit(0)
    assert section.configurations == (edited,)
    scalars = tuple(widget._column_configs)
    section.remove(0)
    assert section.configurations == () and tuple(widget._column_configs) == scalars
    assert not section.empty_label.isHidden()


def test_component_order_excluded_sources_and_initial_layout(gui):
    app, widget, _ = gui
    for field in widget._action_fields:
        field.setCurrentIndex(field.findData(Action.EXCLUDE))
    dialog = editor(widget)
    fill(dialog)
    dialog.show()
    app.processEvents()
    assert dialog.rows[0].source.count() == 3
    assert not dialog.rows[0].up.isEnabled() and not dialog.rows[-1].down.isEnabled()
    for row in dialog.rows:
        assert row.source.geometry().right() < row.normalization.geometry().left()
        assert row.normalization.geometry().right() < row.up.geometry().left()
    first = dialog.rows[0]
    dialog.move_component(first, 1)
    dialog.accept()
    assert [s.reference.header for s in dialog.result_config.components] == ['CPF', 'NOME']
    widget.composite_section.set_configurations((dialog.result_config,), notify=True)
    widget.validate_current_configuration()
    assert widget.generate_button.isEnabled()
    dialog.close()


@pytest.mark.parametrize('invalid', ['minimum', 'duplicate', 'name', 'collision'])
def test_dialog_validation_reuses_planner(gui, invalid):
    _, widget, _ = gui
    dialog = editor(widget)
    fill(dialog)
    if invalid == 'minimum': dialog.remove_component(dialog.rows[0])
    elif invalid == 'duplicate': dialog.rows[1].source.setCurrentIndex(0)
    elif invalid == 'name': dialog.name_field.setText('  ')
    elif invalid == 'collision': dialog.name_field.setText('IDADE')
    dialog.accept()
    assert dialog.result_config is None and dialog.error_label.text()


def test_duplicate_and_synthetic_sources_preserve_real_references(gui, tmp_path):
    _, widget, _ = gui
    source = tmp_path / 'duplicates.csv'
    source.write_text('NOME,NOME,,CPF\nAna,Bia,x,123\n', encoding='utf-8')
    widget.load_csv(str(source))
    for field in widget._action_fields:
        field.setCurrentIndex(field.findData(Action.EXCLUDE))
    dialog = editor(widget)
    fill(dialog)
    combo = dialog.rows[0].source
    assert combo.itemText(0) == 'NOME (1)' and combo.itemText(1) == 'NOME (2)'
    assert 'gerado' in combo.itemText(2)
    assert combo.itemData(0).occurrence == 0 and combo.itemData(1).occurrence == 1
    assert combo.itemData(2).is_synthetic and combo.itemData(2).structure
    dialog.rows[0].source.setCurrentIndex(1)
    dialog.rows[1].source.setCurrentIndex(2)
    dialog.accept()
    assert dialog.result_config.components[0].reference.occurrence == 1
    assert dialog.result_config.components[1].reference.is_synthetic


def test_closed_combos_ignore_wheel(gui):
    app, widget, _ = gui
    dialog = editor(widget)
    dialog.show()
    app.processEvents()
    for combo in (dialog.rows[0].source, dialog.rows[0].normalization):
        before = combo.currentIndex()
        event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(combo, event)
        assert combo.currentIndex() == before
    dialog.close()


def test_profile_save_load_update_and_csv_reset(gui, monkeypatch):
    app, widget, service = gui
    original = create(widget)
    widget.validate_current_configuration()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Perfil composto', True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.save_as_profile()
    profile = service.list_profiles()[0]
    assert profile.composites == (original,) and profile.format_version == 2
    widget.composite_section.set_configurations(())
    widget.apply_selected_profile()
    assert widget.composite_section.configurations == (original,)
    assert widget.generate_button.isEnabled()
    widget.composite_section.remove(0)
    widget.validate_current_configuration()
    widget.update_selected_profile()
    assert service.list_profiles()[0].composites == ()
    create(widget)
    widget.load_csv(str(widget._inspection_result.path))
    assert widget.composite_section.configurations == ()


def test_v1_profile_load_clears_composites(gui, monkeypatch):
    _, widget, service = gui
    profile = service.create('Legado', widget._column_configs)
    document = json.loads(service.repository.path.read_text(encoding='utf-8'))
    document['schema_version'] = 1
    raw = document['profiles'][0]
    raw['format_version'] = 1
    del raw['composites'], raw['unknown_column_policy']
    for column in raw['columns']:
        column['anonymize'] = column.pop('action') == 'mask'
    service.repository.path.write_text(json.dumps(document), encoding='utf-8')
    widget.refresh_profiles(profile.identifier)
    create(widget)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.apply_selected_profile()
    assert widget.composite_section.configurations == () and widget.generate_button.isEnabled()


@pytest.mark.parametrize('mode', ['preserve', 'only', 'mask'])
def test_gui_worker_real_composite_output_and_vault(gui, tmp_path, monkeypatch, mode):
    _, widget, _ = gui
    config = create(widget)
    for index, field in enumerate(widget._action_fields):
        if index < 2 or mode == 'only':
            field.setCurrentIndex(field.findData(Action.EXCLUDE))
    if mode == 'mask':
        widget._action_fields[1].setCurrentIndex(widget._action_fields[1].findData(Action.MASK))
    assert widget.composite_section.configurations == (config,)
    widget.validate_current_configuration()
    assert widget.generate_button.isEnabled()
    class Key:
        def get_key(self): return b'G' * 32
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
    widget._key_provider = Key()
    widget._vault_repository_factory = lambda: repo
    # Mesmo worker/pipeline real; execução síncrona evita depender do scheduler.
    monkeypatch.setattr(AnonymizationWorker, 'start', lambda self: self.run())
    output = tmp_path / 'output.csv'
    widget._start_processing(output, overwrite=False)
    with output.open(encoding='utf-8-sig', newline='') as stream:
        headers, row = list(csv.reader(stream))
    expected = ['PESSOA_CORRELACAO'] if mode == 'only' else ['IDADE', 'PESSOA_CORRELACAO']
    if mode == 'mask': expected.insert(0, 'CPF')
    assert headers == expected
    if mode != 'only': assert row[-2] == '24'
    assert repo.count() == (1 if mode == 'mask' else 0)
    assert row[-1].startswith('CORR-')
    mapping = repo.composite_repository().get_composite_mapping(row[-1])
    assert mapping.canonical_values == ('gustavo wallace', '99999999999')
    assert mapping.variations[0].original_values == ('Gustavo Wallace', '999.999.999-99')


def test_invalid_profile_retained_for_review_never_starts_worker(gui, tmp_path, monkeypatch):
    _, widget, service = gui
    config = create(widget)
    invalid = replace(config, components=(CompositeSource(SourceColumnRef('AUSENTE')), config.components[1]))
    profile = service.create('Incompatível', widget._column_configs, composites=(invalid,))
    widget.refresh_profiles(profile.identifier)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.apply_selected_profile()
    assert widget.composite_section.configurations == (invalid,)
    assert not widget.generate_button.isEnabled()
    monkeypatch.setattr(AnonymizationWorker, 'start', lambda self: pytest.fail('Não pode iniciar worker'))
    widget._start_processing(tmp_path / 'invalid.csv', overwrite=False)
    assert not (tmp_path / 'invalid.csv').exists()
    dialog = editor(widget, invalid)
    assert 'indisponível' in dialog.rows[0].source.currentText()
    dialog.accept()
    assert dialog.result_config is None


def test_unknown_columns_require_explicit_review_and_scalar_changes_keep_composite(gui, monkeypatch):
    _, widget, service = gui
    config = create(widget)
    profile = service.create('Incompleto', widget._column_configs[:2], composites=(config,))
    widget.refresh_profiles(profile.identifier)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.apply_selected_profile()
    widget.validate_current_configuration()
    assert not widget.generate_button.isEnabled()
    widget._action_fields[2].setCurrentIndex(widget._action_fields[2].findData(Action.EXCLUDE))
    widget.validate_current_configuration()
    assert widget.generate_button.isEnabled() and widget.composite_section.configurations == (config,)
    widget._action_fields[2].setCurrentIndex(widget._action_fields[2].findData(Action.PRESERVE))
    widget._output_name_fields[2].setText(config.output_name)
    widget.validate_current_configuration()
    assert not widget.generate_button.isEnabled() and 'repetido' in widget.status_label.text()


def test_section_preserves_creation_order_and_deletes_only_selected(gui, monkeypatch):
    _, widget, _ = gui
    names = iter(['ZETA', 'ALFA'])

    def configure(dialog):
        fill(dialog)
        dialog.name_field.setText(next(names))
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(CompositeDialog, 'exec', configure)
    section = widget.composite_section
    section.add_button.click()
    section.add_button.click()
    first, second = section.configurations
    assert first.identifier != second.identifier
    assert [section.table.item(i, 1).text() for i in range(2)] == ['ZETA', 'ALFA']
    assert widget._build_current_plan().final_headers[-2:] == ('ZETA', 'ALFA')
    section.remove(0)
    assert section.configurations == (second,)


def test_action_transitions_validation_and_profile_round_trip(gui, monkeypatch):
    app, widget, service = gui
    original = create(widget, mask=False)
    section = widget.composite_section
    combo = section.table.cellWidget(0, 0)
    prefix = section.table.cellWidget(0, 3)
    assert [combo.itemText(i) for i in range(combo.count())] == ['Preservar', 'Mascarar']
    assert not prefix.isEnabled() and prefix.text() == ''
    assert combo.property('columnAction') == 'preserve'
    combo.setCurrentIndex(1)
    assert prefix.isEnabled() and prefix.text() == ''
    widget.validate_current_configuration()
    assert not widget.generate_button.isEnabled() and prefix.styleSheet()
    monkeypatch.setattr(AnonymizationWorker, 'start', lambda self: pytest.fail('Worker inválido'))
    blocked_output = widget._inspection_result.path.parent / 'blocked.csv'
    widget._start_processing(blocked_output, overwrite=False)
    assert not blocked_output.exists()
    prefix.setText('invalid prefix')
    widget.validate_current_configuration()
    assert not widget.generate_button.isEnabled()
    prefix.setText('CORR')
    widget.validate_current_configuration()
    assert widget.generate_button.isEnabled() and not prefix.styleSheet()
    masked = section.configurations[0]
    assert masked == replace(original, action=Action.MASK, prefix='CORR')
    dialog = editor(widget, masked)
    assert not hasattr(dialog, 'prefix_field')
    dialog.name_field.setText('EDITADA')
    dialog.accept()
    assert dialog.result_config == replace(masked, output_name='EDITADA')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Ações', True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.save_as_profile()
    section.set_configurations(())
    widget.apply_selected_profile()
    assert section.configurations == (masked,)
    assert section.table.cellWidget(0, 3).isEnabled()
    combo = section.table.cellWidget(0, 0)
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(combo, event)
    assert Action(combo.currentData()) is Action.MASK
    combo.setCurrentIndex(0)
    assert section.configurations == (original,)
    assert not section.table.cellWidget(0, 3).isEnabled()
    assert section.table.cellWidget(0, 3).text() == ''
    widget.validate_current_configuration()
    widget.update_selected_profile()
    assert service.list_profiles()[0].composites == (original,)
    section.set_configurations(())
    widget.apply_selected_profile()
    assert section.configurations == (original,)
    assert not section.table.cellWidget(0, 3).isEnabled()


@pytest.mark.parametrize('mixed', [False, True])
def test_gui_preserve_and_mixed_real_output(gui, tmp_path, monkeypatch, mixed):
    _, widget, _ = gui
    for field in widget._action_fields:
        field.setCurrentIndex(field.findData(Action.EXCLUDE))
    clear = create(widget, mask=False)
    composites = [clear]
    if mixed:
        from uuid import uuid4
        composites.append(replace(clear, identifier=uuid4(), output_name='TOKEN', action=Action.MASK, prefix='CORR'))
    widget.composite_section.set_configurations(composites, notify=True)
    widget.validate_current_configuration()
    assert widget.generate_button.isEnabled()
    calls = []
    class Key:
        def get_key(self):
            calls.append('key')
            assert mixed
            return b'G' * 32
    def vault():
        calls.append('vault')
        assert mixed
        return VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
    widget._key_provider = Key()
    widget._vault_repository_factory = vault
    monkeypatch.setattr(AnonymizationWorker, 'start', lambda self: self.run())
    output = tmp_path / 'clear.csv'
    widget._start_processing(output, overwrite=False)
    with output.open(encoding='utf-8-sig', newline='') as stream:
        headers, row = list(csv.reader(stream))
    assert headers == [c.output_name for c in composites]
    assert row[0] == '["gustavo wallace","99999999999"]'
    if mixed:
        assert calls == ['key', 'vault']
        assert row[1].startswith('CORR-')
        repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
        assert repo.composite_repository().get_composite_mapping(row[1]).occurrence_count == 1
    else:
        assert not calls and not (tmp_path / 'vault.db').exists()


def test_refined_dialog_and_table_layout(gui):
    app, widget, _ = gui
    section = widget.composite_section
    create(widget, mask=False)
    widget.resize(900, 600)
    widget.show()
    app.processEvents()
    assert [section.table.horizontalHeaderItem(i).text() for i in range(5)] == [
        'Ação', 'Cabeçalho de saída', 'Componentes', 'Prefixo', 'Ações']
    assert widget.config_table.horizontalHeaderItem(2).text() == 'Cabeçalho de saída'
    for column in (0, 3, 4):
        assert section.table.cellWidget(0, column).width() <= section.table.columnWidth(column)
    dialog = editor(widget)
    dialog.show()
    app.processEvents()
    assert dialog.name_field.accessibleName() == 'Cabeçalho de saída'
    initial = dialog.height()
    assert not hasattr(dialog, 'prefix_field')
    for _ in range(12):
        dialog.add_component()
    app.processEvents()
    assert dialog.height() >= initial
    assert dialog.scroll.verticalScrollBar().maximum() > 0
    assert dialog.height() <= dialog.screen().availableGeometry().height()
    dialog.close()
