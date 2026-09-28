import csv
import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig, generate_token
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv, CSVAnonymizationError, ProcessingCancelled
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource, build_processing_plan
from data_mask_studio.transfer_package import MaskedFileBinding, PackageError
from data_mask_studio.transfer_package.binding import compute_file_binding, verify_file_binding
from test_composite_csv import setup_case, output_rows, KEY


def assert_binding(result):
    raw = result.output_path.read_bytes()
    assert result.masked_file_binding == MaskedFileBinding(hashlib.sha256(raw).hexdigest(), len(raw))
    verify_file_binding(result.output_path, result.masked_file_binding)


def test_scalar_emission_exact_not_token_discovery(tmp_path):
    source = tmp_path / 'input.csv'
    preserved = 'OTHER-ABCDEFGHIJKL'
    source.write_text(f'A,B,C,D\na,b,{preserved},discard\na,c,{preserved},discard\n, ,{preserved},discard\n', encoding='utf-8')
    configs = [ColumnConfig('A', True, 'AA'), ColumnConfig('B', True, 'BB'),
               ColumnConfig('C'), ColumnConfig('D', action=Action.EXCLUDE)]
    result = anonymize_csv(source, tmp_path / 'output.csv', encoding='utf-8', delimiter=',',
                           configurations=configs, secret_key=KEY)
    expected = {generate_token(KEY, 'AA', 'a'), generate_token(KEY, 'BB', 'b'), generate_token(KEY, 'BB', 'c')}
    assert result.emitted_scalar_codes == tuple(sorted(expected))
    assert result.emitted_composite_codes == ()
    rows = output_rows(result.output_path)
    assert rows[0] == ['A', 'B', 'C']
    assert rows[1][0] == rows[2][0]
    assert rows[3] == ['', ' ', preserved]
    assert preserved not in result.emitted_scalar_codes
    assert_binding(result)
    with pytest.raises(FrozenInstanceError): result.emitted_scalar_codes = ()


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'utf-16', 'utf-32', 'cp1252'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_binding_uses_serialized_bytes_not_input_encoding(tmp_path, encoding, newline):
    source = tmp_path / 'input.csv'
    source.write_bytes(('Nome;Extra' + newline + 'João;"linha' + newline + 'interna"' + newline).encode(encoding))
    result = anonymize_csv(source, tmp_path / 'output.csv', encoding=encoding, delimiter=';',
                           configurations=[ColumnConfig('Nome', True, 'NAME'), ColumnConfig('Extra')], secret_key=KEY)
    assert result.output_path.read_bytes().startswith(b'\xef\xbb\xbf')
    assert result.emitted_scalar_codes == (generate_token(KEY, 'NAME', 'João'),)
    assert_binding(result)


@pytest.mark.parametrize('mixed', [False, True])
def test_composite_emission_deduplication_and_type_separation(tmp_path, mixed):
    configs = [ColumnConfig('NOME', action=Action.EXCLUDE),
               ColumnConfig('CPF', action=Action.MASK if mixed else Action.EXCLUDE, prefix='CPF_ID' if mixed else ''),
               ColumnConfig('IDADE')]
    source, destination, _, kwargs = setup_case(tmp_path, [
        ('Ana', '999.999.999-99', '20'), ('Ana', '999.999.999-99', '20'),
        ('Bea', '888.888.888-88', '21'), (' ', ' ', '22'),
    ], configs, second=True)
    result = anonymize_csv(source, destination, **kwargs)
    rows = output_rows(destination)[1:]
    expected_composites = {value for row in rows for value in row[-2:] if value}
    assert len(expected_composites) == 4
    assert result.emitted_composite_codes == tuple(sorted(expected_composites))
    expected_scalars = {row[0] for row in rows if row[0].strip()} if mixed else set()
    assert result.emitted_scalar_codes == tuple(sorted(expected_scalars))
    assert not set(result.emitted_scalar_codes) & set(result.emitted_composite_codes)
    assert_binding(result)


def test_no_mask_and_composite_preserve_do_not_track(tmp_path):
    source = tmp_path / 'input.csv'
    source.write_text('A,B,C\nAA-ABCDEFGHIJKL,Name,drop\n', encoding='utf-8')
    inspection = inspect_csv(source)
    configs = [ColumnConfig('A'), ColumnConfig('B'), ColumnConfig('C', action=Action.EXCLUDE)]
    composite = CompositeColumnConfig('Combined', '', (
        CompositeSource(source_ref_at(inspection, 0)), CompositeSource(source_ref_at(inspection, 1)),
    ), action=Action.PRESERVE)
    plan = build_processing_plan(inspection, configs, [composite])
    assert not plan.requires_masking
    result = anonymize_csv(source, tmp_path / 'output.csv', encoding='utf-8', delimiter=',', processing_plan=plan)
    assert result.emitted_scalar_codes == result.emitted_composite_codes == ()
    assert output_rows(result.output_path)[1][0] == 'AA-ABCDEFGHIJKL'
    assert_binding(result)
    assert not list(tmp_path.glob('*.dmspackage'))


