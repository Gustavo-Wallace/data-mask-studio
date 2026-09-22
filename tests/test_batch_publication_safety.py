import multiprocessing
import os

import pytest

from data_mask_studio.batch import BatchFile, BatchService, BatchFileStatus
from data_mask_studio.environment import EnvironmentError
from data_mask_studio.publication import Publication, recover_publications
from test_batch import make_profile_service, write_csv, FixedKeyProvider, CountingRepository
from test_publication import counts, journals
from data_mask_studio.batch.output_naming import reserve_output_file
from data_mask_studio.batch.service import _remove_reservation
from data_mask_studio.environment import environment_lease
from data_mask_studio.publication import PublicationError


def batch_case(root):
    service, profile = make_profile_service(root)
    source = root / 'input.csv'
    write_csv(source)
    output = root / 'output'
    output.mkdir()
    repo = CountingRepository(root / 'vault.db')
    batch = BatchService(service)
    items = [BatchFile(source)]
    batch.validate(items, profile)
    return batch, items, profile, output, repo


@pytest.mark.parametrize('after_unlink', [False, True])
def test_batch_final_survives_journal_cleanup_failure(tmp_path, monkeypatch, after_unlink):
    batch, items, profile, output, repo = batch_case(tmp_path)
    complete = Publication.complete

    def fail(self):
        if after_unlink:
            complete(self)
        raise PermissionError('synthetic journal cleanup failure')

    monkeypatch.setattr(Publication, 'complete', fail)
    batch.process(items, profile, output, FixedKeyProvider(), lambda: repo)
    assert items[0].status is BatchFileStatus.ERROR
    assert repo.count() == 2
    destination = output / 'input_anonimizado.csv'
    assert destination.is_file()
    content, committed = destination.read_bytes(), counts(repo)
    assert len(journals(tmp_path)) == int(not after_unlink)
    assert recover_publications(repo.database_path, b'B' * 32) == int(not after_unlink)
    assert recover_publications(repo.database_path, b'B' * 32) == 0
    assert destination.read_bytes() == content
    assert counts(repo) == committed


def _producer(root, pipe):
    batch, items, profile, output, repo = batch_case(root)
    complete = Publication.complete

    def pause(self):
        pipe.send('published')
        if not pipe.poll(30):
            raise TimeoutError('test handshake')
        pipe.recv()
        complete(self)

    Publication.complete = pause
    batch.process(items, profile, output, FixedKeyProvider(), lambda: repo)
    pipe.send(items[0].status.value)


def test_real_windows_recovery_cannot_race_active_producer(tmp_path):
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=_producer, args=(tmp_path, child))
    process.start()
    try:
        assert parent.poll(20) and parent.recv() == 'published'
        with pytest.raises(EnvironmentError):
            recover_publications(tmp_path / 'vault.db', b'B' * 32)
        assert len(journals(tmp_path)) == 1
    finally:
        parent.send('finish')
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0
    assert parent.poll(5) and parent.recv() == BatchFileStatus.COMPLETED.value
    assert not journals(tmp_path)
    assert recover_publications(tmp_path / 'vault.db', b'B' * 32) == 0
    parent.close()
    child.close()


@pytest.mark.parametrize('content', [None, b'', b'published output'])
def test_cleanup_only_removes_owned_claim_never_destination(tmp_path, content):
    reservation = reserve_output_file(tmp_path, tmp_path / 'input.csv')
    assert reservation.claim_path.exists()
    assert not reservation.path.exists()
    if content is not None:
        reservation.path.write_bytes(content)
    _remove_reservation(reservation)
    _remove_reservation(reservation)
    assert not reservation.claim_path.exists()
    if content is not None:
        assert reservation.path.read_bytes() == content
    else:
        assert not reservation.path.exists()


def test_claim_cannot_be_replaced_while_owned_and_names_remain_unique(tmp_path):
    first = reserve_output_file(tmp_path, tmp_path / 'input.csv')
    second = reserve_output_file(tmp_path, tmp_path / 'input.csv')
    try:
        assert first.path != second.path
        outsider = tmp_path / 'outsider'
        outsider.write_bytes(b'other file')
        with pytest.raises(OSError):
            os.replace(outsider, first.claim_path)
        assert outsider.read_bytes() == b'other file'
    finally:
        first.close()
        second.close()
    assert not list(tmp_path.glob('.dms-batch-reservation-*'))


