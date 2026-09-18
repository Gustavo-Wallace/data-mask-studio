import sqlite3
from dataclasses import replace

import pytest

from data_mask_studio.backup import EnvironmentPaths, create_backup, restore_backup
from data_mask_studio.integrity import IntegrityAuditor, IntegrityStatus
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing.composite_identity import generate_composite_token, serialize_composite_identity
from data_mask_studio.vault import VaultCipher, VaultRepository, MappingCandidate
from data_mask_studio.vault.composite_models import CompositeMappingCandidate
from data_mask_studio.vault.composite_payload import composite_aad, encode_tuple

AES = b'V' * 32
HMAC = b'H' * 32
CANONICAL = ('gustavo', '99999999999')
ORIGINAL = ('Gustavo', '999.999.999-99')


class Provider:
    def __init__(self, key): self.key = key
    def get_key(self): return self.key


def paths(directory):
    return EnvironmentPaths(directory, directory / 'secret.key', directory / 'vault_key.dpapi',
                            directory / 'vault.db', directory / 'profiles.json')


def setup(tmp_path, *, wrong_code=False):
    repository = VaultRepository(tmp_path / 'vault.db', VaultCipher(AES))
    code = generate_composite_token(b'X' * 32 if wrong_code else HMAC, 'CORR', CANONICAL)
    item = CompositeMappingCandidate(code, 'CORR', CANONICAL, ORIGINAL, (Rule.PERSON_NAME, Rule.CPF), 2)
    repository.composite_repository().upsert_composite_mapping(item)
    return repository, item


def audit(tmp_path, **kwargs):
    return IntegrityAuditor(paths(tmp_path), Provider(HMAC), Provider(AES)).run(**kwargs)


def check(report, prefix):
    return next(c for c in report.checks if c.check_type.startswith(prefix))


def state(repo):
    with sqlite3.connect(repo.database_path) as connection:
        return tuple(tuple(connection.execute(f'SELECT * FROM {table} ORDER BY rowid')) for table in
                     ('vault_mappings', 'vault_variations', 'composite_mappings', 'composite_variations'))


@pytest.mark.parametrize('mixed', [False, True])
@pytest.mark.parametrize('multiple', [False, True])
def test_valid_counts_read_only_and_no_normalization(tmp_path, monkeypatch, mixed, multiple):
    from data_mask_studio.anonymization import TokenGenerator
    repo, item = setup(tmp_path)
    if multiple:
        repo.composite_repository().upsert_composite_mapping(replace(item, original_values=('GUSTAVO', '99999999999'), occurrences=3))
    else:
        repo.composite_repository().upsert_composite_mapping(replace(item, occurrences=3))
    if mixed:
        code = TokenGenerator(HMAC).generate('NOME', 'scalar')
        with repo.transaction() as transaction:
            transaction.upsert_batch([MappingCandidate(code, 'NOME', 'scalar', 'Nome')])
    monkeypatch.setattr('data_mask_studio.vault.composite_repository.normalize_value', lambda *args: pytest.fail('normalizer composto'))
    before = state(repo)
    report = audit(tmp_path)
    assert report.status is IntegrityStatus.INTACT
    assert check(report, 'Mappings compostos').examined == 1
    assert check(report, 'Variações compostas').examined == (2 if multiple else 1)
    assert check(report, 'Recomputação dos tokens compostos').failures == 0
    assert state(repo) == before
    for value in (*CANONICAL, *ORIGINAL):
        assert value not in report.to_safe_text()


@pytest.mark.parametrize('statement', [
    "UPDATE composite_mappings SET encrypted_value=X'00'",
    "UPDATE composite_mappings SET nonce=X'00'",
    "UPDATE composite_mappings SET code='CORR-AAAAAAAAAAAA'",
    "UPDATE composite_mappings SET prefix='OTHER'",
    'UPDATE composite_mappings SET component_count=3',
    "UPDATE composite_mappings SET rules='[\"exact\"]'",
    'UPDATE composite_mappings SET identity_version=2',
    'UPDATE composite_mappings SET payload_version=2',
    'UPDATE composite_mappings SET aad_version=2',
    "UPDATE composite_variations SET encrypted_value=X'00'",
    "UPDATE composite_variations SET nonce=X'00'",
    "UPDATE composite_variations SET code='OTHER-AAAAAAAAAAAA'",
    "UPDATE composite_variations SET identifier='another-id'",
    'UPDATE composite_mappings SET total_occurrences=0',
    'UPDATE composite_mappings SET total_occurrences=99',
    'UPDATE composite_variations SET occurrence_count=0',
    'UPDATE composite_variations SET occurrence_count=1.5',
    'DELETE FROM composite_variations',
    'DELETE FROM composite_mappings',
])
def test_persisted_corruption_fails_closed_without_changes(tmp_path, statement):
    repo, _ = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute('PRAGMA ignore_check_constraints=ON')
        connection.execute(statement)
    before = state(repo)
    report = audit(tmp_path)
    assert report.status is IntegrityStatus.FAILURE
    assert state(repo) == before
    for value in (*CANONICAL, *ORIGINAL, AES.decode(), HMAC.decode()):
        assert value not in report.to_safe_text()


