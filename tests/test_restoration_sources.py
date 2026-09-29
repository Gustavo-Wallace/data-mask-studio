import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from data_mask_studio.restoration.exceptions import MissingCodeError, RestorationSecurityError
from data_mask_studio.restoration.sources import open_vault_source
from data_mask_studio.transfer_package import (
    TransferPayload, ScalarRestorationMapping, CompositeRestorationMapping, write_package,
)
from data_mask_studio.transfer_package.binding import compute_file_binding
from data_mask_studio.transfer_package.restoration_source import TransferPackageRestorationSource
from data_mask_studio.transfer_package.serialization import encode_payload, HARD_PAYLOAD_LIMIT
from data_mask_studio.vault import MappingCandidate, VaultRepository
from test_transfer_package import seal_plaintext, PASSWORD

SCALAR = 'NAME-ABCDEFGHIJKL'
SECOND = 'NAME-BCDEFGHIJKLM'
COMPOSITE = 'PAIR-ABCDEFGHIJKL'
MISSING = 'MISSING-ABCDEFGHIJKL'


@pytest.fixture
def package_case(tmp_path):
    csv = tmp_path / 'masked.csv'
    csv.write_bytes(f'NAME,PAIR\n{SCALAR},{COMPOSITE}\n'.encode())
    payload = TransferPayload('1.2.0', compute_file_binding(csv), (
        ScalarRestorationMapping(SCALAR, ' Synthetic Person ', 'Synthetic Person'),
        ScalarRestorationMapping(SECOND, 'Second Original', 'second canonical'),
    ), (CompositeRestorationMapping(COMPOSITE, 1, ('synthetic', 'city'), ('Synthetic', 'City')),))
    package = write_package(tmp_path / 'file.dmspackage', payload, PASSWORD)
    return package, csv, payload


def test_success_multiple_lookups_no_plaintext_files_or_sensitive_repr(package_case, tmp_path, monkeypatch):
    package, csv, payload = package_case
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    def forbidden(*a, **kw): pytest.fail('Package source must never consult a vault')
    monkeypatch.setattr(VaultRepository, '__init__', forbidden)
    monkeypatch.setattr(VaultRepository, 'read_session', forbidden)
    source = TransferPackageRestorationSource(package, PASSWORD, csv)
    first, second = source.get_scalar(SCALAR), source.get_scalar(SECOND)
    assert (first.original_value, first.canonical_value) == (' Synthetic Person ', 'Synthetic Person')
    assert second.original_value == 'Second Original'
    assert source.get_scalar(SCALAR) == first
    composite = source.get_composite(COMPOSITE)
    assert composite.original_values == ('Synthetic', 'City')
    assert composite.canonical_values == ('synthetic', 'city')
    assert composite.identity_version == 1
    for text in (repr(source), repr(first), repr(second), repr(composite)):
        for secret in ('Synthetic', 'canonical', 'City', PASSWORD, SCALAR): assert secret not in text
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert not hasattr(source, 'password') and not hasattr(source, 'payload')
    for method in (source.get_scalar, source.get_composite):
        with pytest.raises(MissingCodeError) as error: method(MISSING)
        assert MISSING not in str(error.value)


def test_null_composite_original_remains_null(package_case):
    package, csv, payload = package_case
    payload = replace(payload, composite_mappings=(replace(payload.composite_mappings[0], original_values=None),))
    package.write_bytes(seal_plaintext(encode_payload(payload)))
    value = TransferPackageRestorationSource(package, PASSWORD, csv).get_composite(COMPOSITE)
    assert value.original_values is None and value.canonical_values == ('synthetic', 'city')


@pytest.mark.parametrize('method,code', [('get_scalar', COMPOSITE), ('get_composite', SCALAR)])
def test_type_separation(package_case, method, code):
    package, csv, _ = package_case
    source = TransferPackageRestorationSource(package, PASSWORD, csv)
    with pytest.raises(RestorationSecurityError) as error: getattr(source, method)(code)
    assert code not in str(error.value)