def test_precommit_batch_failure_cleans_claim_temp_and_journal(tmp_path, monkeypatch):
    batch, items, profile, output, repo = batch_case(tmp_path)
    prepare = Publication.prepare

    def fail(self, temp):
        prepare(self, temp)
        raise OSError('before commit')

    monkeypatch.setattr(Publication, 'prepare', fail)
    batch.process(items, profile, output, FixedKeyProvider(), lambda: repo)
    assert items[0].status is BatchFileStatus.ERROR
    assert repo.count() == 0
    assert not list(output.iterdir())
    assert not journals(tmp_path)


def test_external_destination_created_at_publication_is_preserved(tmp_path, monkeypatch):
    import data_mask_studio.csv_tools.csv_anonymizer as module
    batch, items, profile, output, repo = batch_case(tmp_path)
    publish = module.publish

    def collision(temp, destination, overwrite):
        assert not overwrite
        destination.write_bytes(b'external file')
        publish(temp, destination, overwrite)

    monkeypatch.setattr(module, 'publish', collision)
    batch.process(items, profile, output, FixedKeyProvider(), lambda: repo)
    assert items[0].status is BatchFileStatus.ERROR
    destination = output / 'input_anonimizado.csv'
    assert destination.read_bytes() == b'external file'
    committed = counts(repo)
    assert repo.count() == 2
    with pytest.raises(PublicationError):
        recover_publications(repo.database_path, b'B' * 32)
    assert counts(repo) == committed
    assert destination.read_bytes() == b'external file'
    assert journals(tmp_path) and list(output.glob('*.tmp'))
    assert not list(output.glob('.dms-batch-reservation-*'))


def test_recovery_under_shared_lease_fails_without_upgrade_or_mutation(tmp_path):
    with environment_lease(tmp_path):
        with pytest.raises(EnvironmentError):
            recover_publications(tmp_path / 'vault.db', b'B' * 32)
    with environment_lease(tmp_path, exclusive=True):
        assert recover_publications(tmp_path / 'vault.db', b'B' * 32) == 0


def test_default_factory_under_shared_lease_never_attempts_recovery(tmp_path, monkeypatch):
    from data_mask_studio.vault import defaults
    from data_mask_studio.vault import VaultRepository, VaultCipher
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'B' * 32))
    monkeypatch.setattr(defaults, 'default_vault_directory', lambda: tmp_path)
    monkeypatch.setattr(defaults, 'default_database_path', lambda: repo.database_path)
    monkeypatch.setattr(defaults, 'LocalKeyProvider', lambda *_: FixedKeyProvider())
    monkeypatch.setattr(defaults, 'VaultKeyProvider', lambda *_: FixedKeyProvider())
    with environment_lease(tmp_path):
        assert defaults.create_default_vault_repository().count() == 0
        operations = tmp_path / 'publication-operations'
        operations.mkdir()
        journal = operations / '.dms-operation-pending.json'
        journal.write_bytes(b'preserve evidence')
        with pytest.raises(PublicationError, match='Reabra'):
            defaults.create_default_vault_repository()
        assert journal.read_bytes() == b'preserve evidence'


def _hold_claim(root, pipe):
    reservation = reserve_output_file(root, root / 'input.csv')
    pipe.send(str(reservation.claim_path))
    pipe.recv()
    reservation.close()


def test_windows_process_death_releases_only_claim(tmp_path):
    from pathlib import Path
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=_hold_claim, args=(tmp_path, child))
    process.start()
    try:
        assert parent.poll(20)
        claim = Path(parent.recv())
        destination = tmp_path / 'input_anonimizado.csv'
        destination.write_bytes(b'published')
        assert claim.exists()
    finally:
        process.terminate()
        process.join(10)
        parent.close()
        child.close()
    assert not claim.exists()
    assert destination.read_bytes() == b'published'


def test_batch_ambiguous_commit_preserves_evidence(tmp_path, monkeypatch):
    batch, items, profile, output, repo = batch_case(tmp_path)

    def fail(self):
        raise OSError('commit confirmation unavailable')

    monkeypatch.setattr(Publication, 'committed', fail)
    batch.process(items, profile, output, FixedKeyProvider(), lambda: repo)
    assert items[0].status is BatchFileStatus.ERROR
    assert repo.count() == 2
    committed = counts(repo)
    with pytest.raises(PublicationError, match='indeterminada'):
        recover_publications(repo.database_path, b'B' * 32)
    assert counts(repo) == committed
    assert journals(tmp_path) and list(output.glob('*.tmp'))
    assert not (output / 'input_anonimizado.csv').exists()
    assert not list(output.glob('.dms-batch-reservation-*'))
