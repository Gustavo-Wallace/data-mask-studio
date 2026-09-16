import json
from dataclasses import replace

import pytest

from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.source_binding import SourceBindingError, bind_source, source_ref_at
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource, build_processing_plan
from data_mask_studio.profiles import (
    PROFILE_FORMAT_VERSION, PROFILES_SCHEMA_VERSION, ProfileFormatError,
    ProfileRepository, ProfileService, ProfileStorageError, UnknownColumnPolicy,
)


def case(tmp_path, header='NOME,CPF', configs=None):
    source = tmp_path / 'input.csv'
    source.write_text(header + '\n' + ','.join('x' for _ in header.split(',')) + '\n', encoding='utf-8')
    inspection = inspect_csv(source)
    definition = CompositeColumnConfig('PESSOA', 'CORR', (
        CompositeSource(source_ref_at(inspection, 0), Rule.PERSON_NAME),
        CompositeSource(source_ref_at(inspection, 1), Rule.CPF),
    ), action=Action.MASK)
    configs = configs or [ColumnConfig(inspection.headers[-1])]
    repository = ProfileRepository(tmp_path / 'profiles.json')
    service = ProfileService(repository)
    profile = service.create('Perfil composto', configs, composites=[definition])
    return service, repository, profile, inspection


def document(repository):
    return json.loads(repository.path.read_text(encoding='utf-8'))


def test_v2_complete_round_trip_and_explicit_contract(tmp_path):
    configs = [ColumnConfig('NOME', action=Action.EXCLUDE),
               ColumnConfig('CPF', True, 'CPF_ID', Rule.CPF, output_name='DOCUMENTO'),
               ColumnConfig('CIDADE', normalization_rule=Rule.COLLAPSE_WHITESPACE)]
    service, repo, profile, _ = case(tmp_path, 'NOME,CPF,CIDADE', configs)
    assert repo.load() == [profile]
    assert PROFILE_FORMAT_VERSION == PROFILES_SCHEMA_VERSION == 2
    raw = document(repo)
    assert raw['schema_version'] == raw['profiles'][0]['format_version'] == 2
    encoded = raw['profiles'][0]
    assert encoded['unknown_column_policy'] == 'require_explicit'
    assert all('anonymize' not in column for column in encoded['columns'])
    assert [column['action'] for column in encoded['columns']] == ['exclude', 'mask', 'preserve']
    assert encoded['composites'][0]['identifier'] == str(profile.composites[0].identifier)
    application = service.apply(profile, ['NOME', 'CPF', 'CIDADE', 'EXTRA'])
    assert application.composites == profile.composites
    assert application.unknown_column_policy is UnknownColumnPolicy.REQUIRE_EXPLICIT
    assert application.extra_headers == ('EXTRA',) and not application.is_complete
    assert not service.apply(profile, ['NOME', 'CPF', 'CIDADE']).is_complete
    assert 'composites' in application.compatibility_message
    forbidden = {'token', 'canonical_values', 'original_values', 'encrypted_value', 'secret_key', 'input_index'}

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not keys(raw) & forbidden


def test_service_update_rename_preserve_order_and_uuids(tmp_path):
    service, repo, profile, _ = case(tmp_path)
    first = profile.composites[0]
    second = CompositeColumnConfig('OUTRA', 'SECOND', first.components[::-1], action=Action.MASK)
    updated = service.update(profile.identifier, [ColumnConfig('CPF')], composites=[first, second])
    assert repo.load()[0] == updated
    renamed = replace(first, output_name='PESSOA_CORRELACAO')
    updated = service.update(profile.identifier, [ColumnConfig('CPF')], composites=[renamed, second])
    assert updated.composites[0].identifier == first.identifier
    updated = service.update(profile.identifier, [ColumnConfig('CPF', output_name='DOCUMENTO')])
    updated = service.rename(profile.identifier, 'Novo nome')
    loaded = repo.load()[0]
    assert loaded == updated
    assert loaded.composites == (renamed, second)
    assert [s.reference for s in loaded.composites[0].components] == [s.reference for s in loaded.composites[1].components][::-1]
    service.update(profile.identifier, [ColumnConfig('CPF')], composites=[])
    assert repo.load()[0].composites == ()


def test_unique_reference_rebinds_after_reordering(tmp_path):
    _, repo, profile, inspection = case(tmp_path)
    reordered = replace(inspection, headers=['CPF', 'NOME'])
    loaded = repo.load()[0]
    plan = build_processing_plan(reordered, [ColumnConfig('CPF'), ColumnConfig('NOME')], loaded.composites)
    assert [part.input_index for part in plan.outputs[-1].components] == [1, 0]
    assert loaded.composites == profile.composites


