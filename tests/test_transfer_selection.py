import json
import sqlite3
from dataclasses import replace

import pytest

from data_mask_studio.anonymization import generate_token
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.transfer_package import MaskedFileBinding, PackageError
from data_mask_studio.transfer_package.selection import select_mappings
from data_mask_studio.transfer_package import selection
from data_mask_studio.transfer_package.serialization import encode_payload
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from data_mask_studio.vault.composite_models import CompositeMappingCandidate
from data_mask_studio.vault.repository import VaultReadSession

HMAC = b'H' * 32
AES = b'A' * 32


def scalar(index):
    value = f'Fake person {index}'
    return MappingCandidate(generate_token(HMAC, 'AA', value), 'AA', value, 'Person')


def composite(index):
    canonical = (f'Fake person {index}', f'Fake city {index}')
    original = (f'  Fake person {index}  ', f'Fake city {index}')
    return CompositeMappingCandidate(
        generate_composite_token(HMAC, 'ZZ', canonical), 'ZZ', canonical, original,
        (Rule.COLLAPSE_WHITESPACE, Rule.EXACT),
    )


@pytest.fixture
def vault(tmp_path):
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(AES))
    scalars, composites = [scalar(i) for i in range(3)], [composite(i) for i in range(3)]
    with repo.transaction() as tx:
        tx.upsert_batch(scalars)
    for item in composites:
        repo.composite_repository().upsert_composite_mapping(item)
    return repo, scalars, composites


@pytest.mark.parametrize('scalar_count,composite_count', [(1, 0), (2, 0), (0, 1), (0, 2), (2, 2)])
def test_only_requested_mappings_are_selected(vault, scalar_count, composite_count):
    repo, scalars, composites = vault
    a = [s.code for s in scalars[:scalar_count]]
    b = [c.code for c in composites[:composite_count]]
    result = select_mappings(repo, scalar_codes=a, composite_codes=b)
    assert [s.code for s in result.scalar_mappings] == sorted(a)
    assert [c.code for c in result.composite_mappings] == sorted(b)
    originals = {s.code: s.original_value for s in scalars}
    for item in result.scalar_mappings:
        assert item.original_value == originals[item.code]
        assert item.canonical_value == originals[item.code]
    for item in result.composite_mappings:
        source = next(c for c in composites if c.code == item.code)
        assert item.canonical_values == source.canonical_values
        assert item.original_values == source.original_values
    assert 'Fake' not in repr(result) and 'AA-' not in repr(result)


def test_deduplication_and_order_are_canonical(vault):
    repo, scalars, composites = vault
    a, b = [s.code for s in scalars], [c.code for c in composites]
    first = select_mappings(repo, scalar_codes=a, composite_codes=b)
    second = select_mappings(repo, scalar_codes=list(reversed(a)) * 2, composite_codes=list(reversed(b)) * 3)
    assert first == second
    binding = MaskedFileBinding('ab' * 32, 123)
    assert encode_payload(first.to_payload(binding)) == encode_payload(second.to_payload(binding))


@pytest.mark.parametrize('kind', ['scalar', 'composite'])
def test_missing_mapping_fails_without_partial_result(vault, kind):
    repo, scalars, composites = vault
    with pytest.raises(PackageError, match='ausentes'):
        select_mappings(repo, scalar_codes=[scalars[0].code] + ([scalar(999).code] if kind == 'scalar' else []),
                        composite_codes=[composites[0].code] + ([composite(999).code] if kind == 'composite' else []))


@pytest.mark.parametrize('kind', ['scalar_as_composite', 'composite_as_scalar', 'both'])
def test_strict_type_separation(vault, kind):
    repo, scalars, composites = vault
    with pytest.raises(PackageError):
        if kind == 'scalar_as_composite':
            select_mappings(repo, composite_codes=[scalars[0].code])
        elif kind == 'composite_as_scalar':
            select_mappings(repo, scalar_codes=[composites[0].code])
        else:
            select_mappings(repo, scalar_codes=[scalars[0].code], composite_codes=[scalars[0].code])


