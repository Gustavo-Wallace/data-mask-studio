from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLineEdit, QInputDialog

from data_mask_studio.gui import anonymization_widget as module
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError, ProcessingCancelled
from data_mask_studio.transfer_package.staging import PackageStagingRequest
from data_mask_studio.anonymization.models import AnonymizationResult
from data_mask_studio.vault import VaultRepository, VaultCipher
from test_composite_gui import gui, create

PASSWORD = 'synthetic GUI package password'


@pytest.mark.parametrize('password,confirmation,valid', [
    ('abcdefg', 'abcdefg', False),
    ('abcdefgh', 'abcdefgh', True),
    ('abcdefghijkl', 'abcdefghijkl', True),
    ('abcdefghijklmnop', 'abcdefghijklmnop', True),
    ('abcdefgh', 'abcdefgi', False),
    ('', '', False),
    ('        ', '        ', False),
])
def test_package_gui_password_policy(gui, tmp_path, password, confirmation, valid):
    from data_mask_studio.transfer_package.models import PackageError
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    controls.password.setText(password)
    controls.confirmation.setText(confirmation)
    args = (widget._inspection_result.path, tmp_path / 'out.csv', True)
    if valid:
        options = controls.request(*args)
        assert options['transfer_package_request'].password == password
    else:
        with pytest.raises(PackageError):
            controls.request(*args)


def enable(widget, tmp_path):
    widget.select_all_columns()
    widget.validate_current_configuration()
    controls = widget.transfer_controls
    controls.checkbox.setChecked(True)
    controls.destination.setText(str(tmp_path / 'out.dmspackage'))
    controls.password.setText(PASSWORD)
    controls.confirmation.setText(PASSWORD)
    return controls


def test_mask_and_unchecked_lifecycle(gui, tmp_path):
    _, widget, _ = gui
    controls = widget.transfer_controls
    assert not controls.checkbox.isEnabled() and not controls.checkbox.isChecked()
    assert not controls.fields.isEnabled()
    enable(widget, tmp_path)
    assert controls.checkbox.isEnabled() and controls.fields.isEnabled()
    assert controls.password.echoMode() == controls.confirmation.echoMode() == QLineEdit.EchoMode.Password
    widget.unselect_all_columns()
    assert not controls.checkbox.isEnabled() and not controls.checkbox.isChecked()
    assert not controls.password.text() and not controls.confirmation.text() and not controls.destination.text()


def test_composite_mask_controls_enablement(gui):
    _, widget, _ = gui
    composite = create(widget)
    controls = widget.transfer_controls
    assert controls.checkbox.isEnabled()
    from data_mask_studio.anonymization.models import ColumnAction
    widget.composite_section.set_configurations((replace(composite, action=ColumnAction.PRESERVE),), notify=True)
    assert not controls.checkbox.isEnabled()


@pytest.mark.parametrize('invalid', ['extension', 'existing', 'csv_alias', 'password', 'short', 'confirmation'])
def test_invalid_inputs_do_not_launch_worker(gui, tmp_path, monkeypatch, invalid):
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    output = tmp_path / 'out.csv'
    if invalid == 'extension': controls.destination.setText(str(tmp_path / 'bad.zip'))
    elif invalid == 'existing': Path(controls.destination.text()).write_bytes(b'protected')
    elif invalid == 'csv_alias': controls.destination.setText(str(output))
    elif invalid == 'password': controls.password.clear()
    elif invalid == 'short': controls.password.setText('short')
    else: controls.confirmation.setText('different password')
    monkeypatch.setattr(module, 'AnonymizationWorker', lambda *a, **k: pytest.fail('Worker must not start'))
    widget._start_processing(output, overwrite=False)
    assert widget._worker is None and widget.status_label.text()
    assert PASSWORD not in widget.status_label.text()
    if invalid == 'existing': assert Path(controls.destination.text()).read_bytes() == b'protected'


@pytest.mark.parametrize('enabled', [False, True])
def test_request_is_passed_only_to_worker(gui, tmp_path, monkeypatch, enabled):
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    if not enabled: controls.checkbox.setChecked(False)
    started = []
    monkeypatch.setattr(AnonymizationWorker, 'start', lambda self: started.append(self))
    widget._start_processing(tmp_path / 'out.csv', overwrite=False)
    assert len(started) == 1
    worker = started[0]
    if enabled:
        assert worker._transfer_package_request.password == PASSWORD
        assert worker._transfer_package_destination == tmp_path / 'out.dmspackage'
    else: assert worker._transfer_package_request is None
    assert not controls.password.text() and not controls.confirmation.text()
    assert not controls.isEnabled()
    worker._transfer_package_request = None
    widget._worker_finished()


