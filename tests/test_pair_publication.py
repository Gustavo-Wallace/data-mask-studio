import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest

from data_mask_studio.csv_tools import csv_anonymizer as csv_module
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv, CSVAnonymizationError
from data_mask_studio.environment import EnvironmentError, GENERATION_FILE
from data_mask_studio.output_reservation import OutputReservation
from data_mask_studio.publication import (
    PairPublication, PublicationError, recover_publications, publish, _signature,
)
from data_mask_studio.transfer_package import read_package
from data_mask_studio.transfer_package.binding import verify_file_binding
from data_mask_studio.transfer_package.staging import PackageStagingRequest
from test_publication import case, counts, journals, KEY

PASSWORD = 'synthetic pair publication password'


def pair_case(root):
    source, destination, repo, kwargs = case(root)
    package = root / 'out.dmspackage'
    kwargs.update(transfer_package_request=PackageStagingRequest(PASSWORD),
                  transfer_package_destination=package)
    return source, destination, package, repo, kwargs


def pending(root, monkeypatch, published=()):
    source, destination, package, repo, kwargs = pair_case(root)
    def interrupt(self, publisher):
        for role in published:
            artifact = self.data if role == 'csv' else self.data['package']
            publish(Path(artifact['temp']), Path(artifact['destination']), False)
        raise OSError('synthetic interruption')
    with monkeypatch.context() as patch:
        patch.setattr(PairPublication, 'publish_pair', interrupt)
        with pytest.raises(CSVAnonymizationError, match='pendente'):
            anonymize_csv(source, destination, **kwargs)
    assert repo.count() == 1 and len(journals(root)) == 1
    assert not list(root.glob('.dms-batch-reservation-*'))
    return source, destination, package, repo, kwargs


def test_success_and_durable_order(tmp_path, monkeypatch):
    source, destination, package, repo, kwargs = pair_case(tmp_path)
    original_prepare = PairPublication.prepare_pair
    original_commit = PairPublication.committed
    events = []
    def prepare(self, *args):
        original_prepare(self, *args)
        assert repo.count() == 0
        assert len(self.reservations) == 2
        for reservation in self.reservations:
            assert reservation.claim_path.is_file()
            with pytest.raises(OSError):
                OutputReservation(reservation.path)
        envelope = json.loads(self.path.read_bytes())
        assert envelope['data']['state'] == 'READY'
        assert envelope['data']['operation_journal_version'] == 2
        payload = read_package(Path(self.data['package']['temp']), PASSWORD)
        verify_file_binding(Path(self.data['temp']), payload.masked_file)
        events.append('ready')
    def committed(self):
        assert repo.count() == 1 and not destination.exists() and not package.exists()
        original_commit(self)
        events.append('committed')
    original_publish = csv_module.publish
    def publishing(temp, final, overwrite):
        assert events[:2] == ['ready', 'committed'] and not overwrite
        events.append(final.suffix)
        original_publish(temp, final, overwrite)
    monkeypatch.setattr(PairPublication, 'prepare_pair', prepare)
    monkeypatch.setattr(PairPublication, 'committed', committed)
    monkeypatch.setattr(csv_module, 'publish', publishing)
    result = anonymize_csv(source, destination, **kwargs)
    assert events == ['ready', 'committed', '.csv', '.dmspackage']
    assert result.transfer_package_path == package
    payload = read_package(package, PASSWORD)
    verify_file_binding(destination, payload.masked_file)
    assert payload.masked_file == result.masked_file_binding
    assert payload.scalar_mappings[0].original_value == 'Alice'
    assert b'Alice' not in destination.read_bytes()
    assert not journals(tmp_path) and not list(tmp_path.glob('*.tmp'))
    assert not list(tmp_path.glob('.dms-batch-reservation-*'))


@pytest.mark.parametrize('published', [(), ('csv',), ('package',), ('csv', 'package')])
def test_recovery_all_states(tmp_path, monkeypatch, published):
    _, destination, package, repo, _ = pending(tmp_path, monkeypatch, published)
    before = counts(repo)
    data = json.loads(journals(tmp_path)[0].read_bytes())['data']
    expected = [Path(a['destination'] if Path(a['destination']).exists() else a['temp']).read_bytes()
                for a in (data, data['package'])]
    assert recover_publications(repo.database_path, KEY) == 1
    assert [destination.read_bytes(), package.read_bytes()] == expected
    verify_file_binding(destination, read_package(package, PASSWORD).masked_file)
    assert counts(repo) == before and recover_publications(repo.database_path, KEY) == 0
    assert not journals(tmp_path) and not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('target', ['csv', 'package'])
