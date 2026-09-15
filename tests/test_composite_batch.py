import csv
import json
from dataclasses import replace

import pytest

from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig
from data_mask_studio.batch import BatchError, BatchFile, BatchFileStatus as Status, BatchService, CancellationRequest
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.source_binding import SourceColumnRef, source_ref_at
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository

KEY = b'B' * 32


class Resources:
    def __init__(self, tmp_path):
        self.path = tmp_path / 'vault.db'
        self.key_calls = self.vault_calls = 0
        self.repository = None

    def get_key(self):
        self.key_calls += 1
        return KEY

    def vault(self):
        self.vault_calls += 1
        self.repository = VaultRepository(self.path, VaultCipher(b'V' * 32))
        return self.repository


def write_csv(path, headers, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def setup_profile(tmp_path, headers=('NOME', 'CPF', 'IDADE'), configs=None, indexes=(0, 1), multiple=False):
    template = write_csv(tmp_path / 'template.csv', headers, [['x'] * len(headers)])
    inspection = inspect_csv(template)
    configs = configs or [ColumnConfig(h, action=Action.PRESERVE if h == 'IDADE' else Action.EXCLUDE)
                          for h in dict.fromkeys(inspection.headers)]
    first = CompositeColumnConfig('PESSOA', 'CORR', (
        CompositeSource(source_ref_at(inspection, indexes[0]), Rule.PERSON_NAME),
        CompositeSource(source_ref_at(inspection, indexes[1]), Rule.CPF),
    ))
    definitions = [first]
    if multiple:
        definitions.append(CompositeColumnConfig('OUTRA', 'OTHER', first.components[::-1]))
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    service.create('Perfil composto batch', configs, composites=definitions)
    return service, service.list_profiles()[0]


def read_output(item):
    with item.output_path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.reader(stream))


def process(tmp_path, service, profile, sources, **kwargs):
    batch = BatchService(service)
    files = [BatchFile(path) for path in sources]
    batch.validate(files, profile)
    output = tmp_path / 'out'
    output.mkdir()
    resources = Resources(tmp_path)
    summary = batch.process(files, profile, output, resources, resources.vault, **kwargs)
    return files, summary, resources


def test_two_files_same_canonical_share_mapping_and_keep_profile(tmp_path, monkeypatch):
    service, profile = setup_profile(tmp_path)
    before = service.repository.path.read_bytes()
    a = write_csv(tmp_path / 'a.csv', ['NOME', 'CPF', 'IDADE'], [['Gustavo Wallace', '999.999.999-99', '24']])
    b = write_csv(tmp_path / 'b.csv', ['IDADE', 'CPF', 'NOME'], [['25', '99999999999', 'GUSTAVO WALLACE']])
    originals = [path.read_bytes() for path in (a, b)]
    import data_mask_studio.batch.service as module
    actual = module.anonymize_csv
    plans = []

    def observe(*args, **kwargs):
        # Nunca executar como scalar-only silenciosamente.
        plans.append(kwargs['processing_plan'])
        assert plans[-1].outputs[-1].identifier == profile.composites[0].identifier
        return actual(*args, **kwargs)

    monkeypatch.setattr(module, 'anonymize_csv', observe)
    files, summary, resources = process(tmp_path, service, profile, [a, b])
    token = generate_composite_token(KEY, 'CORR', ('gustavo wallace', '99999999999'))
    assert read_output(files[0]) == [['IDADE', 'PESSOA'], ['24', token]]
    assert read_output(files[1]) == [['IDADE', 'PESSOA'], ['25', token]]
    assert [c.input_index for c in plans[0].outputs[-1].components] == [0, 1]
    assert [c.input_index for c in plans[1].outputs[-1].components] == [2, 1]
    mapping = resources.repository.composite_repository().get_composite_mapping(token)
    assert mapping.occurrence_count == 2
    assert [v.occurrence_count for v in mapping.variations] == [1, 1]
    assert [v.original_values for v in mapping.variations] == [
        ('Gustavo Wallace', '999.999.999-99'), ('GUSTAVO WALLACE', '99999999999')]
    assert resources.key_calls == resources.vault_calls == 1
    assert summary.completed_files == 2
    assert all(r.processing_result.composite_mapping_occurrences == 1 for r in summary.results)
    assert service.repository.path.read_bytes() == before
    assert service.list_profiles()[0] == profile
    assert [path.read_bytes() for path in (a, b)] == originals
    assert not service.apply(profile, ['NOME', 'CPF', 'IDADE']).is_complete


