import pytest

from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.processing import PlanningError
from data_mask_studio.profiles import ProfileRepository, ProfileService


@pytest.fixture
def editor(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    app = create_application([])
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    yield app, service
    app.processEvents()


def setup_widget(tmp_path, editor, header='X', extra_count=1):
    app, service = editor
    template = tmp_path / 'template.csv'
    template.write_text(f'{header},{header}\nAlice,ignore\n', encoding='utf-8')
    service.create('Duplicate policy', [
        ColumnConfig(header, True, 'XX', output_name='MASKED_X'),
        ColumnConfig(header, action=Action.EXCLUDE),
    ], inspection=inspect_csv(template))
    source = tmp_path / 'input.csv'
    source.write_text(','.join([header] * (2 + extra_count)) + '\n' +
                      ','.join(['Alice', 'ignore'] + ['NEW_PRIVATE_VALUE'] * extra_count) + '\n', encoding='utf-8')
    widget = AnonymizationWidget(profile_service=service)
    widget.load_csv(str(source))
    widget.apply_profile_button.click()
    app.processEvents()
    return widget


def close_widget(editor, widget):
    widget.close()
    widget.deleteLater()
    editor[0].processEvents()


def test_known_duplicate_edit_does_not_approve_private_third_occurrence(editor, tmp_path, monkeypatch):
    from data_mask_studio.batch import BatchFile, BatchFileStatus, BatchService

    widget = setup_widget(tmp_path, editor)
    try:
        service = editor[1]
        item = BatchFile(tmp_path / 'input.csv')
        BatchService(service).validate([item], service.list_profiles()[0])
        assert item.status is BatchFileStatus.INCOMPATIBLE
        assert not widget.generate_button.isEnabled()
        assert widget._column_configs[2].action is Action.PRESERVE
        widget._output_name_fields[0].setText('RENAMED_MASKED_X')
        widget.validate_current_configuration()
        assert not widget.generate_button.isEnabled()
        with pytest.raises(PlanningError):
            widget._build_current_plan()
        from PySide6.QtWidgets import QFileDialog
        monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                            lambda *args, **kwargs: pytest.fail('Generation must not reach file selection'))
        widget.generate_button.click()
        widget._choose_output_file()
        assert not (tmp_path / 'input_anonimizado.csv').exists()
    finally:
        close_widget(editor, widget)


@pytest.mark.parametrize('action', [Action.PRESERVE, Action.MASK, Action.EXCLUDE])
def test_third_occurrence_explicit_review_allows_selected_action(editor, tmp_path, action):
    from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv
    import csv

    widget = setup_widget(tmp_path, editor)
    try:
        widget._output_name_fields[0].setText('RENAMED_MASKED_X')
        widget.validate_current_configuration()
        assert widget._unreviewed_sources == {2}
        assert not widget.generate_button.isEnabled()
        # Editing the output name is the existing way to review an unchanged PRESERVE action.
        if action is Action.PRESERVE:
            widget._output_name_fields[2].setText('REVIEWED_X')
        else:
            field = widget._action_fields[2]
            field.setCurrentIndex(field.findData(action.value))
            if action is Action.MASK:
                widget._prefix_fields[2].setText('EXTRA')
        widget.validate_current_configuration()
        assert not widget._unreviewed_sources
        assert widget.generate_button.isEnabled()
        assert widget._column_configs[0].action is Action.MASK
        assert widget._column_configs[1].action is Action.EXCLUDE
        output = tmp_path / 'reviewed.csv'
        anonymize_csv(tmp_path / 'input.csv', output, encoding='utf-8', delimiter=',',
                      processing_plan=widget._build_current_plan(), secret_key=b'K' * 32)
        rows = list(csv.reader(output.read_text(encoding='utf-8-sig').splitlines()))
        if action is Action.PRESERVE:
            assert rows[1][1] == 'NEW_PRIVATE_VALUE'
        else:
            assert 'NEW_PRIVATE_VALUE' not in output.read_text(encoding='utf-8-sig')
            assert len(rows[1]) == (2 if action is Action.MASK else 1)
    finally:
        close_widget(editor, widget)


@pytest.mark.parametrize('header', ['X', 'column_1'])
def test_multiple_unknown_occurrences_are_independent(editor, tmp_path, header):
    widget = setup_widget(tmp_path, editor, header=header, extra_count=2)
    try:
        assert widget._unreviewed_sources == {2, 3}
        widget._output_name_fields[2].setText('THIRD')
        assert widget._unreviewed_sources == {3}
        widget._output_name_fields[0].setText('KNOWN')
        widget.validate_current_configuration()
        assert widget._unreviewed_sources == {3}
        assert not widget.generate_button.isEnabled()
        widget._action_fields[3].setCurrentIndex(widget._action_fields[3].findData(Action.EXCLUDE.value))
        widget.validate_current_configuration()
        assert widget.generate_button.isEnabled()
        assert widget._column_configs[2].action is Action.PRESERVE
        assert widget._column_configs[2].output_name == 'THIRD'
    finally:
        close_widget(editor, widget)


@pytest.mark.parametrize('headers,unknown', [
    ('KEEP,B', (1,)), (',KEEP', (0,)), ('column_1,KEEP', (0,)),
    (',KEEP,column_1,column_1', (0, 2, 3)),
])
def test_review_uses_physical_indices_for_unique_synthetic_and_literal_sources(editor, tmp_path, headers, unknown):
    from data_mask_studio.batch import BatchFile, BatchFileStatus, BatchService

    app, service = editor
    profile = service.create('Known source', [ColumnConfig('KEEP')])
    source = tmp_path / 'source.csv'
    source.write_text(headers + '\n' + ','.join(['value'] * len(headers.split(','))) + '\n', encoding='utf-8')
    result = service.apply(profile, inspect_csv(source))
    assert result.extra_source_indices == unknown
    item = BatchFile(source)
    BatchService(service).validate([item], profile)
    assert item.status is BatchFileStatus.INCOMPATIBLE
    widget = AnonymizationWidget(profile_service=service)
    try:
        widget.load_csv(str(source))
        widget.apply_profile_button.click()
        known = headers.split(',').index('KEEP')
        widget._output_name_fields[known].setText('KNOWN')
        assert widget._unreviewed_sources == set(unknown)
        for position, index in enumerate(unknown):
            widget._output_name_fields[index].setText(f'REVIEWED_{index}')
            assert widget._unreviewed_sources == set(unknown[position + 1:])
            widget.validate_current_configuration()
            assert widget.generate_button.isEnabled() == (position == len(unknown) - 1)
        widget.clear_selection()
        assert not widget._unreviewed_sources
    finally:
        close_widget(editor, widget)