@pytest.mark.parametrize('value', [None, 12, '', 'bad', 'AA-abcdefghijkl', ' AA-ABCDEFGHIJKL', 'AA-ABCDEFGHIJKL\n'])
def test_invalid_identity_rejected_before_read(vault, value, monkeypatch):
    repo, _, _ = vault
    monkeypatch.setattr(repo, 'as_read_only', lambda: pytest.fail('must reject before reading'))
    with pytest.raises(PackageError): select_mappings(repo, scalar_codes=[value])
    with pytest.raises(PackageError): select_mappings(repo, composite_codes=[value])


def test_empty_or_untyped_collection_rejected(vault):
    repo, scalars, _ = vault
    with pytest.raises(PackageError): select_mappings(repo)
    with pytest.raises(PackageError): select_mappings(repo, scalar_codes=scalars[0].code)


@pytest.mark.parametrize('mutation', [
    "UPDATE vault_mappings SET canonical_encrypted_value = X'00'",
    'DELETE FROM vault_variations',
    'UPDATE vault_mappings SET total_occurrences = 999',
    "UPDATE composite_mappings SET encrypted_value = X'00'",
    'DELETE FROM composite_variations',
    'UPDATE composite_mappings SET component_count = 3',
])
def test_corrupt_vault_fails_closed_without_sensitive_diagnostics(vault, mutation):
    repo, scalars, composites = vault
    with sqlite3.connect(repo.database_path) as db:
        db.execute(mutation)
    with pytest.raises(PackageError) as error:
        select_mappings(repo, scalar_codes=[scalars[0].code], composite_codes=[composites[0].code])
    assert 'Fake' not in str(error.value)
    assert scalars[0].code not in str(error.value)
    assert composites[0].code not in str(error.value)
    assert error.value.__suppress_context__


def test_ambiguous_original_is_not_invented_or_exported(vault, monkeypatch):
    repo, _, composites = vault
    item = composites[0]
    repo.composite_repository().upsert_composite_mapping(replace(item, original_values=item.canonical_values))
    monkeypatch.setattr('data_mask_studio.vault.composite_repository.normalize_value',
                        lambda *args: pytest.fail('must use stored canonical tuple'))
    selected = select_mappings(repo, composite_codes=[item.code])
    assert selected.composite_mappings[0].original_values is None
    assert selected.composite_mappings[0].canonical_values == item.canonical_values


@pytest.mark.parametrize('batch_size', [1, 400])
def test_all_types_and_batches_share_one_wal_snapshot(vault, monkeypatch, batch_size):
    writer, scalars, composites = vault
    reader = writer.as_read_only()
    original_connect = reader._connect_for_read
    connections, commits, errors = [], [], []
    monkeypatch.setattr(selection, 'BALANCED_SETTINGS', replace(selection.BALANCED_SETTINGS, sqlite_lookup_batch_size=batch_size))
    def connect():
        connection = original_connect()
        connections.append(connection)
        def trace(sql):
            if 'FROM composite_mappings' not in sql or commits or errors:
                return
            try:
                # A valid WAL commit after scalar SELECT pinned the snapshot.
                writer.composite_repository().upsert_composite_mapping(
                    replace(composites[0], original_values=composites[0].canonical_values))
                with writer.transaction() as tx:
                    tx.upsert_batch([scalars[0]])
                commits.append(True)
            except Exception as error:
                errors.append(error)
        connection.set_trace_callback(trace)
        return connection
    monkeypatch.setattr(reader, '_connect_for_read', connect)
    result = select_mappings(reader, scalar_codes=[scalars[0].code], composite_codes=[composites[0].code])
    assert commits == [True] and not errors and len(connections) == 1
    assert result.composite_mappings[0].original_values == composites[0].original_values
    with pytest.raises(sqlite3.ProgrammingError): connections[0].execute('SELECT 1')
    later = select_mappings(reader, composite_codes=[composites[0].code])
    assert later.composite_mappings[0].original_values is None