@pytest.mark.parametrize('only', [True, False])
def test_composite_only_or_mixed_scalars_and_multiple_outputs(tmp_path, only):
    configs = [ColumnConfig('NOME', action=Action.EXCLUDE),
               ColumnConfig('CPF', action=Action.EXCLUDE if only else Action.MASK, prefix='CPF_ID'),
               ColumnConfig('IDADE', action=Action.EXCLUDE if only else Action.PRESERVE, output_name='ANOS')]
    service, profile = setup_profile(tmp_path, configs=configs, multiple=True)
    path = write_csv(tmp_path / 'input.csv', ['NOME', 'CPF', 'IDADE'], [['Ana', '99999999999', '24']])
    files, summary, resources = process(tmp_path, service, profile, [path])
    headers, row = read_output(files[0])
    assert headers == (['PESSOA', 'OUTRA'] if only else ['CPF', 'ANOS', 'PESSOA', 'OUTRA'])
    assert row[-2] != row[-1]
    assert resources.repository.count() == (0 if only else 1)
    assert all(resources.repository.composite_repository().get_composite_mapping(code) for code in row[-2:])
    result = summary.results[0].processing_result
    assert result.composite_tokens_generated == 2
    assert result.scalar_mapping_occurrences == (0 if only else 1)


@pytest.mark.parametrize('kind', ['extra', 'missing_scalar', 'missing_composite', 'all_excluded'])
def test_invalid_profile_compatibility_never_accesses_resources(tmp_path, kind):
    service, profile = setup_profile(tmp_path)
    headers = ['NOME', 'CPF', 'IDADE']
    if kind == 'extra': headers.append('SEGREDO')
    elif kind == 'missing_scalar': headers.remove('IDADE')
    elif kind == 'missing_composite':
        composite = profile.composites[0]
        profile = replace(profile, composites=(replace(composite, components=(
            CompositeSource(SourceColumnRef('AUSENTE')), composite.components[1],
        )),))
    elif kind == 'all_excluded':
        profile = replace(profile, composites=(), columns=tuple(replace(c, action=Action.EXCLUDE) for c in profile.columns))
    path = write_csv(tmp_path / 'input.csv', headers, [['PRIVATE_VALUE'] * len(headers)])
    batch = BatchService(service)
    files = [BatchFile(path)]
    batch.validate(files, profile)
    assert files[0].status is Status.INCOMPATIBLE
    resources = Resources(tmp_path)
    with pytest.raises(BatchError):
        batch.process(files, profile, tmp_path / 'out', resources, resources.vault)
    assert resources.key_calls == resources.vault_calls == 0
    assert not resources.path.exists() and not (tmp_path / 'out').exists()
    assert 'PRIVATE_VALUE' not in files[0].result_message


def test_revalidation_rejects_new_header_before_keys_or_output(tmp_path):
    service, profile = setup_profile(tmp_path)
    path = write_csv(tmp_path / 'input.csv', ['NOME', 'CPF', 'IDADE'], [['Ana', '99999999999', '24']])
    batch = BatchService(service)
    files = [BatchFile(path)]
    batch.validate(files, profile)
    write_csv(path, ['NOME', 'CPF', 'IDADE', 'SEGREDO'], [['Ana', '99999999999', '24', 'PRIVATE_VALUE']])
    resources = Resources(tmp_path)
    summary = batch.process(files, profile, tmp_path / 'absent-output', resources, resources.vault)
    assert summary.incompatible_files == 1
    assert resources.key_calls == resources.vault_calls == 0
    assert not (tmp_path / 'absent-output').exists()


def test_duplicate_header_explicit_occurrence_is_not_first_match(tmp_path):
    service, profile = setup_profile(tmp_path, ('NOME', 'NOME', 'CPF'), indexes=(1, 2))
    path = write_csv(tmp_path / 'duplicate.csv', ['NOME', 'NOME', 'CPF'], [['Wrong', 'Ana', '99999999999']])
    files, _, resources = process(tmp_path, service, profile, [path])
    token = read_output(files[0])[1][0]
    assert resources.repository.composite_repository().get_composite_mapping(token).canonical_values == ('ana', '99999999999')


@pytest.mark.parametrize('change', ['none', 'literal', 'reorder'])
def test_synthetic_source_provenance(tmp_path, change):
    service, profile = setup_profile(tmp_path, ('', 'CPF', 'IDADE'))
    headers = ['', 'CPF', 'IDADE']
    if change == 'literal': headers[0] = 'column_1'
    if change == 'reorder': headers = ['CPF', '', 'IDADE']
    path = write_csv(tmp_path / 'synthetic.csv', headers, [['Ana', '99999999999', '24']])
    batch = BatchService(service)
    files = [BatchFile(path)]
    batch.validate(files, profile)
    if change == 'none':
        files, _, resources = process(tmp_path, service, profile, [path])
        assert files[0].status is Status.COMPLETED
        assert resources.repository.composite_repository().get_composite_mapping(read_output(files[0])[1][-1])
    else:
        assert files[0].status is Status.INCOMPATIBLE


