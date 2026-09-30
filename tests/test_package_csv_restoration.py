import csv
import codecs
import io
from dataclasses import replace
from pathlib import Path

import pytest

from data_mask_studio.restoration.csv_restorer import restore_csv, restore_csv_from_package
from data_mask_studio.restoration.models import MissingCodePolicy, RepresentationPolicy
from data_mask_studio.restoration.exceptions import RestorationError, RestorationSecurityError, MissingCodeError, RestorationCancelled
from data_mask_studio.transfer_package import write_package, encrypt_package
from data_mask_studio.transfer_package.binding import compute_file_binding
from data_mask_studio.transfer_package.selection import select_mappings
from data_mask_studio.transfer_package import restoration_source as package_module
from data_mask_studio.vault import MappingCandidate, VaultRepository
from data_mask_studio.normalization import NormalizationRule
from test_composite_restoration import setup
from test_restoration import configuration

PASSWORD = 'synthetic restore package password'
SCALAR = 'NAME-ABCDEFGHIJKL'


def make_case(tmp_path, *, encoding='utf-8', ambiguous=False, delimiter=','):
    repo, composite = setup(tmp_path)
    if ambiguous:
        repo.composite_repository().upsert_composite_mapping(
            replace(composite, original_values=('GUSTAVO', '99999999999')))
    with repo.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(SCALAR, 'NAME', ' Synthetic Person ', 'Name',
            canonical_value='Synthetic Person', normalization_rule=NormalizationRule.COLLAPSE_WHITESPACE)])
    masked = tmp_path / 'masked.csv'
    with masked.open('w', encoding=encoding, newline='') as stream:
        csv.writer(stream, delimiter=delimiter).writerows([
            ['Name', 'Pair', 'Unselected'], [SCALAR, composite.code, 'keep'],
            [SCALAR, composite.code, 'keep2'], ['', ' ', 'keep3'], ['plain', 'common', 'keep4']])
    boms = {'utf-16-le': codecs.BOM_UTF16_LE, 'utf-16-be': codecs.BOM_UTF16_BE,
            'utf-32-le': codecs.BOM_UTF32_LE, 'utf-32-be': codecs.BOM_UTF32_BE}
    if encoding in boms:
        masked.write_bytes(boms[encoding] + masked.read_bytes())
    config = configuration(masked, encoding=encoding, delimiter=delimiter,
                           headers=('Name', 'Pair', 'Unselected'), indexes=(0, 1))
    payload = select_mappings(repo, scalar_codes=[SCALAR], composite_codes=[composite.code]).to_payload(compute_file_binding(masked))
    package = write_package(tmp_path / 'file.dmspackage', payload, PASSWORD)
    return repo, config, package, payload, tmp_path / 'restored.csv'


def read_rows(path, delimiter=','):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.reader(stream, delimiter=delimiter))


@pytest.mark.parametrize('ambiguous', [False, True])
@pytest.mark.parametrize('representation', list(RepresentationPolicy))
def test_real_mixed_restore_matches_local_bytes(tmp_path, ambiguous, representation):
    repo, config, package, _, output = make_case(tmp_path, ambiguous=ambiguous)
    config = replace(config, representation_policy=representation)
    original = config.source_path.read_bytes(), package.read_bytes()
    local = restore_csv(config, tmp_path / 'local.csv', repo)
    result = restore_csv_from_package(config, output, package, PASSWORD)
    assert result.output_path.read_bytes() == local.output_path.read_bytes()
    assert result.restored_codes == 4 and result.rows_processed == 4 and result.empty_cells == 2
    assert result.composite_restored_canonical == (2 if ambiguous else 0)
    assert result.composite_restored_exact == (0 if ambiguous else 2)
    assert result.missing_code_policy is MissingCodePolicy.ABORT
    assert (config.source_path.read_bytes(), package.read_bytes()) == original
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp1252', 'utf-16', 'utf-16-le', 'utf-16-be', 'utf-32', 'utf-32-le', 'utf-32-be'])
@pytest.mark.parametrize('delimiter', [',', ';'])
def test_encodings_delimiters_and_newlines(tmp_path, encoding, delimiter):
    _, config, package, _, output = make_case(tmp_path, encoding=encoding, delimiter=delimiter)
    restore_csv_from_package(config, output, package, PASSWORD)
    assert read_rows(output, delimiter)[1][0] == ' Synthetic Person '
    assert output.read_bytes().startswith(b'\xef\xbb\xbf')


def test_no_vault_environment_access_or_fallback(tmp_path, monkeypatch):
    _, config, package, _, output = make_case(tmp_path)
    def forbidden(*args, **kwargs): pytest.fail('Package restoration must not access vault/environment')
    monkeypatch.setattr(VaultRepository, '__init__', forbidden)
    monkeypatch.setattr(VaultRepository, 'as_read_only', forbidden)
    monkeypatch.setattr('data_mask_studio.environment.environment_lease', forbidden)
    result = restore_csv_from_package(config, output, package, PASSWORD)
    assert result.restored_codes == 4