@pytest.mark.parametrize('overwrite', [False, True])
def test_conflicts_prevent_commit_even_with_overwrite(tmp_path, target, overwrite):
    source, destination, package, repo, kwargs = pair_case(tmp_path)
    protected = destination if target == 'csv' else package
    protected.write_bytes(b'user file')
    before = counts(repo)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs, overwrite=overwrite)
    assert protected.read_bytes() == b'user file' and counts(repo) == before
    assert not (package if target == 'csv' else destination).exists()
    assert not journals(tmp_path) and not list(tmp_path.glob('*.tmp'))


def test_second_reservation_failure_releases_first(tmp_path):
    source, destination, package, repo, kwargs = pair_case(tmp_path)
    occupied = OutputReservation(package)
    try:
        with pytest.raises(CSVAnonymizationError):
            anonymize_csv(source, destination, **kwargs)
        assert occupied.claim_path.exists()
        free = OutputReservation(destination)
        free.close()
        assert repo.count() == 0 and not destination.exists() and not package.exists()
        assert not journals(tmp_path) and not list(tmp_path.glob('*.tmp'))
    finally:
        occupied.close()


@pytest.mark.parametrize('target', ['csv', 'package'])
@pytest.mark.parametrize('place', ['temp', 'destination'])
def test_mismatches_fail_closed_before_any_publication(tmp_path, monkeypatch, target, place):
    _, destination, package, repo, _ = pending(tmp_path, monkeypatch)
    journal = journals(tmp_path)[0]
    evidence = journal.read_bytes()
    data = json.loads(evidence)['data']
    artifact = data if target == 'csv' else data['package']
    protected = Path(artifact[place])
    protected.write_bytes(b'unrelated bytes')
    before = counts(repo)
    with pytest.raises(PublicationError):
        recover_publications(repo.database_path, KEY)
    assert journal.read_bytes() == evidence and protected.read_bytes() == b'unrelated bytes'
    assert counts(repo) == before
    assert not (package if target == 'csv' else destination).exists()
    assert Path(data['temp']).exists() and Path(data['package']['temp']).exists()


@pytest.mark.parametrize('target', ['csv', 'package'])
def test_staging_tamper_before_ready_rolls_back(tmp_path, monkeypatch, target):
    source, destination, package, repo, kwargs = pair_case(tmp_path)
    original = PairPublication.prepare_pair
    def tamper(self, temp, package_temp, *args):
        (temp if target == 'csv' else package_temp).write_bytes(b'tampered')
        original(self, temp, package_temp, *args)
    monkeypatch.setattr(PairPublication, 'prepare_pair', tamper)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs)
    assert repo.count() == 0 and not destination.exists() and not package.exists()
    assert not journals(tmp_path) and not list(tmp_path.glob('*.tmp'))
    assert not list(tmp_path.glob('.dms-batch-reservation-*'))


def test_different_operation_package_cannot_be_substituted(tmp_path, monkeypatch):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    _, destination, package, repo, _ = pending(first, monkeypatch)
    source2, destination2, package2, _, kwargs2 = pair_case(second)
    source2.write_text('Name\nBob\n', encoding='utf-8')
    anonymize_csv(source2, destination2, **kwargs2)
    data = json.loads(journals(first)[0].read_bytes())['data']
    Path(data['package']['temp']).write_bytes(package2.read_bytes())
    with pytest.raises(PublicationError): recover_publications(repo.database_path, KEY)
    assert not destination.exists() and not package.exists() and journals(first)


@pytest.mark.parametrize('field', ['binding', 'role', 'destination', 'generation'])
def test_authenticated_invalid_v2_metadata_is_rejected(tmp_path, monkeypatch, field):
    _, destination, package, repo, _ = pending(tmp_path, monkeypatch)
    journal = journals(tmp_path)[0]
    envelope = json.loads(journal.read_bytes())
    data = envelope['data']
    if field == 'generation': data['generation'] = '0' * 64
    elif field == 'binding': data['package']['binding'] = dict(size=0, sha256='0' * 64)
    elif field == 'role': data['package']['role'] = 'masked_csv'
    else: data['package']['destination'] = data['destination']
    envelope['signature'] = _signature(data, KEY)
    journal.write_text(json.dumps(envelope), encoding='utf-8')
    with pytest.raises(PublicationError): recover_publications(repo.database_path, KEY)
    assert journal.exists() and not destination.exists() and not package.exists()