def test_duplicate_header_occurrence_round_trip(tmp_path):
    _, repo, profile, inspection = case(tmp_path, 'NOME,NOME,CPF')
    loaded = repo.load()[0]
    sources = loaded.composites[0].components
    assert [s.reference.occurrence for s in sources] == [0, 1]
    assert [bind_source(s.reference, inspection) for s in sources] == [0, 1]
    assert loaded.composites == profile.composites


def test_synthetic_provenance_cannot_become_real(tmp_path):
    _, repo, profile, inspection = case(tmp_path, ',CPF')
    loaded = repo.load()[0]
    reference = loaded.composites[0].components[0].reference
    assert reference.is_synthetic and reference.structure == (('column_1', True), ('CPF', False))
    assert reference == profile.composites[0].components[0].reference
    assert bind_source(reference, inspection) == 0
    real = replace(inspection, header_replacements=())
    with pytest.raises(SourceBindingError):
        bind_source(reference, real)
    with pytest.raises(SourceBindingError):
        bind_source(source_ref_at(real, 0), inspection)


@pytest.mark.parametrize('recent', [False, True])
def test_v1_load_does_not_write_and_explicit_save_upgrades(tmp_path, recent):
    repo = ProfileRepository(tmp_path / 'profiles.json')
    columns = [dict(header='NOME', prefix='NAME', normalization_rule='person_name', anonymize=True),
               dict(header='CPF', prefix='', anonymize=False)]
    if recent:
        columns[0].update(action='preserve', anonymize=False, output_name='PESSOA')
        columns[1].update(action='exclude', normalization_rule='exact', output_name='IGNORADO')
    raw = dict(schema_version=1, profiles=[dict(
        identifier='00000000-0000-0000-0000-000000000001', name='Perfil legado', format_version=1,
        created_at='2026-01-01T00:00:00+00:00', modified_at='2026-01-01T00:00:00+00:00', columns=columns,
    )])
    repo.path.write_text(json.dumps(raw), encoding='utf-8')
    before = repo.path.read_bytes()
    loaded = repo.load()[0]
    assert loaded.format_version == 2 and loaded.composites == ()
    assert loaded.unknown_column_policy is UnknownColumnPolicy.REQUIRE_EXPLICIT
    assert loaded.columns[0].normalization_rule is Rule.PERSON_NAME
    assert loaded.columns[1].action is (Action.EXCLUDE if recent else Action.PRESERVE)
    assert loaded.columns[0].output_name == ('PESSOA' if recent else '')
    assert repo.path.read_bytes() == before
    repo.save([loaded])
    assert document(repo)['schema_version'] == document(repo)['profiles'][0]['format_version'] == 2
    assert repo.load() == [loaded]


@pytest.mark.parametrize('mutation', [
    'future_profile', 'future_container', 'missing_policy', 'unknown_policy', 'extra_profile',
    'missing_action', 'null_action', 'extra_column', 'invalid_uuid', 'nil_uuid', 'duplicate_uuid',
    'one_source', 'bad_normalizer', 'bad_prefix', 'bad_name', 'malformed_source',
    'negative_index', 'bool_index', 'bad_occurrence', 'bad_structure', 'synthetic_without_structure',
    'extra_component', 'extra_reference',
])
def test_invalid_v2_rejected_safely_without_overwrite(tmp_path, mutation):
    _, repo, _, _ = case(tmp_path)
    raw = document(repo)
    profile = raw['profiles'][0]
    composite = profile['composites'][0]
    source = composite['components'][0]
    reference = source['reference']
    if mutation == 'future_profile': profile['format_version'] = 3
    elif mutation == 'future_container': raw['schema_version'] = 3
    elif mutation == 'missing_policy': del profile['unknown_column_policy']
    elif mutation == 'unknown_policy': profile['unknown_column_policy'] = 'preserve_unknown'
    elif mutation == 'extra_profile': profile['required_future_semantics'] = 'PRIVATE_VALUE'
    elif mutation == 'missing_action': del profile['columns'][0]['action']
    elif mutation == 'null_action': profile['columns'][0]['action'] = None
    elif mutation == 'extra_column': profile['columns'][0]['anonymize'] = False
    elif mutation == 'invalid_uuid': composite['identifier'] = 'PRIVATE_VALUE'
    elif mutation == 'nil_uuid': composite['identifier'] = '00000000-0000-0000-0000-000000000000'
    elif mutation == 'duplicate_uuid': profile['composites'].append(composite.copy())
    elif mutation == 'one_source': composite['components'].pop()
    elif mutation == 'bad_normalizer': source['normalization_rule'] = 'PRIVATE_VALUE'
    elif mutation == 'bad_prefix': composite['prefix'] = 'PRIVATE VALUE'
    elif mutation == 'bad_name': composite['output_name'] = '  '
    elif mutation == 'malformed_source': source['reference'] = 'PRIVATE_VALUE'
    elif mutation == 'negative_index': reference['original_index'] = -1
    elif mutation == 'bool_index': reference['original_index'] = True
    elif mutation == 'bad_occurrence': reference['occurrence'] = -1
    elif mutation == 'bad_structure': reference['structure'] = [['NOME', 'true']]
    elif mutation == 'synthetic_without_structure': reference['is_synthetic'] = True
    elif mutation == 'extra_component': source['input_index'] = 4
    elif mutation == 'extra_reference': reference['canonical_values'] = 'PRIVATE_VALUE'
    repo.path.write_text(json.dumps(raw), encoding='utf-8')
    before = repo.path.read_bytes()
    with pytest.raises(ProfileFormatError) as error:
        repo.load()
    assert 'PRIVATE' not in str(error.value)
    assert repo.path.read_bytes() == before