@pytest.mark.parametrize('kind', ['scalar', 'composite'])
@pytest.mark.parametrize('policy', list(MissingCodePolicy))
def test_incomplete_package_aborts_even_when_vault_has_mapping(tmp_path, kind, policy):
    _, config, package, payload, output = make_case(tmp_path)
    payload = replace(payload, **{f'{kind}_mappings': ()})
    package.write_bytes(encrypt_package(payload, PASSWORD))
    with pytest.raises(MissingCodeError):
        restore_csv_from_package(replace(config, missing_code_policy=policy), output, package, PASSWORD)
    assert not output.exists() and not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('change', ['byte', 'truncate', 'append', 'encoding', 'same_name', 'other_package', 'password', 'tamper'])
def test_binding_and_authentication_failures_publish_nothing(tmp_path, change):
    _, config, package, payload, output = make_case(tmp_path)
    password = PASSWORD
    original = config.source_path.read_bytes()
    if change == 'byte': config.source_path.write_bytes(original.replace(b'keep', b'FAKE'))
    elif change == 'truncate': config.source_path.write_bytes(original[:-1])
    elif change == 'append': config.source_path.write_bytes(original + b'extra')
    elif change == 'encoding': config.source_path.write_bytes(original.decode().encode('utf-16'))
    elif change == 'same_name':
        other = tmp_path / 'other'; other.mkdir()
        path = other / config.source_path.name; path.write_bytes(b'different content')
        config = replace(config, source_path=path)
    elif change == 'other_package':
        unrelated = tmp_path / 'other.csv'; unrelated.write_bytes(b'other operation')
        package.write_bytes(encrypt_package(replace(payload, masked_file=compute_file_binding(unrelated)), PASSWORD))
    elif change == 'password': password = 'wrong synthetic password'
    else: package.write_bytes(package.read_bytes()[:-1] + bytes([package.read_bytes()[-1] ^ 1]))
    with pytest.raises(RestorationSecurityError) as error:
        restore_csv_from_package(config, output, package, password)
    assert not output.exists() and not list(tmp_path.glob('*.tmp'))
    assert PASSWORD not in str(error.value) and 'Synthetic Person' not in str(error.value)


@pytest.mark.parametrize('change', ['replace', 'delete'])
def test_restoration_consumes_verified_snapshot_not_reopened_path(tmp_path, monkeypatch, change):
    _, config, package, _, output = make_case(tmp_path)
    verify = package_module.verify_binding
    original_open = Path.open
    reads = []
    def opened(self, *args, **kwargs):
        if self == config.source_path: reads.append(True)
        return original_open(self, *args, **kwargs)
    def verified(*args):
        verify(*args)
        config.source_path.unlink()
        if change == 'replace':
            with original_open(config.source_path, 'wb') as stream: stream.write(b'unverified content')
    monkeypatch.setattr(package_module, 'verify_binding', verified)
    monkeypatch.setattr(Path, 'open', opened)
    result = restore_csv_from_package(config, output, package, PASSWORD)
    assert result.restored_codes == 4 and reads == [True]
    assert read_rows(output)[1][0] == ' Synthetic Person '


@pytest.mark.parametrize('failure', ['cancel_copy', 'cancel_restore', 'publish', 'parse', 'binding'])
def test_snapshot_and_output_staging_cleaned(tmp_path, monkeypatch, failure):
    _, config, package, payload, output = make_case(tmp_path)
    if failure == 'parse':
        config.source_path.write_text('Name,Pair,Unselected\nextra,columns,here,bad\n', encoding='utf-8')
        package.write_bytes(encrypt_package(replace(payload, masked_file=compute_file_binding(config.source_path)), PASSWORD))
    if failure == 'binding': config.source_path.write_bytes(b'bad')
    handles = []
    original_temp = package_module.tempfile.TemporaryFile
    def temporary(*args, **kwargs):
        stream = original_temp(*args, **kwargs)
        handles.append(stream)
        return stream
    monkeypatch.setattr(package_module.tempfile, 'TemporaryFile', temporary)
    cancelled = []
    def progress(_): cancelled.append(True)
    if failure == 'publish':
        def fail(*args): raise OSError('synthetic publication failure')
        monkeypatch.setattr('data_mask_studio.restoration.csv_restorer.publish', fail)
    def cancel():
        return (bool(handles) if failure == 'cancel_copy' else bool(cancelled) if failure == 'cancel_restore' else False)
    with pytest.raises(RestorationError):
        restore_csv_from_package(config, output, package, PASSWORD, should_cancel=cancel, progress_callback=progress)
    assert handles and all(handle.closed for handle in handles)
    assert not output.exists() and not list(tmp_path.glob('*.tmp'))
    assert package.exists() and config.source_path.exists()