def test_large_vault_tiny_selection_and_payload_minimization(vault, monkeypatch):
    repo, scalars, composites = vault
    with repo.transaction() as tx:
        tx.upsert_batch([scalar(i) for i in range(3, 1003)])
    queried = []
    original = VaultReadSession.get_many_with_composites
    def lookup(self, codes):
        queried.extend(codes)
        return original(self, codes)
    monkeypatch.setattr(VaultReadSession, 'get_many_with_composites', lookup)
    result = select_mappings(repo, scalar_codes=[scalars[0].code], composite_codes=[composites[0].code])
    assert set(queried) == {scalars[0].code, composites[0].code}
    binding = MaskedFileBinding('cd' * 32, 987654)
    payload = result.to_payload(binding, app_version='1.2.0')
    assert payload.masked_file is binding
    doc = json.loads(encode_payload(payload))
    assert len(doc['scalar_mappings']) == len(doc['composite_mappings']) == 1
    assert set(doc['scalar_mappings'][0]) == {'code', 'original_value', 'canonical_value'}
    assert set(doc['composite_mappings'][0]) == {'code', 'identity_version', 'canonical_values', 'original_values'}
    assert 'Fake person 999' not in encode_payload(payload).decode()
    assert not list(repo.database_path.parent.glob('*.dmspackage'))


def test_selection_is_read_only(vault, monkeypatch):
    repo, scalars, _ = vault
    reader = repo.as_read_only()
    original = reader._connect_for_read
    def connect():
        connection = original()
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('DELETE FROM vault_mappings')
        return connection
    monkeypatch.setattr(reader, '_connect_for_read', connect)
    with sqlite3.connect(repo.database_path) as db:
        before = tuple(db.iterdump())
    select_mappings(reader, scalar_codes=[scalars[0].code])
    with sqlite3.connect(repo.database_path) as db:
        assert tuple(db.iterdump()) == before


def test_scalar_first_representation_and_stored_canonical(vault):
    repo, _, _ = vault
    canonical = 'Fake normalized person'
    code = generate_token(HMAC, 'NAME', canonical)
    first = MappingCandidate(code, 'NAME', '  Fake normalized person  ', 'Name',
                             canonical_value=canonical, normalization_rule=Rule.COLLAPSE_WHITESPACE)
    with repo.transaction() as tx:
        tx.upsert_batch([first])
        tx.upsert_batch([MappingCandidate(code, 'NAME', canonical, 'Name', canonical_value=canonical)])
    result = select_mappings(repo, scalar_codes=[code]).scalar_mappings[0]
    assert result.original_value == first.original_value
    assert result.canonical_value == canonical
    assert not hasattr(result, 'variations')


def test_missing_or_invalid_extra_results_close_session(vault, monkeypatch):
    repo, scalars, _ = vault
    original = VaultReadSession.get_many_with_composites
    sessions = []
    def lookup(self, codes):
        sessions.append(self)
        found = original(self, codes)
        found['UNREQUESTED-ABCDEFGHIJKL'] = found[codes[0]]
        return found
    monkeypatch.setattr(VaultReadSession, 'get_many_with_composites', lookup)
    with pytest.raises(PackageError): select_mappings(repo, scalar_codes=[scalars[0].code])
    with pytest.raises(sqlite3.ProgrammingError): sessions[0]._connection.execute('SELECT 1')


def test_payload_assembly_rejects_invalid_binding(vault):
    repo, scalars, _ = vault
    result = select_mappings(repo, scalar_codes=[scalars[0].code])
    with pytest.raises(PackageError): result.to_payload(MaskedFileBinding('invalid', 0))
    with pytest.raises(PackageError): result.to_payload(MaskedFileBinding('ab' * 32, -1))