@pytest.mark.parametrize('original', [False, True])
@pytest.mark.parametrize('damage', ['magic', 'version', 'truncated', 'trailing', 'utf8', 'arity'])
def test_authenticated_payload_framing_is_audited(tmp_path, original, damage):
    repo, _ = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as connection:
        connection.row_factory = sqlite3.Row
        parent = connection.execute('SELECT * FROM composite_mappings').fetchone()
        variation = connection.execute('SELECT * FROM composite_variations').fetchone()
        encode = encode_tuple if original else serialize_composite_identity
        payload = encode(('A', 'B', 'C') if damage == 'arity' else ('A', 'B'))
        if damage == 'magic': payload = b'XXXXX' + payload[5:]
        elif damage == 'version': payload = payload[:5] + b'\x02' + payload[6:]
        elif damage == 'truncated': payload = payload[:-1]
        elif damage == 'trailing': payload += b'EXTRA'
        elif damage == 'utf8': payload = payload[:-1] + b'\xff'
        encrypted = VaultCipher(AES).encrypt_payload(payload, composite_aad(parent, variation['identifier'] if original else None))
        table = 'composite_variations' if original else 'composite_mappings'
        connection.execute(f'UPDATE {table} SET encrypted_value=?, nonce=?', (encrypted.ciphertext, encrypted.nonce))
    report = audit(tmp_path)
    assert report.status is IntegrityStatus.FAILURE
    assert check(report, 'Variações compostas' if original else 'Mappings compostos').failures == 1


def test_authenticated_code_mismatch_is_not_just_an_aes_check(tmp_path):
    setup(tmp_path, wrong_code=True)
    report = audit(tmp_path)
    assert check(report, 'Mappings compostos').status is IntegrityStatus.INTACT
    assert check(report, 'Recomputação dos tokens compostos').failures == 1
    assert report.status is IntegrityStatus.FAILURE


@pytest.mark.parametrize('rules', ['["exact"]', '["unknown", "exact"]', '{}'])
def test_authenticated_rules_metadata_is_validated(tmp_path, rules):
    repo, _ = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as connection:
        connection.row_factory = sqlite3.Row
        parent = dict(connection.execute('SELECT * FROM composite_mappings').fetchone())
        parent['rules'] = rules
        encrypted = VaultCipher(AES).encrypt_payload(serialize_composite_identity(CANONICAL), composite_aad(parent))
        connection.execute('UPDATE composite_mappings SET rules=?, encrypted_value=?, nonce=?',
                           (rules, encrypted.ciphertext, encrypted.nonce))
    report = audit(tmp_path)
    assert check(report, 'Mappings compostos').failures == 1


def test_authenticated_duplicate_original_is_not_a_second_variation(tmp_path):
    repo, _ = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as connection:
        connection.row_factory = sqlite3.Row
        parent = connection.execute('SELECT * FROM composite_mappings').fetchone()
        encrypted = VaultCipher(AES).encrypt_payload(encode_tuple(ORIGINAL), composite_aad(parent, 'duplicate'))
        connection.execute('INSERT INTO composite_variations VALUES (?,?,?,?,?,?,?)',
                           ('duplicate', parent['code'], encrypted.ciphertext, encrypted.nonce, 'date', 'date', 1))
        connection.execute('UPDATE composite_mappings SET total_occurrences=3')
    report = audit(tmp_path)
    assert check(report, 'Contadores compostos').failures == 0
    assert check(report, 'Unicidade de códigos').failures == 1
    assert report.status is IntegrityStatus.FAILURE


def test_global_code_uniqueness_even_if_triggers_bypassed(tmp_path):
    repo, item = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as connection:
        for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            connection.execute(f'DROP TRIGGER "{name}"')
        encrypted = VaultCipher(AES).encrypt_mapping(item.code, item.prefix, 'Header', Rule.EXACT, 'scalar')
        connection.execute('INSERT INTO vault_mappings VALUES (?,?,?,?,?,?,?,?,?)',
                           (item.code, item.prefix, encrypted.ciphertext, encrypted.nonce, 'Header', 'exact', 'date', 'date', 1))
    report = audit(tmp_path)
    assert check(report, 'Unicidade de códigos').failures == 1
    assert report.status is IntegrityStatus.FAILURE