def test_snapshot_contains_only_masked_bytes_and_has_bounded_reads(tmp_path, monkeypatch):
    _, config, package, _, output = make_case(tmp_path)
    original_open, temp = Path.open, package_module.tempfile.TemporaryFile
    snapshots = []
    def temporary(*args, **kwargs):
        stream = temp(*args, **kwargs); snapshots.append(stream); return stream
    original = config.source_path.read_bytes()
    class BoundedReader(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)
    def opened(self, *args, **kwargs):
        return BoundedReader(original) if self == config.source_path else original_open(self, *args, **kwargs)
    verify = package_module.verify_binding
    def verified(*args):
        snapshot = snapshots[0]
        position = snapshot.tell(); snapshot.seek(0)
        assert snapshot.read() == original
        snapshot.seek(position)
        verify(*args)
    monkeypatch.setattr(package_module.tempfile, 'TemporaryFile', temporary)
    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(package_module, 'verify_binding', verified)
    restore_csv_from_package(config, output, package, PASSWORD)
    assert all(s.closed for s in snapshots)


@pytest.mark.parametrize('alias', ['csv', 'package'])
def test_source_alias_protected_even_with_overwrite(tmp_path, alias):
    _, config, package, _, _ = make_case(tmp_path)
    destination = config.source_path if alias == 'csv' else package
    original = destination.read_bytes()
    with pytest.raises(RestorationError):
        restore_csv_from_package(config, destination, package, PASSWORD, overwrite=True)
    assert destination.read_bytes() == original


def test_destination_conflict_and_explicit_overwrite(tmp_path):
    _, config, package, _, output = make_case(tmp_path)
    output.write_bytes(b'existing file')
    with pytest.raises(RestorationError): restore_csv_from_package(config, output, package, PASSWORD)
    assert output.read_bytes() == b'existing file'
    restore_csv_from_package(config, output, package, PASSWORD, overwrite=True)
    assert read_rows(output)[1][0] == ' Synthetic Person '


def test_no_overwrite_race_preserves_external_final(tmp_path, monkeypatch):
    import os
    _, config, package, _, output = make_case(tmp_path)
    link = os.link
    def race(temp, destination):
        destination.write_bytes(b'external file')
        link(temp, destination)
    monkeypatch.setattr(os, 'link', race)
    with pytest.raises(RestorationError): restore_csv_from_package(config, output, package, PASSWORD)
    assert output.read_bytes() == b'external file' and not list(tmp_path.glob('*.tmp'))


def test_conflicting_package_types_rejected_before_output(tmp_path):
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from data_mask_studio.transfer_package import service
    from data_mask_studio.transfer_package.serialization import encode_payload
    _, config, package, payload, output = make_case(tmp_path)
    document = json.loads(encode_payload(payload))
    document['composite_mappings'][0]['code'] = SCALAR
    salt, nonce = b's' * 16, b'n' * 12
    header = service.HEADER.pack(service.MAGIC, 1, salt, nonce)
    package.write_bytes(header + AESGCM(service._key(PASSWORD, salt)).encrypt(nonce, json.dumps(document).encode(), header))
    with pytest.raises(RestorationSecurityError): restore_csv_from_package(config, output, package, PASSWORD)
    assert not output.exists()


@pytest.mark.parametrize('index', [0, 1])
def test_only_selected_scalar_or_composite_column_is_restored(tmp_path, index):
    _, config, package, _, output = make_case(tmp_path)
    config = replace(config, selected_columns=(config.selected_columns[index],))
    before = read_rows(config.source_path)
    result = restore_csv_from_package(config, output, package, PASSWORD)
    after = read_rows(output)
    assert result.restored_codes == 2
    assert after[1][index] != before[1][index]
    assert after[1][1 - index] == before[1][1 - index]


def test_missing_mapping_after_successful_window_removes_staging(tmp_path, monkeypatch):
    from data_mask_studio.restoration import csv_restorer as module
    _, config, package, payload, output = make_case(tmp_path)
    rows = read_rows(config.source_path)
    rows[2][0] = 'MISSING-ABCDEFGHIJKL'
    with config.source_path.open('w', encoding='utf-8', newline='') as stream:
        csv.writer(stream).writerows(rows)
    package.write_bytes(encrypt_package(replace(payload, masked_file=compute_file_binding(config.source_path)), PASSWORD))
    monkeypatch.setattr(module, 'BALANCED_SETTINGS', replace(module.BALANCED_SETTINGS, restoration_window_rows=1))
    progress = []
    with pytest.raises(MissingCodeError):
        restore_csv_from_package(config, output, package, PASSWORD, progress_callback=progress.append)
    assert len(progress) == 1 and progress[0].rows_processed == 1
    assert not output.exists() and not list(tmp_path.glob('*.tmp'))
