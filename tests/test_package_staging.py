import csv
import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import replace

import pytest

from data_mask_studio.anonymization import ColumnConfig, ColumnAction as Action
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv, CSVAnonymizationError, ProcessingCancelled
from data_mask_studio.transfer_package import PackageError, read_package
from data_mask_studio.transfer_package.selection import select_mappings, select_transaction_mappings
from data_mask_studio.transfer_package.staging import PackageStagingRequest, stage_transfer_package
from data_mask_studio.transfer_package import staging
from data_mask_studio.vault import MappingCandidate
from test_composite_csv import setup_case, counts

PASSWORD = 'synthetic staging password'


def case(tmp_path):
    return setup_case(tmp_path, [('Fake Alice', '99999999999', '20')], [
        ColumnConfig('NOME', action=Action.EXCLUDE), ColumnConfig('CPF', True, 'CPF_ID'), ColumnConfig('IDADE'),
    ])


def dump(repo):
    with sqlite3.connect(repo.database_path) as db:
        return tuple(db.iterdump())


def test_mixed_existing_and_new_are_packaged_before_commit(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    old = anonymize_csv(source, tmp_path / 'seed.csv', **kwargs)
    with repo.transaction() as tx:
        tx.upsert_batch([MappingCandidate('OTHER-ABCDEFGHIJKL', 'OTHER', 'Unreferenced secret', 'Unused')])
    before = dump(repo)
    with source.open('a', encoding='utf-8', newline='') as stream:
        csv.writer(stream).writerows([('Fake Bob', '88888888888', '21'), ('Fake Bob', '88888888888', '21')])
    original = repo.transaction
    observed = []
    @contextmanager
    def transaction():
        with original() as tx:
            yield tx
            # Runs AFTER candidate creation but BEFORE the real context commits.
            assert tx._connection.in_transaction
            candidates = list(tmp_path.glob('.dms-package-*.tmp'))
            assert len(candidates) == 1 and not destination.exists()
            payload = read_package(candidates[0], PASSWORD)
            assert len(payload.scalar_mappings) == len(payload.composite_mappings) == 2
            assert dump(repo) == before  # independent SQLite connection sees only old data
            new_scalar = next(m.code for m in payload.scalar_mappings if m.code not in old.emitted_scalar_codes)
            new_composite = next(m.code for m in payload.composite_mappings if m.code not in old.emitted_composite_codes)
            with pytest.raises(PackageError): select_mappings(repo, scalar_codes=[new_scalar])
            with pytest.raises(PackageError): select_mappings(repo, composite_codes=[new_composite])
            assert select_transaction_mappings(tx, scalar_codes=[new_scalar], composite_codes=[new_composite])
            observed.append(payload)
    monkeypatch.setattr(repo, 'transaction', transaction)
    result = anonymize_csv(source, destination, **kwargs, transfer_package_request=PackageStagingRequest(PASSWORD),
                           transfer_package_destination=tmp_path / 'out.dmspackage')
    assert len(observed) == 1
    payload = observed[0]
    assert tuple(m.code for m in payload.scalar_mappings) == result.emitted_scalar_codes
    assert tuple(m.code for m in payload.composite_mappings) == result.emitted_composite_codes
    assert 'OTHER-ABCDEFGHIJKL' not in result.emitted_scalar_codes
    assert payload.masked_file == result.masked_file_binding
    raw = destination.read_bytes()
    assert payload.masked_file.sha256 == hashlib.sha256(raw).hexdigest()
    assert payload.masked_file.size == len(raw)
    assert read_package(result.transfer_package_path, PASSWORD) == payload
    encrypted = result.transfer_package_path.read_bytes()
    assert b'Fake Alice' not in encrypted and PASSWORD.encode() not in encrypted
    assert b'Unreferenced secret' not in encrypted
    assert list(tmp_path.glob('*.dmspackage')) == [result.transfer_package_path]
    assert PASSWORD not in repr(PackageStagingRequest(PASSWORD))
    assert not list(tmp_path.glob('.dms-package-*.tmp'))


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'type', 'budget', 'encrypt', 'write', 'binding', 'payload', 'cancel'])
def test_package_failure_rolls_back_without_final_files(tmp_path, monkeypatch, failure):
    source, destination, repo, kwargs = case(tmp_path)
    before = dump(repo)
    request = PackageStagingRequest(PASSWORD, max_payload_bytes=1 if failure == 'budget' else 64 * 1024 * 1024)
    original_select = staging.select_transaction_mappings
    def select(tx, **codes):
        if failure == 'missing':
            codes['scalar_codes'] = ['MISSING-ABCDEFGHIJKL']
        elif failure == 'corrupt':
            tx._connection.execute("UPDATE vault_mappings SET canonical_encrypted_value=X'00'")
        elif failure == 'type':
            codes['scalar_codes'], codes['composite_codes'] = codes['composite_codes'], codes['scalar_codes']
        return original_select(tx, **codes)
    monkeypatch.setattr(staging, 'select_transaction_mappings', select)
    if failure == 'encrypt':
        def fail(*args, **kw): raise RuntimeError('private synthetic encryption failure')
        monkeypatch.setattr(staging, 'encrypt_package', fail)
    if failure == 'write':
        original_temp = staging.tempfile.NamedTemporaryFile
        def fail(*args, **kw):
            if kw.get('prefix') == '.dms-package-': raise OSError('private failure')
            return original_temp(*args, **kw)
        monkeypatch.setattr(staging.tempfile, 'NamedTemporaryFile', fail)
    if failure in ('binding', 'payload'):
        original_read = staging.read_package
        def mismatched(*args, **kw):
            payload = original_read(*args, **kw)
            if failure == 'binding': return replace(payload, masked_file=replace(payload.masked_file, size=0))
            return replace(payload, scalar_mappings=())
        monkeypatch.setattr(staging, 'read_package', mismatched)
    cancelled = []
    if failure == 'cancel':
        original_encrypt = staging.encrypt_package
        def encrypt(*args, **kw):
            data = original_encrypt(*args, **kw)
            cancelled.append(True)
            return data
        monkeypatch.setattr(staging, 'encrypt_package', encrypt)
        kwargs['should_cancel'] = lambda: bool(cancelled)
    with pytest.raises(ProcessingCancelled if failure == 'cancel' else CSVAnonymizationError) as error:
        anonymize_csv(source, destination, **kwargs, transfer_package_request=request,
                      transfer_package_destination=tmp_path / 'out.dmspackage')
    assert 'private' not in str(error.value) and PASSWORD not in str(error.value)
    assert not destination.exists() and not list(tmp_path.glob('*.dmspackage'))
    assert not list(tmp_path.glob('.*.tmp'))
    assert dump(repo) == before
    assert not list((tmp_path / 'publication-operations').glob('*.json'))


