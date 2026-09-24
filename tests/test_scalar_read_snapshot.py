"""Deterministic valid WAL commits between scalar mapping and variation reads."""
import sqlite3

import pytest

from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository, VaultError
from data_mask_studio.vault.repository import VaultReadSession

CODE = 'NOME-ABCDEFGHI234'


def candidate():
    return MappingCandidate(CODE, 'NOME', 'Original value', 'Nome')


def setup_race(tmp_path, monkeypatch):
    writer = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'R' * 32))
    with writer.transaction() as transaction:
        transaction.upsert_batch([candidate()])
    reader = writer.as_read_only()
    connect = reader._connect_for_read
    commits, errors, observed = [], [], []
    def traced_connect():
        connection = connect()
        def before_select(sql):
            if 'FROM vault_variations' not in sql or commits or errors:
                return
            # Mapping rows have already been materialized by the reader.
            try:
                with writer.transaction() as transaction:
                    transaction.upsert_batch([candidate()])
                commits.append(True)
                with sqlite3.connect(writer.database_path) as fresh:
                    observed.append((
                        fresh.execute('SELECT total_occurrences FROM vault_mappings').fetchone()[0],
                        fresh.execute('SELECT SUM(occurrence_count) FROM vault_variations').fetchone()[0],
                    ))
            except Exception as error:
                errors.append(error)
        connection.set_trace_callback(before_select)
        return connection
    monkeypatch.setattr(reader, '_connect_for_read', traced_connect)
    return writer, reader, commits, errors, observed


@pytest.mark.parametrize('method', ['get_many', 'get_many_with_composites', 'get_decrypted_mapping'])
def test_valid_concurrent_commit_keeps_scalar_snapshot(tmp_path, monkeypatch, method):
    writer, reader, commits, errors, observed = setup_race(tmp_path, monkeypatch)
    if method == 'get_decrypted_mapping':
        result = reader.get_decrypted_mapping(CODE)
    else:
        with reader.read_session() as session:
            result = getattr(session, method)([CODE])[CODE]
            # The session owns one coherent view, not one snapshot per SELECT.
            assert session.get_many([CODE])[CODE].occurrence_count == 1
    assert commits == [True] and errors == []
    assert observed == [(2, 2)]
    assert result.occurrence_count == 1
    assert sum(v.occurrence_count for v in result.variations) == 1
    assert reader.get_decrypted_mapping(CODE).occurrence_count == 2


@pytest.mark.parametrize('format', ['html', 'csv'])
def test_restoration_during_valid_commit(tmp_path, monkeypatch, format):
    writer, reader, commits, errors, observed = setup_race(tmp_path, monkeypatch)
    source = tmp_path / f'input.{format}'
    output = tmp_path / f'output.{format}'
    if format == 'html':
        from data_mask_studio.html_restoration import inspect_html, restore_html
        source.write_text(f'<p>{CODE}</p>', encoding='utf-8')
        original = source.read_bytes()
        restore_html(inspect_html(source), output, reader)
        assert output.read_text(encoding='utf-8') == '<p>Original value</p>'
    else:
        from data_mask_studio.restoration import restore_csv
        from test_restoration import configuration
        source.write_text(f'Nome\n{CODE}\n', encoding='utf-8')
        original = source.read_bytes()
        restore_csv(configuration(source, headers=('Nome',), delimiter=','), output, reader)
        assert output.read_text(encoding='utf-8-sig').splitlines() == ['Nome', 'Original value']
    assert source.read_bytes() == original
    assert commits == [True] and not errors and observed == [(2, 2)]
    assert writer.get_decrypted_mapping(CODE).occurrence_count == 2


@pytest.mark.parametrize('method', ['get_many', 'get_many_with_composites'])
@pytest.mark.parametrize('write', [False, True])
def test_caller_owned_transaction_is_not_ended(tmp_path, method, write):
    from data_mask_studio.vault.database import connect, connect_read_only
    from data_mask_studio.vault.repository import VaultTransaction
    cipher = VaultCipher(b'R' * 32)
    repo = VaultRepository(tmp_path / 'vault.db', cipher)
    with repo.transaction() as transaction:
        transaction.upsert_batch([candidate()])
    connection = (connect if write else connect_read_only)(repo.database_path)
    try:
        connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        if write:
            VaultTransaction(connection, cipher, repo.database_path).upsert_batch([candidate()])
        session = VaultReadSession(connection, cipher)
        result = getattr(session, method)([CODE])[CODE]
        assert result.occurrence_count == (2 if write else 1)
        assert connection.in_transaction
        connection.rollback()
    finally:
        connection.close()
    assert repo.get_decrypted_mapping(CODE).occurrence_count == 1


@pytest.mark.parametrize('mutation', [
    'UPDATE vault_mappings SET total_occurrences = 99',
    'DELETE FROM vault_variations',
    "UPDATE vault_variations SET code = 'OTHER'",
    'UPDATE vault_variations SET encrypted_value = zeroblob(length(encrypted_value))',
])
def test_actual_corruption_still_rejected(tmp_path, mutation):
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'R' * 32))
    with repo.transaction() as transaction:
        transaction.upsert_batch([candidate()])
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute(mutation)
    with pytest.raises(VaultError):
        repo.get_decrypted_mapping(CODE)
    with pytest.raises(VaultError):
        with repo.as_read_only().read_session() as session:
            session.get_many([CODE])
    with pytest.raises(sqlite3.ProgrammingError):
        session._connection.execute('SELECT 1')


def test_read_snapshots_do_not_mutate_vault(tmp_path):
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'R' * 32))
    with repo.transaction() as transaction:
        transaction.upsert_batch([candidate()])
    def snapshot():
        with sqlite3.connect(repo.database_path) as connection:
            return tuple(connection.iterdump())
    before = snapshot()
    reader = repo.as_read_only()
    reader.get_decrypted_mapping(CODE)
    with reader.read_session() as session:
        session.get_many([CODE])
        session.get_many_with_composites([CODE])
        with pytest.raises(sqlite3.OperationalError):
            session._connection.execute('UPDATE vault_mappings SET total_occurrences = 99')
    assert snapshot() == before