@pytest.mark.parametrize('mutation', ['changed', 'truncated', 'appended', 'same_name', 'missing', 'unreadable'])
def test_binding_failure_exposes_no_source(package_case, tmp_path, monkeypatch, mutation):
    package, csv, _ = package_case
    if mutation == 'changed': csv.write_bytes(csv.read_bytes().replace(b'NAME', b'FAKE'))
    elif mutation == 'truncated': csv.write_bytes(csv.read_bytes()[:-1])
    elif mutation == 'appended': csv.write_bytes(csv.read_bytes() + b'extra')
    elif mutation == 'missing': csv.unlink()
    elif mutation == 'same_name':
        other = tmp_path / 'other'; other.mkdir()
        csv = other / csv.name; csv.write_bytes(b'not the original')
    else:
        original = Path.open
        def fail(self, *a, **kw):
            if self == csv: raise PermissionError('private path')
            return original(self, *a, **kw)
        monkeypatch.setattr(Path, 'open', fail)
    with pytest.raises(RestorationSecurityError) as error:
        TransferPackageRestorationSource(package, PASSWORD, csv)
    for secret in (PASSWORD, 'Synthetic', str(csv), 'private'): assert secret not in str(error.value)


def test_binding_runs_before_mapping_indexes_exist(package_case, monkeypatch):
    package, csv, _ = package_case
    from data_mask_studio.transfer_package import restoration_source as module
    original = module.verify_file_binding
    instance = TransferPackageRestorationSource.__new__(TransferPackageRestorationSource)
    observed = []
    def verify(*args):
        assert not hasattr(instance, '_scalars') and not hasattr(instance, '_composites')
        original(*args)
        observed.append(True)
    monkeypatch.setattr(module, 'verify_file_binding', verify)
    instance.__init__(package, PASSWORD, csv)
    assert observed == [True] and instance.get_scalar(SCALAR)


@pytest.mark.parametrize('mutation', ['password', 'tamper', 'version', 'malformed', 'duplicate', 'conflict', 'scalar', 'composite'])
def test_invalid_package_rejected(package_case, mutation):
    package, csv, payload = package_case
    password = PASSWORD
    if mutation == 'password': password = 'wrong synthetic password'
    elif mutation in ('tamper', 'version'):
        from data_mask_studio.transfer_package.service import MAGIC
        data = bytearray(package.read_bytes())
        data[-1 if mutation == 'tamper' else len(MAGIC) + 1] ^= 1
        package.write_bytes(data)
    else:
        document = json.loads(encode_payload(payload))
        if mutation == 'duplicate': document['scalar_mappings'].append(document['scalar_mappings'][0])
        elif mutation == 'conflict': document['composite_mappings'][0]['code'] = SCALAR
        elif mutation == 'scalar': document['scalar_mappings'][0]['original_value'] = 123
        elif mutation == 'composite': document['composite_mappings'][0]['original_values'] = ['wrong arity']
        raw = b'not JSON' if mutation == 'malformed' else json.dumps(document).encode()
        package.write_bytes(seal_plaintext(raw))
    with pytest.raises(RestorationSecurityError) as error:
        TransferPackageRestorationSource(package, password, csv)
    for secret in (PASSWORD, 'Synthetic', SCALAR): assert secret not in str(error.value)


@pytest.mark.parametrize('budget', [0, True, 1, HARD_PAYLOAD_LIMIT + 1])
def test_invalid_or_exceeded_budget(package_case, budget):
    package, csv, _ = package_case
    with pytest.raises(RestorationSecurityError):
        TransferPackageRestorationSource(package, PASSWORD, csv, max_payload_bytes=budget)


def test_exact_payload_boundary_and_explicit_higher_budget(package_case):
    package, csv, payload = package_case
    size = len(encode_payload(payload))
    with pytest.raises(RestorationSecurityError):
        TransferPackageRestorationSource(package, PASSWORD, csv, max_payload_bytes=size - 1)
    for budget in (size, 65 * 1024 * 1024, HARD_PAYLOAD_LIMIT):
        assert TransferPackageRestorationSource(package, PASSWORD, csv, max_payload_bytes=budget).get_scalar(SCALAR)


