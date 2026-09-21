import gc
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace

import pytest

from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv, resolve_anonymization_environment
from data_mask_studio.environment import EnvironmentError, environment_lease
from data_mask_studio.processing import build_processing_plan
from data_mask_studio.vault import VaultCipher, VaultRepository, VaultError
from data_mask_studio.vault import database
from test_composite_actions import setup


@pytest.mark.parametrize('composite', [False, True])
@pytest.mark.parametrize('key', [None, b'H' * 32])
def test_preserve_only_never_inspects_vault(tmp_path, composite, key, monkeypatch):
    inspection, columns, component = setup(tmp_path)
    if not composite:
        columns = [ColumnConfig(h, action=Action.PRESERVE) for h in inspection.headers]
    plan = build_processing_plan(inspection, columns, [component] if composite else [])

    class ForbiddenVault:
        def __getattribute__(self, name):
            pytest.fail(f'Unexpected vault access: {name}')

    @contextmanager
    def forbidden_lease(*args, **kwargs):
        pytest.fail('Preserve-only must not acquire an environment lease')
        yield

    monkeypatch.setattr('data_mask_studio.environment.environment_lease', forbidden_lease)
    repository = ForbiddenVault()
    assert resolve_anonymization_environment(processing_plan=plan, vault_repository=repository) is None
    result = anonymize_csv(inspection.path, tmp_path / 'output.csv', encoding=inspection.encoding,
                          delimiter=',', processing_plan=plan, vault_repository=repository, secret_key=key)
    assert result.new_mappings == result.updated_mappings == 0
    assert (tmp_path / 'output.csv').is_file()


@pytest.mark.parametrize('mode', ['scalar', 'composite', 'mixed', 'legacy'])
def test_mask_and_legacy_hold_shared_lease(tmp_path, mode):
    inspection, columns, component = setup(tmp_path)
    columns = [ColumnConfig(h, action=Action.PRESERVE) for h in inspection.headers]
    composites = []
    if mode == 'composite':
        composites = [replace(component, action=Action.MASK, prefix='PERSON')]
    else:
        columns[0] = ColumnConfig('NOME', action=Action.MASK, prefix='NAME')
        if mode == 'scalar':
            columns[1] = ColumnConfig('CPF', action=Action.MASK, prefix='DOC')
    plan = None if mode == 'legacy' else build_processing_plan(inspection, columns, composites)
    repository = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
    assert resolve_anonymization_environment(processing_plan=plan, vault_repository=repository) == tmp_path
    calls = []

    def progress(_):
        with pytest.raises(EnvironmentError):
            with environment_lease(tmp_path, exclusive=True):
                pass
        calls.append(True)

    anonymize_csv(inspection.path, tmp_path / 'output.csv', encoding=inspection.encoding, delimiter=',',
                  configurations=columns, processing_plan=plan, vault_repository=repository,
                  secret_key=b'H' * 32, progress_callback=progress)
    assert calls
    with environment_lease(tmp_path, exclusive=True):
        pass


def test_closed_connection_collected_in_other_thread_without_second_close(tmp_path, monkeypatch):
    unraisable = []
    monkeypatch.setattr('sys.unraisablehook', unraisable.append)
    connections = [database.connect(tmp_path / 'vault.db')]
    connections[0].close()
    assert connections[0]._lease is None
    with environment_lease(tmp_path, exclusive=True):
        pass

    def collect():
        connections.clear()
        gc.collect()

    thread = threading.Thread(target=collect)
    thread.start()
    thread.join()
    assert not unraisable


def test_previous_destructor_mechanism_fails_even_after_explicit_close(tmp_path):
    connection = database.connect(tmp_path / 'vault.db')
    connection.close()
    errors = []

    def former_destructor():
        try:
            connection.close()
        except sqlite3.ProgrammingError as error:
            errors.append(error)

    thread = threading.Thread(target=former_destructor)
    thread.start()
    thread.join()
    assert len(errors) == 1
    assert 'same thread' in str(errors[0])


@pytest.mark.parametrize('readonly', [False, True])
def test_connection_setup_failure_closes_and_releases_lease(tmp_path, monkeypatch, readonly):
    path = tmp_path / 'vault.db'
    database.connect(path).close()
    closed = []
    original_close = database._EnvironmentConnection.close

    def close(self):
        original_close(self)
        closed.append(self._lease)

    def execute(self, *args, **kwargs):
        raise sqlite3.OperationalError('synthetic setup failure')

    monkeypatch.setattr(database._EnvironmentConnection, 'execute', execute)
    monkeypatch.setattr(database._EnvironmentConnection, 'close', close)
    with pytest.raises(VaultError):
        (database.connect_read_only if readonly else database.connect)(path)
    assert closed == [None]
    with environment_lease(tmp_path, exclusive=True):
        pass


def test_html_qt_worker_closes_connections_on_owner_thread(tmp_path, monkeypatch):
    from test_html_restoration_interface import test_html_widget_analyzes_offscreen
    created, closed = {}, {}
    original_connect, original_close = database._leased_connect, database._EnvironmentConnection.close

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        created[connection] = threading.get_ident()
        return connection

    def close(connection):
        closed[connection] = threading.get_ident()
        original_close(connection)

    monkeypatch.setattr(database, '_leased_connect', connect)
    monkeypatch.setattr(database._EnvironmentConnection, 'close', close)
    test_html_widget_analyzes_offscreen(tmp_path)
    assert created and created == closed
    assert len(set(created.values())) == 2
    assert all(connection._lease is None for connection in created)