def test_changed_environment_generation_fences_recovery(tmp_path, monkeypatch):
    _, destination, package, repo, _ = pending(tmp_path, monkeypatch)
    (tmp_path / GENERATION_FILE).write_bytes(b'new generation')
    with pytest.raises(PublicationError, match='Geração'):
        recover_publications(repo.database_path, KEY)
    assert journals(tmp_path) and not destination.exists() and not package.exists()


@pytest.mark.parametrize('phase', ['ready', 'confirmation', 'cleanup'])
def test_failure_at_journal_boundaries(tmp_path, monkeypatch, phase):
    source, destination, package, repo, kwargs = pair_case(tmp_path)
    method = {'ready': 'prepare_pair', 'confirmation': 'committed', 'cleanup': 'complete'}[phase]
    original = getattr(PairPublication, method)
    def fail(self, *args):
        if phase == 'ready': original(self, *args)
        raise OSError('synthetic boundary failure')
    monkeypatch.setattr(PairPublication, method, fail)
    with pytest.raises(CSVAnonymizationError): anonymize_csv(source, destination, **kwargs)
    if phase == 'ready':
        assert repo.count() == 0 and not journals(tmp_path) and not list(tmp_path.glob('*.tmp'))
    elif phase == 'confirmation':
        assert repo.count() == 1
        with pytest.raises(PublicationError, match='indeterminada'):
            recover_publications(repo.database_path, KEY)
        assert not destination.exists() and not package.exists() and journals(tmp_path)
    else:
        assert destination.exists() and package.exists()
        assert recover_publications(repo.database_path, KEY) == 1


def _active_producer(root, pipe):
    source, destination, _, _, kwargs = pair_case(root)
    original = PairPublication.publish_pair
    def pause(self, publisher):
        pipe.send('committed')
        if not pipe.poll(30): raise TimeoutError('test handshake')
        pipe.recv()
        original(self, publisher)
    PairPublication.publish_pair = pause
    anonymize_csv(source, destination, **kwargs)
    pipe.send('complete')


def test_active_producer_retains_lease_and_both_reservations(tmp_path):
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=_active_producer, args=(tmp_path, child))
    process.start()
    try:
        assert parent.poll(20) and parent.recv() == 'committed'
        with pytest.raises(EnvironmentError): recover_publications(tmp_path / 'vault.db', KEY)
        for name in ('out.csv', 'out.dmspackage'):
            with pytest.raises(OSError): OutputReservation(tmp_path / name)
            candidate = tmp_path / 'competing.tmp'
            candidate.write_bytes(b'competing producer')
            with pytest.raises(OSError): publish(candidate, tmp_path / name, False)
            assert candidate.read_bytes() == b'competing producer'
            candidate.unlink()
        assert len(journals(tmp_path)) == 1
    finally:
        parent.send('finish')
        process.join(20)
        if process.is_alive(): process.terminate(); process.join()
    assert process.exitcode == 0 and parent.poll(5) and parent.recv() == 'complete'
    assert not journals(tmp_path)
    parent.close(); child.close()


def test_recovery_does_not_steal_second_reservation(tmp_path, monkeypatch):
    _, destination, package, repo, _ = pending(tmp_path, monkeypatch)
    claim = OutputReservation(package)
    try:
        with pytest.raises(PublicationError): recover_publications(repo.database_path, KEY)
        free = OutputReservation(destination)
        free.close()
        assert claim.claim_path.exists() and journals(tmp_path)
        assert not destination.exists() and not package.exists()
    finally:
        claim.close()
    assert recover_publications(repo.database_path, KEY) == 1