@pytest.mark.parametrize('ambiguous', [False, True])
def test_vault_adapter_matches_exported_package(tmp_path, ambiguous):
    from test_composite_restoration import setup
    from data_mask_studio.transfer_package.selection import select_mappings
    repo, item = setup(tmp_path)
    if ambiguous:
        repo.composite_repository().upsert_composite_mapping(replace(item, original_values=('GUSTAVO', '99999999999')))
    with repo.transaction() as tx:
        tx.upsert_batch([MappingCandidate(SCALAR, 'NAME', 'Original', 'Name')])
    csv = tmp_path / 'masked.csv'; csv.write_bytes(b'synthetic masked bytes')
    payload = select_mappings(repo, scalar_codes=[SCALAR], composite_codes=[item.code]).to_payload(compute_file_binding(csv))
    package = write_package(tmp_path / 'file.dmspackage', payload, PASSWORD)
    package_source = TransferPackageRestorationSource(package, PASSWORD, csv)
    before = repo.database_path.read_bytes()
    with open_vault_source(repo) as local:
        assert local.get_scalar(SCALAR) == package_source.get_scalar(SCALAR)
        assert local.get_composite(item.code) == package_source.get_composite(item.code)
        assert (local.get_composite(item.code).original_values is None) == ambiguous
        for method in (local.get_scalar, local.get_composite):
            with pytest.raises(MissingCodeError): method(MISSING)
        with pytest.raises(RestorationSecurityError): local.get_scalar(item.code)
        with pytest.raises(RestorationSecurityError): local.get_composite(SCALAR)
    assert repo.database_path.read_bytes() == before
    with pytest.raises(RestorationSecurityError): local.get_scalar(SCALAR)


def test_vault_adapter_pins_one_snapshot(tmp_path):
    from data_mask_studio.vault import VaultCipher
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'R' * 32))
    with repo.transaction() as tx: tx.upsert_batch([MappingCandidate(SCALAR, 'NAME', 'First', 'Name')])
    with open_vault_source(repo) as source:
        assert source.get_scalar(SCALAR).original_value == 'First'
        with repo.transaction() as tx: tx.upsert_batch([MappingCandidate(SECOND, 'NAME', 'Second', 'Name')])
        with pytest.raises(MissingCodeError): source.get_scalar(SECOND)
        assert source.get_scalar(SCALAR).original_value == 'First'
    with open_vault_source(repo) as source: assert source.get_scalar(SECOND).original_value == 'Second'


def test_fresh_imports_and_local_adapter_has_no_package_dependency():
    code = '''
import sys
import data_mask_studio.restoration.sources
assert not any(name.startswith('data_mask_studio.transfer_package') for name in sys.modules)
import data_mask_studio.transfer_package.restoration_source
import data_mask_studio.processing
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


def test_scalar_and_composite_share_snapshot(tmp_path):
    from test_composite_restoration import setup
    repo, item = setup(tmp_path)
    with repo.transaction() as tx: tx.upsert_batch([MappingCandidate(SCALAR, 'NAME', 'First', 'Name')])
    with open_vault_source(repo) as source:
        source.get_scalar(SCALAR)  # pin snapshot before the composite changes
        repo.composite_repository().upsert_composite_mapping(replace(item, original_values=('GUSTAVO', '99999999999')))
        assert source.get_composite(item.code).original_values == item.original_values
    with open_vault_source(repo) as source:
        assert source.get_composite(item.code).original_values is None


def test_vault_authentication_failure_is_safe_and_session_closes(tmp_path):
    import sqlite3
    from test_composite_restoration import setup
    repo, item = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute("UPDATE composite_mappings SET encrypted_value=X'00'")
    with pytest.raises(RestorationSecurityError) as error:
        with open_vault_source(repo) as source:
            source.get_composite(item.code)
    assert item.code not in str(error.value) and 'Gustavo' not in str(error.value)
    with pytest.raises(RestorationSecurityError, match='fechada'): source.get_composite(item.code)