@pytest.mark.parametrize('failure', ['row', 'cancel', 'publication'])
def test_failed_operation_has_no_successful_result(tmp_path, monkeypatch, failure):
    source, destination, repo, kwargs = setup_case(tmp_path, [('Ana', '99999999999', '20')])
    if failure == 'row':
        with source.open('a', encoding='utf-8') as stream: stream.write('bad,row\n')
    elif failure == 'cancel':
        emitted_rows = []
        kwargs['progress_callback'] = emitted_rows.append
        kwargs['should_cancel'] = lambda: bool(emitted_rows)
    else:
        def fail(*args): raise OSError('synthetic publication failure')
        monkeypatch.setattr('data_mask_studio.csv_tools.csv_anonymizer.publish', fail)
    result = None
    with pytest.raises(ProcessingCancelled if failure == 'cancel' else CSVAnonymizationError):
        result = anonymize_csv(source, destination, **kwargs)
    assert result is None and not destination.exists()
    if failure != 'publication':
        assert not list(tmp_path.glob('*.tmp'))
        assert not list(tmp_path.glob('.*.tmp'))


def test_binding_computed_before_publish_without_csv_reparse(tmp_path, monkeypatch):
    source, destination, _, kwargs = setup_case(tmp_path, [('Ana', '99999999999', '20')])
    import data_mask_studio.csv_tools.csv_anonymizer as module
    original = module.publish
    staged = []
    def publish(temp, target, overwrite):
        staged.append(compute_file_binding(temp))
        assert not target.exists()
        original(temp, target, overwrite)
    monkeypatch.setattr(module, 'publish', publish)
    result = anonymize_csv(source, destination, **kwargs)
    assert staged == [result.masked_file_binding]
    assert_binding(result)


@pytest.mark.parametrize('mutation', ['byte', 'truncate', 'append'])
def test_verification_rejects_changed_bytes(tmp_path, mutation):
    path = tmp_path / 'file.csv'
    path.write_bytes(b'A,B\r\n1,2\r\n')
    expected = compute_file_binding(path)
    changed = {'byte': b'A,B\r\n1,3\r\n', 'truncate': b'A,B\r\n', 'append': b'A,B\r\n1,2\r\n3,4\r\n'}[mutation]
    path.write_bytes(changed)
    assert compute_file_binding(path) != expected
    with pytest.raises(PackageError, match='não corresponde'): verify_file_binding(path, expected)


def test_missing_and_unreadable_files_fail_safely(tmp_path, monkeypatch):
    path = tmp_path / 'private-name.csv'
    expected = MaskedFileBinding('ab' * 32, 3)
    with pytest.raises(PackageError) as error: verify_file_binding(path, expected)
    assert path.name not in str(error.value)
    path.write_bytes(b'abc')
    def denied(*args, **kwargs): raise PermissionError('private detail')
    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(PackageError) as error: verify_file_binding(path, expected)
    assert 'private' not in str(error.value)


def test_binding_streams_bounded_reads(tmp_path, monkeypatch):
    path = tmp_path / 'large.csv'
    path.write_bytes(b'x' * (2 * 1024 * 1024 + 17))
    original = Path.open
    reads = []
    class Reader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def read(self, size):
            assert 0 < size <= 1024 * 1024
            reads.append(size)
            return self.stream.read(size)
    monkeypatch.setattr(Path, 'open', lambda self, *a, **kw: Reader(original(self, *a, **kw)))
    assert compute_file_binding(path).size == 2 * 1024 * 1024 + 17
    assert len(reads) == 4


@pytest.mark.parametrize('expected', [MaskedFileBinding('invalid', 0), MaskedFileBinding('ab' * 32, -1),
                                      MaskedFileBinding('ab' * 32, True), None])
def test_invalid_binding_is_rejected_without_reading(tmp_path, expected, monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('must not read')
    monkeypatch.setattr(Path, 'open', forbidden)
    with pytest.raises(PackageError): verify_file_binding(tmp_path / 'absent.csv', expected)


def test_binding_failure_does_not_publish_or_return_result(tmp_path, monkeypatch):
    source = tmp_path / 'input.csv'
    source.write_text('A\nx\n', encoding='utf-8')
    def fail(*args): raise PackageError('synthetic binding failure')
    monkeypatch.setattr('data_mask_studio.transfer_package.binding.compute_file_binding', fail)
    destination = tmp_path / 'output.csv'
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, encoding='utf-8', delimiter=',',
                      configurations=[ColumnConfig('A', True, 'AA')], secret_key=KEY)
    assert not destination.exists()
    assert not list(tmp_path.glob('.*.tmp'))