@pytest.mark.parametrize('legacy', [False, True])
def test_scalar_profiles_v1_and_v2_still_run(tmp_path, legacy):
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    service.create('Escalar legado', [ColumnConfig('NOME', True, 'NAME')])
    if legacy:
        document = json.loads(service.repository.path.read_text(encoding='utf-8'))
        document['schema_version'] = 1
        raw = document['profiles'][0]
        raw['format_version'] = 1
        del raw['composites'], raw['unknown_column_policy']
        for column in raw['columns']:
            column['anonymize'] = column.pop('action') == 'mask'
        service.repository.path.write_text(json.dumps(document), encoding='utf-8')
    before = service.repository.path.read_bytes()
    profile = service.list_profiles()[0]
    path = write_csv(tmp_path / 'scalar.csv', ['NOME'], [['Ana']])
    files, summary, resources = process(tmp_path, service, profile, [path])
    assert files[0].status is Status.COMPLETED and resources.repository.count() == 1
    assert summary.results[0].processing_result.composite_tokens_generated == 0
    assert service.repository.path.read_bytes() == before


def test_composite_failure_rolls_back_file_and_continues(tmp_path, monkeypatch):
    service, profile = setup_profile(tmp_path, configs=[ColumnConfig('NOME', True, 'NAME'), ColumnConfig('CPF'), ColumnConfig('IDADE')])
    failed = write_csv(tmp_path / 'failed.csv', ['NOME', 'CPF', 'IDADE'], [['Ana', '99999999999', '24'], ['PRIVATE_VALUE', '99999999999', '24']])
    valid = write_csv(tmp_path / 'valid.csv', ['NOME', 'CPF', 'IDADE'], [['Bia', '88888888888', '25']])
    import data_mask_studio.processing.composite_executor as executor
    actual = executor._normalize_with_fallback

    def fail(value, rule):
        if value == 'PRIVATE_VALUE':
            raise RuntimeError(value)
        return actual(value, rule)

    monkeypatch.setattr(executor, '_normalize_with_fallback', fail)
    files, summary, resources = process(tmp_path, service, profile, [failed, valid])
    assert [item.status for item in files] == [Status.ERROR, Status.COMPLETED]
    assert summary.error_files == summary.completed_files == 1
    assert 'PRIVATE_VALUE' not in files[0].result_message
    assert resources.repository.count() == 1
    assert resources.repository.composite_repository().get_composite_mapping(
        generate_composite_token(KEY, 'CORR', ('ana', '99999999999'))) is None
    assert len(list((tmp_path / 'out').iterdir())) == 1
    assert files[0].processing_result is None


def test_composite_cancellation_rolls_back_and_cleans_output(tmp_path):
    service, profile = setup_profile(tmp_path)
    a = write_csv(tmp_path / 'a.csv', ['NOME', 'CPF', 'IDADE'], [['Ana', '99999999999', '24']])
    b = write_csv(tmp_path / 'b.csv', ['NOME', 'CPF', 'IDADE'], [['Bia', '88888888888', '25']])
    cancellation = CancellationRequest()
    files, _, resources = process(tmp_path, service, profile, [a, b], cancellation=cancellation,
                                   progress_callback=lambda _: cancellation.request())
    assert all(item.status is Status.CANCELLED for item in files)
    assert not list((tmp_path / 'out').iterdir())
    assert resources.repository.composite_repository().get_composite_mapping(
        generate_composite_token(KEY, 'CORR', ('ana', '99999999999'))) is None


def test_composite_fallback_statistics_survive_batch_summary(tmp_path):
    service, profile = setup_profile(tmp_path)
    path = write_csv(tmp_path / 'fallback.csv', ['NOME', 'CPF', 'IDADE'], [['\u0301', '99999999999', '24']])
    files, summary, _ = process(tmp_path, service, profile, [path])
    result = summary.results[0].processing_result
    assert result is files[0].processing_result
    assert [(f.rule, f.count) for f in result.composite_normalization_fallbacks] == [(Rule.PERSON_NAME, 1)]
    assert result.composite_tokens_generated == result.composite_mapping_occurrences == 1
    assert '\u0301' not in files[0].result_message