@pytest.mark.parametrize('stage', ['staged', 'ready', 'committed', 'csv', 'both'])
def test_process_crash_at_pair_boundaries(tmp_path, stage):
    _, destination, package, repo, _ = pair_case(tmp_path)
    program = '''
import os, sys
from pathlib import Path
from data_mask_studio.publication import PairPublication
from test_pair_publication import pair_case
from data_mask_studio.csv_tools import csv_anonymizer as module
root, stage = Path(sys.argv[1]), sys.argv[2]
source, destination, package, repo, kwargs = pair_case(root)
if stage in ('staged', 'ready', 'committed'):
    method = 'prepare_pair' if stage in ('staged', 'ready') else 'committed'
    original = getattr(PairPublication, method)
    def crash(self, *args):
        if stage != 'staged': original(self, *args)
        os._exit(73)
    setattr(PairPublication, method, crash)
else:
    original = module.publish
    def crash(temp, final, overwrite):
        original(temp, final, overwrite)
        if stage == 'csv' or final.suffix == '.dmspackage': os._exit(73)
    module.publish = crash
module.anonymize_csv(source, destination, **kwargs)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent.resolve()))
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path), stage],
                            env=env, capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode(errors='replace')
    assert not list(tmp_path.glob('.dms-batch-reservation-*'))
    before = counts(repo)
    if stage == 'staged':
        assert repo.count() == 0 and not journals(tmp_path)
        assert recover_publications(repo.database_path, KEY) == 0
        assert not destination.exists() and not package.exists()
    elif stage == 'ready':
        assert repo.count() == 0
        with pytest.raises(PublicationError, match='indeterminada'): recover_publications(repo.database_path, KEY)
        assert not destination.exists() and not package.exists() and journals(tmp_path)
    else:
        assert repo.count() == 1 and recover_publications(repo.database_path, KEY) == 1
        verify_file_binding(destination, read_package(package, PASSWORD).masked_file)
    assert counts(repo) == before


@pytest.mark.parametrize('target', [None, 'source', 'csv', 'wrong_extension', 'hardlink'])
def test_destination_contract(tmp_path, target):
    source, destination, package, repo, kwargs = pair_case(tmp_path)
    if target == 'hardlink': os.link(source, package)
    kwargs['transfer_package_destination'] = {
        None: None, 'source': source, 'csv': destination,
        'wrong_extension': tmp_path / 'out.zip', 'hardlink': package,
    }[target]
    with pytest.raises(CSVAnonymizationError): anonymize_csv(source, destination, **kwargs)
    assert repo.count() == 0 and not destination.exists() and not journals(tmp_path)


def test_separate_package_directory_and_no_sensitive_journal_fields(tmp_path, monkeypatch):
    source, destination, _, repo, kwargs = pair_case(tmp_path)
    directory = tmp_path / 'packages'
    directory.mkdir()
    kwargs['transfer_package_destination'] = directory / 'out.dmspackage'
    def fail(*args): raise OSError('pause')
    monkeypatch.setattr(csv_module, 'publish', fail)
    with pytest.raises(CSVAnonymizationError): anonymize_csv(source, destination, **kwargs)
    content = journals(tmp_path)[0].read_bytes()
    for secret in (PASSWORD.encode(), KEY, b'Alice', b'NAME-', b'V' * 32):
        assert secret not in content
    assert len(list(directory.glob('.dms-package-*.tmp'))) == 1
    assert recover_publications(repo.database_path, KEY) == 1
    verify_file_binding(destination, read_package(kwargs['transfer_package_destination'], PASSWORD).masked_file)


@pytest.mark.parametrize('target', ['csv', 'package'])
def test_external_final_race_never_overwrites(tmp_path, monkeypatch, target):
    source, destination, package, repo, kwargs = pair_case(tmp_path)
    link = os.link
    protected = destination if target == 'csv' else package
    def competing_link(temp, final):
        if final == protected:
            final.write_bytes(b'external final')
        link(temp, final)
    monkeypatch.setattr(os, 'link', competing_link)
    with pytest.raises(CSVAnonymizationError): anonymize_csv(source, destination, **kwargs)
    assert repo.count() == 1 and protected.read_bytes() == b'external final'
    with pytest.raises(PublicationError): recover_publications(repo.database_path, KEY)
    assert protected.read_bytes() == b'external final' and journals(tmp_path)
    if target == 'package': assert destination.exists()


def test_startup_recovers_pair_after_keys_and_before_schema_initialization(tmp_path, monkeypatch):
    from data_mask_studio.vault import initializer
    _, destination, package, repo, _ = pending(tmp_path, monkeypatch)
    events = []
    class Provider:
        def __init__(self, key): self.key = key
        def get_key(self):
            events.append('key')
            return self.key
    original = initializer.initialize_schema
    def schema(*args):
        assert events == ['key', 'key']
        assert destination.exists() and package.exists() and not journals(tmp_path)
        original(*args)
    monkeypatch.setattr(initializer, 'initialize_schema', schema)
    assert initializer.initialize_existing_vault(repo.database_path, Provider(b'V' * 32), Provider(KEY))


def test_pair_evidence_not_offered_as_generic_temporary(tmp_path, monkeypatch):
    from data_mask_studio.maintenance.temporary_cleanup import locate_temporaries
    pending(tmp_path, monkeypatch)
    assert not locate_temporaries(tmp_path, [tmp_path], now=10**12)