@pytest.mark.parametrize('action', ['clear', 'load', 'uncheck', 'success', 'failure', 'cancel'])
def test_password_reset(gui, tmp_path, action):
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    controls.show_passwords.setChecked(True)
    if action == 'clear': widget.clear_selection()
    elif action == 'load': widget.load_csv(str(widget._inspection_result.path))
    elif action == 'uncheck': controls.checkbox.setChecked(False)
    elif action == 'success': widget._processing_completed(AnonymizationResult(tmp_path / 'out.csv', 1, 0.1))
    elif action == 'cancel': widget._processing_cancelled()
    else: widget._processing_failed(CSVAnonymizationError('Publicação pendente preservada para recuperação.'))
    assert not controls.password.text() and not controls.confirmation.text()
    assert not controls.show_passwords.isChecked()
    assert controls.password.echoMode() == controls.confirmation.echoMode() == QLineEdit.EchoMode.Password
    if action == 'failure': assert 'pendente' in widget.status_label.text()


def test_password_visibility_toggle_preserves_values_and_request(gui, tmp_path):
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    assert controls.show_passwords.text() == 'Mostrar senhas'
    assert not controls.show_passwords.isChecked()
    assert controls.password.echoMode() == controls.confirmation.echoMode() == QLineEdit.EchoMode.Password
    args = (widget._inspection_result.path, tmp_path / 'out.csv', True)
    expected = controls.request(*args)
    for visible in (True, False, True):
        controls.show_passwords.setChecked(visible)
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        assert controls.password.echoMode() == controls.confirmation.echoMode() == mode
        assert controls.password.text() == controls.confirmation.text() == PASSWORD
        assert controls.request(*args) == expected
    controls.reset()
    assert not controls.show_passwords.isChecked()
    assert controls.password.echoMode() == controls.confirmation.echoMode() == QLineEdit.EchoMode.Password
    assert not controls.password.text() and not controls.confirmation.text()


def test_success_uses_returned_final_path(gui, tmp_path):
    _, widget, _ = gui
    enable(widget, tmp_path)
    final = tmp_path / 'returned.dmspackage'
    widget._processing_completed(AnonymizationResult(tmp_path / 'out.csv', 1, 0.1, transfer_package_path=final))
    assert str(final) in widget.output_path_label.text()
    assert PASSWORD not in widget.output_path_label.text()


def test_profile_contains_no_execution_options(gui, tmp_path, monkeypatch):
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    destination = controls.destination.text()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Profile', True))
    widget.save_as_profile()
    text = (tmp_path / 'profiles.json').read_text(encoding='utf-8')
    assert PASSWORD not in text and destination not in text
    assert 'transfer_package' not in text


@pytest.mark.parametrize('outcome', ['success', 'cancel', 'failure'])
def test_worker_package_boundary_and_secret_release(gui, tmp_path, monkeypatch, outcome):
    app, widget, _ = gui
    enable(widget, tmp_path)
    class Key:
        def get_key(self): return b'H' * 32
    worker = AnonymizationWorker(widget._inspection_result, str(tmp_path / 'out.csv'), widget._column_configs,
        Key(), lambda: object(), overwrite=False, processing_plan=widget._build_current_plan(),
        transfer_package_request=PackageStagingRequest(PASSWORD), transfer_package_destination=tmp_path / 'out.dmspackage')
    observed, failures = [], []
    def backend(*args, **kwargs):
        from PySide6.QtCore import QThread
        assert QThread.currentThread() != app.thread()
        assert kwargs['transfer_package_request'].password == PASSWORD
        observed.append(True)
        if outcome == 'cancel':
            assert kwargs['should_cancel']()
            raise ProcessingCancelled('Cancelado')
        if outcome == 'failure': raise CSVAnonymizationError('Publicação pendente preservada para recuperação.')
        return AnonymizationResult(tmp_path / 'out.csv', 1, 0.1, transfer_package_path=tmp_path / 'out.dmspackage')
    monkeypatch.setattr('data_mask_studio.gui.anonymization_worker.anonymize_csv', backend)
    worker.failed.connect(failures.append)
    if outcome == 'cancel': worker.request_cancel()
    worker.start()
    assert worker.wait(5000)
    app.processEvents()
    assert observed == [True] and worker._transfer_package_request is None
    if outcome == 'failure':
        assert len(failures) == 1 and failures[0].__traceback__ is None and failures[0].__context__ is None
        assert 'pendente' in str(failures[0])
    worker.deleteLater()