def test_snapshot_with_wal_is_stable_during_concurrent_updates(tmp_path):
    repo, item = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as keeper:
        keeper.execute('PRAGMA journal_mode=WAL')
        keeper.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        repo.composite_repository().upsert_composite_mapping(item)
        assert (tmp_path / 'vault.db-wal').stat().st_size > 0
        def progress(completed, total):
            if completed == 3:
                repo.composite_repository().upsert_composite_mapping(replace(item, original_values=('GUSTAVO', '99999999999')))
        report = audit(tmp_path, progress_callback=progress)
        assert report.status is IntegrityStatus.INTACT
        assert check(report, 'Variações compostas').examined == 1
    assert check(audit(tmp_path), 'Variações compostas').examined == 2


def test_backup_restored_environment_passes_attestation(tmp_path):
    setup(tmp_path)
    class Protector:
        def protect(self, value): return b'TEST:' + value
        def unprotect(self, value):
            assert value.startswith(b'TEST:')
            return value[5:]
    archive = tmp_path / 'test.dmsbackup'
    password = 'senha longa exclusivamente de teste'
    create_backup(archive, password, password, paths=paths(tmp_path),
                  hmac_key_provider=Provider(HMAC), vault_key_provider=Provider(AES))
    target = tmp_path / 'restored'
    restore_backup(archive, password, paths=paths(target), protector=Protector())
    assert audit(target).status is IntegrityStatus.INTACT


@pytest.mark.parametrize('column', ['identity_version', 'payload_version', 'aad_version'])
def test_future_version_is_not_interpreted(tmp_path, monkeypatch, column):
    repo, _ = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute(f'UPDATE composite_mappings SET {column}=99')
    monkeypatch.setattr(VaultCipher, 'decrypt_payload', lambda *args: pytest.fail('Não interpretar versão futura'))
    assert audit(tmp_path).status is IntegrityStatus.FAILURE


def second_mapping(repo, item):
    canonical = ('outro', CANONICAL[1])
    second = replace(item, code=generate_composite_token(HMAC, 'CORR', canonical),
                     canonical_values=canonical, original_values=('Outro', ORIGINAL[1]))
    repo.composite_repository().upsert_composite_mapping(second)
    return second


def test_variation_reparented_to_existing_mapping_fails_authentication(tmp_path):
    repo, item = setup(tmp_path)
    other = second_mapping(repo, item)
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute('UPDATE composite_variations SET code=? WHERE code=?', (other.code, item.code))
    report = audit(tmp_path)
    assert check(report, 'Variações compostas').failures == 1
    assert report.status is IntegrityStatus.FAILURE


def test_duplicate_variation_id_across_parents_is_detected(tmp_path):
    repo, item = setup(tmp_path)
    other = second_mapping(repo, item)
    with sqlite3.connect(repo.database_path) as connection:
        connection.row_factory = sqlite3.Row
        # Fixture adulterada contorna a PK; nenhuma mudança no schema de produção.
        connection.execute('CREATE TABLE altered AS SELECT * FROM composite_variations')
        connection.execute('DROP TABLE composite_variations')
        connection.execute('ALTER TABLE altered RENAME TO composite_variations')
        identifier = connection.execute('SELECT identifier FROM composite_variations WHERE code=?', (item.code,)).fetchone()[0]
        parent = connection.execute('SELECT * FROM composite_mappings WHERE code=?', (other.code,)).fetchone()
        encrypted = VaultCipher(AES).encrypt_payload(encode_tuple(other.original_values), composite_aad(parent, identifier))
        connection.execute('UPDATE composite_variations SET identifier=?, encrypted_value=?, nonce=? WHERE code=?',
                           (identifier, encrypted.ciphertext, encrypted.nonce, other.code))
    report = audit(tmp_path)
    assert check(report, 'Variações compostas').failures == 0
    assert check(report, 'Unicidade de códigos').failures == 1
    assert report.status is IntegrityStatus.FAILURE


def test_streaming_queries_and_cooperative_cancellation(tmp_path):
    from data_mask_studio.integrity import IntegrityCancelled
    from data_mask_studio.integrity.composite_audit import audit_composites
    from data_mask_studio.vault.database import connect_read_only
    repo, item = setup(tmp_path)
    for i in range(1, 20):
        repo.composite_repository().upsert_composite_mapping(replace(item, original_values=('Gustavo' + ' ' * i, ORIGINAL[1])))
    before = state(repo)
    connection = connect_read_only(repo.database_path)
    try:
        connection.execute('BEGIN')
        statements = []
        connection.set_trace_callback(statements.append)
        checks = audit_composites(connection, HMAC, AES, lambda: False)
        assert all(c.status is IntegrityStatus.INTACT for c in checks)
        assert len([s for s in statements if s.startswith('SELECT')]) == 5
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls > 5
        with pytest.raises(IntegrityCancelled):
            audit_composites(connection, HMAC, AES, cancelled)
    finally:
        connection.close()
    assert state(repo) == before