def test_non_package_path_does_no_package_work(tmp_path, monkeypatch):
    source, destination, _, kwargs = case(tmp_path)
    def forbidden(*args, **kw): pytest.fail('unexpected package work')
    monkeypatch.setattr(staging, 'stage_transfer_package', forbidden)
    monkeypatch.setattr(staging, 'select_transaction_mappings', forbidden)
    monkeypatch.setattr(staging, 'encrypt_package', forbidden)
    result = anonymize_csv(source, destination, **kwargs)
    assert result.transfer_package_path is None and destination.exists()
    assert not list(tmp_path.glob('.dms-package-*'))


def test_post_staging_cancel_discards_verified_candidate_on_rollback(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    original = staging.stage_transfer_package
    candidates = []
    def stage(*args, **kw):
        candidate = original(*args, **kw)
        candidates.append(candidate)
        return candidate
    monkeypatch.setattr(staging, 'stage_transfer_package', stage)
    with pytest.raises(ProcessingCancelled):
        anonymize_csv(source, destination, **kwargs, transfer_package_request=PackageStagingRequest(PASSWORD),
                      transfer_package_destination=tmp_path / 'out.dmspackage', should_cancel=lambda: bool(candidates))
    assert len(candidates) == 1 and not candidates[0].path.exists()
    assert counts(repo) == (0, 0, 0) and not destination.exists()


def test_transaction_read_context_does_not_commit_or_close(tmp_path):
    _, _, repo, _ = case(tmp_path)
    with repo.transaction() as tx:
        tx.upsert_batch([MappingCandidate('ONLY-ABCDEFGHIJKL', 'ONLY', 'Fake', 'Column')])
        with tx.read_session() as session:
            assert session.get_many(['ONLY-ABCDEFGHIJKL'])
        assert tx._connection.in_transaction
        assert select_transaction_mappings(tx, scalar_codes=['ONLY-ABCDEFGHIJKL'])
        with pytest.raises(PackageError): select_mappings(repo, scalar_codes=['ONLY-ABCDEFGHIJKL'])
    assert select_mappings(repo, scalar_codes=['ONLY-ABCDEFGHIJKL'])
    with pytest.raises(PackageError): select_transaction_mappings(tx, scalar_codes=['ONLY-ABCDEFGHIJKL'])


def test_post_commit_failure_retains_candidate_not_final_package(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    def fail(*args): raise OSError('publication failure')
    monkeypatch.setattr('data_mask_studio.csv_tools.csv_anonymizer.publish', fail)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs, transfer_package_request=PackageStagingRequest(PASSWORD),
                      transfer_package_destination=tmp_path / 'out.dmspackage')
    candidates = list(tmp_path.glob('.dms-package-*.tmp'))
    assert len(candidates) == 1 and read_package(candidates[0], PASSWORD)
    assert counts(repo) == (1, 1, 1)
    assert not destination.exists() and not list(tmp_path.glob('*.dmspackage'))


def test_csv_binding_change_is_detected_before_commit(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    original = staging.read_package
    def changed(*args, **kw):
        payload = original(*args, **kw)
        csv_temp = next(tmp_path.glob('.dms-operation-*.tmp'))
        with csv_temp.open('ab') as stream:
            stream.write(b'changed')
        return payload
    monkeypatch.setattr(staging, 'read_package', changed)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs, transfer_package_request=PackageStagingRequest(PASSWORD),
                      transfer_package_destination=tmp_path / 'out.dmspackage')
    assert counts(repo) == (0, 0, 0)
    assert not destination.exists() and not list(tmp_path.glob('.*.tmp'))


def test_partial_package_write_is_ciphertext_only_and_cleaned(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    original = staging.tempfile.NamedTemporaryFile
    written = []
    class PartialWriter:
        def __init__(self, stream): self.stream = stream; self.name = stream.name
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def write(self, data):
            assert isinstance(data, bytes) and data.startswith(b'DMSTRANSFER\x00')
            assert PASSWORD.encode() not in data and b'Fake Alice' not in data
            assert b'H' * 32 not in data and b'V' * 32 not in data
            written.append(self.name)
            self.stream.write(data[:50])
            raise OSError('synthetic partial disk write')
    def create(*args, **kw):
        stream = original(*args, **kw)
        return PartialWriter(stream) if kw.get('prefix') == '.dms-package-' else stream
    monkeypatch.setattr(staging.tempfile, 'NamedTemporaryFile', create)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs, transfer_package_request=PackageStagingRequest(PASSWORD),
                      transfer_package_destination=tmp_path / 'out.dmspackage')
    assert len(written) == 1 and counts(repo) == (0, 0, 0)
    assert not destination.exists() and not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('budget', [0, True, 1024 * 1024 * 1024 + 1])
def test_invalid_trusted_budget_aborts_before_commit(tmp_path, budget):
    source, destination, repo, kwargs = case(tmp_path)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs, transfer_package_request=PackageStagingRequest(PASSWORD, budget),
                      transfer_package_destination=tmp_path / 'out.dmspackage')
    assert counts(repo) == (0, 0, 0) and not destination.exists()


def test_no_emitted_codes_cannot_make_package(tmp_path):
    source, destination, repo, kwargs = setup_case(tmp_path, [(' ', ' ', '20')])
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs, transfer_package_request=PackageStagingRequest(PASSWORD),
                      transfer_package_destination=tmp_path / 'out.dmspackage')
    assert counts(repo) == (0, 0, 0) and not destination.exists()