def test_real_gui_worker_generates_pair(gui, tmp_path):
    app, widget, _ = gui
    controls = enable(widget, tmp_path)
    class Key:
        def get_key(self): return b'H' * 32
    widget._key_provider = Key()
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
    widget._vault_repository_factory = lambda: repo
    widget._start_processing(tmp_path / 'out.csv', overwrite=False)
    worker = widget._worker
    assert worker is not None and worker.wait(10000)
    app.processEvents()
    assert (tmp_path / 'out.csv').is_file() and (tmp_path / 'out.dmspackage').is_file()
    assert 'out.dmspackage' in widget.output_path_label.text()
    assert not controls.password.text() and worker._transfer_package_request is None


def test_automatic_destination_follows_csv_output(gui, tmp_path):
    _, widget, _ = gui
    widget.select_all_columns()
    controls = widget.transfer_controls
    controls.checkbox.setChecked(True)
    assert Path(controls.destination.text()) == tmp_path / 'source_anonimizado.dmspackage'
    output = tmp_path / 'customers_anonymized.csv'
    controls.set_output_path(output)
    assert Path(controls.destination.text()) == output.with_suffix('.dmspackage')
    controls.set_output_path(tmp_path / 'different.csv')
    assert Path(controls.destination.text()) == tmp_path / 'different.dmspackage'


@pytest.mark.parametrize('method', ['edit', 'browse'])
def test_manual_destination_survives_csv_output_change(gui, tmp_path, monkeypatch, method):
    from PySide6.QtWidgets import QFileDialog
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    custom = tmp_path / 'custom.dmspackage'
    if method == 'edit': controls.destination.setText(str(custom))
    else:
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(custom), ''))
        controls._browse()
    controls.set_output_path(tmp_path / 'different.csv')
    controls.checkbox.setChecked(False)
    controls.checkbox.setChecked(True)
    assert not controls.password.text() and not controls.confirmation.text()
    controls.password.setText(PASSWORD)
    controls.confirmation.setText(PASSWORD)
    request = controls.request(widget._inspection_result.path, tmp_path / 'current.csv', True)
    assert request['transfer_package_destination'] == custom


def test_blank_destination_derived_at_processing_boundary(gui, tmp_path, monkeypatch):
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    controls.destination.clear()
    started = []
    monkeypatch.setattr(AnonymizationWorker, 'start', lambda self: started.append(self))
    output = tmp_path / 'actual-output.csv'
    widget._start_processing(output, overwrite=False)
    assert len(started) == 1
    assert started[0]._transfer_package_destination == output.with_suffix('.dmspackage')
    assert Path(controls.destination.text()) == output.with_suffix('.dmspackage')
    started[0]._transfer_package_request = None
    widget._worker_finished()


def test_automatic_destination_conflict_is_not_renamed(gui, tmp_path, monkeypatch):
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    controls.destination.clear()
    output = tmp_path / 'actual.csv'
    package = output.with_suffix('.dmspackage')
    package.write_bytes(b'existing package')
    monkeypatch.setattr(module, 'AnonymizationWorker', lambda *a, **k: pytest.fail('Must not launch'))
    widget._start_processing(output, overwrite=False)
    assert 'existente' in widget.status_label.text()
    assert Path(controls.destination.text()) == package
    assert package.read_bytes() == b'existing package' and not output.exists()


@pytest.mark.parametrize('reset', ['load', 'clear', 'no_mask'])
def test_destination_auto_manual_state_resets(gui, tmp_path, reset):
    _, widget, _ = gui
    controls = enable(widget, tmp_path)
    controls.destination.setText(str(tmp_path / 'custom.dmspackage'))
    source = tmp_path / 'new.csv'
    source.write_text('Name\nSynthetic\n', encoding='utf-8')
    if reset == 'load': widget.load_csv(str(source))
    elif reset == 'clear':
        widget.clear_selection()
        widget.load_csv(str(source))
    else: widget.unselect_all_columns()
    widget.select_all_columns()
    controls.checkbox.setChecked(True)
    expected = widget._inspection_result.path.with_name(widget._inspection_result.path.stem + '_anonimizado.dmspackage')
    assert Path(controls.destination.text()) == expected
    assert not controls.password.text() and not controls.confirmation.text()