def test_save_rejects_malformed_composites_atomically(tmp_path):
    _, repo, profile, _ = case(tmp_path)
    before = repo.path.read_bytes()
    broken = replace(profile, composites=(replace(profile.composites[0], prefix='invalid-prefix'),))
    with pytest.raises(ProfileStorageError):
        repo.save([broken])
    assert repo.path.read_bytes() == before


def test_composite_only_profile_configuration_is_preserved(tmp_path):
    configs = [ColumnConfig(header, action=Action.EXCLUDE) for header in ('NOME', 'CPF')]
    _, repo, profile, _ = case(tmp_path, configs=configs)
    assert repo.load() == [profile]


def test_scalar_only_v2_round_trip(tmp_path):
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    profile = service.create('Escalar apenas', [ColumnConfig('CPF', True, 'CPF_ID', Rule.CPF)])
    assert service.list_profiles() == [profile]
    raw = document(service.repository)['profiles'][0]
    assert raw['composites'] == [] and raw['unknown_column_policy'] == 'require_explicit'
    assert 'anonymize' not in raw['columns'][0]


def test_batch_support_does_not_release_unadapted_profile_consumers(tmp_path):
    from data_mask_studio.batch.models import BatchFile, BatchFileStatus
    from data_mask_studio.batch.validation import validate_file

    service, _, profile, inspection = case(tmp_path)
    # Batch agora é adaptado; aplicação genérica/GUI continua falhando fechado.
    profile = service.update(profile.identifier, [ColumnConfig('NOME'), ColumnConfig('CPF')])
    item = BatchFile(path=inspection.path)
    validate_file(item, profile, service)
    assert item.status is BatchFileStatus.COMPATIBLE
    application = service.apply(profile, inspection.headers)
    assert not application.is_complete
    assert 'composites' in application.compatibility_message


def test_policy_stays_explicit_after_service_round_trip(tmp_path):
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    created = service.create('Escalar explícito', [ColumnConfig('CPF')])
    updated = service.update(created.identifier, [ColumnConfig('CPF')])
    loaded = service.list_profiles()[0]
    assert loaded == updated
    application = service.apply(loaded, ['CPF', 'NOVO'])
    assert application.unknown_column_policy is UnknownColumnPolicy.REQUIRE_EXPLICIT
    assert not application.is_complete and application.extra_headers == ('NOVO',)


@pytest.mark.parametrize('first_module', ['profiles', 'backup'])
def test_profile_imports_do_not_depend_on_prior_csv_imports(first_module):
    import subprocess
    import sys

    result = subprocess.run([
        sys.executable, '-c', f'import data_mask_studio.{first_module}; import data_mask_studio.profiles',
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_bound_composite_is_not_a_persistable_configuration(tmp_path):
    _, repo, profile, inspection = case(tmp_path)
    plan = build_processing_plan(inspection, [ColumnConfig(h) for h in inspection.headers], profile.composites)
    previous = repo.path.read_bytes()
    with pytest.raises(ProfileStorageError):
        repo.save([replace(profile, composites=(plan.outputs[-1],))])
    assert repo.path.read_bytes() == previous
