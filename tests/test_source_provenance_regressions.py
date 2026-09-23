import csv
import json
import sqlite3

import pytest

from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv, CSVAnonymizationError
from data_mask_studio.processing import build_processing_plan, PlanningError
from data_mask_studio.profiles import ProfileService, ProfileRepository
from data_mask_studio.profiles import ProfileFormatError
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.vault import VaultRepository, VaultCipher


def test_h2_synthetic_policy_cannot_bind_literal_header(tmp_path):
    source = tmp_path / 'source.csv'
    source.write_text(',CPF\napproved,123\n', encoding='utf-8')
    inspection = inspect_csv(source)
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    configurations = [ColumnConfig('column_1', action=Action.PRESERVE),
                      ColumnConfig('CPF', action=Action.EXCLUDE)]
    profile = service.create('Synthetic policy', configurations, inspection=inspection)
    source.write_text('CPF,column_1\n123,DIFFERENT_PRIVATE_VALUE\n', encoding='utf-8')
    with pytest.raises(PlanningError):
        service.build_plan(service.list_profiles()[0], inspect_csv(source))
    from data_mask_studio.batch import BatchFile, BatchFileStatus, BatchService
    item = BatchFile(source)
    BatchService(service).validate([item], profile)
    assert item.status is BatchFileStatus.INCOMPATIBLE


def test_m1_opened_source_provenance_must_match_plan(tmp_path):
    source = tmp_path / 'source.csv'
    source.write_text(',CPF\napproved,123\n', encoding='utf-8')
    plan = build_processing_plan(inspect_csv(source), [
        ColumnConfig('column_1', action=Action.PRESERVE), ColumnConfig('CPF', action=Action.EXCLUDE),
    ])
    source.write_text('column_1,CPF\nDIFFERENT_PRIVATE_VALUE,123\n', encoding='utf-8')
    destination = tmp_path / 'output.csv'
    with pytest.raises(CSVAnonymizationError) as error:
        anonymize_csv(source, destination, encoding='utf-8', delimiter=',', processing_plan=plan)
    assert 'DIFFERENT_PRIVATE_VALUE' not in str(error.value)
    assert not destination.exists()


@pytest.mark.parametrize('header', [',CPF', 'column_1,CPF', 'NOME,NOME'])
def test_scalar_reference_round_trip_and_occurrences(tmp_path, header):
    source = tmp_path / 'source.csv'
    source.write_text(header + '\nfirst,second\n', encoding='utf-8')
    inspection = inspect_csv(source)
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    profile = service.create('Identity', [
        ColumnConfig(h, output_name=f'OUT_{i}') for i, h in enumerate(inspection.headers)
    ], inspection=inspection)
    loaded = service.list_profiles()[0]
    assert loaded == profile
    assert tuple(c.reference for c in loaded.columns) == tuple(
        source_ref_at(inspection, i) for i in range(2))
    application = service.apply(loaded, inspection)
    assert application.is_complete
    assert [c.output_name for c in application.configurations] == ['OUT_0', 'OUT_1']
    plan = service.build_plan(loaded, inspection)
    output = tmp_path / 'output.csv'
    anonymize_csv(source, output, encoding='utf-8', delimiter=',', processing_plan=plan)
    assert list(csv.reader(output.read_text(encoding='utf-8-sig').splitlines())) == [
        ['OUT_0', 'OUT_1'], ['first', 'second']]
    if header == 'column_1,CPF':
        source.write_text(',CPF\nfirst,second\n', encoding='utf-8')
        assert not service.apply(loaded, inspect_csv(source)).is_complete


@pytest.mark.parametrize('mutation', ['missing', 'unknown', 'bad_reference'])
def test_v2_scalar_reference_strict_format(tmp_path, mutation):
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    service.create('Identity', [ColumnConfig('NOME')])
    raw = json.loads(service.repository.path.read_text(encoding='utf-8'))
    column = raw['profiles'][0]['columns'][0]
    if mutation == 'missing':
        del column['reference']
    elif mutation == 'unknown':
        column['future_identity'] = {}
    else:
        column['reference']['future_identity'] = True
    service.repository.path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(ProfileFormatError):
        service.list_profiles()


@pytest.mark.parametrize('header,accepted', [('NOME,CPF', True), ('column_1,CPF', False),
                                           (',CPF', False), ('NOME,NOME', False)])
def test_legacy_identity_is_not_fabricated(tmp_path, header, accepted):
    name = 'column_1' if header.startswith((',', 'column_1')) else 'NOME'
    raw = dict(schema_version=1, profiles=[dict(
        identifier='00000000-0000-0000-0000-000000000001', name='Legacy identity', format_version=1,
        created_at='2026-01-01T00:00:00+00:00', modified_at='2026-01-01T00:00:00+00:00',
        columns=[dict(header=name, prefix='NAME', normalization_rule='exact', anonymize=True)])])
    repository = ProfileRepository(tmp_path / 'profiles.json')
    repository.path.write_text(json.dumps(raw), encoding='utf-8')
    service = ProfileService(repository)
    profile = service.list_profiles()[0]
    assert profile.columns[0].reference is None
    source = tmp_path / 'source.csv'
    source.write_text(header + '\na,b\n', encoding='utf-8')
    result = service.apply(profile, inspect_csv(source))
    assert bool(result.matched_headers) is accepted
    if not accepted:
        assert result.missing_headers and not result.is_complete
    repository.save([profile])
    assert service.list_profiles()[0].columns[0].reference is None


def test_execution_reorders_real_sources_without_changing_plan(tmp_path):
    source = tmp_path / 'source.csv'
    source.write_text('NOME,CPF,IDADE\n Ana  Silva ,123,24\n', encoding='utf-8')
    inspection = inspect_csv(source)
    configs = [ColumnConfig('NOME', action=Action.EXCLUDE),
               ColumnConfig('CPF', action=Action.EXCLUDE), ColumnConfig('IDADE')]
    composite = CompositeColumnConfig('PESSOA', '', (
        CompositeSource(source_ref_at(inspection, 0), Rule.PERSON_NAME),
        CompositeSource(source_ref_at(inspection, 1)),
    ), action=Action.PRESERVE)
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    profile = service.create('Reorder', configs, inspection=inspection, composites=[composite])
    plan = service.build_plan(profile, inspection)
    first = tmp_path / 'first.csv'
    anonymize_csv(source, first, encoding='utf-8', delimiter=',', processing_plan=plan)
    source.write_text('IDADE,CPF,NOME\n24,123, Ana  Silva \n', encoding='utf-8')
    application = service.apply(profile, inspect_csv(source))
    assert [c.action for c in application.configurations] == [Action.PRESERVE, Action.EXCLUDE, Action.EXCLUDE]
    second = tmp_path / 'second.csv'
    anonymize_csv(source, second, encoding='utf-8', delimiter=',', processing_plan=plan)
    assert second.read_bytes() == first.read_bytes()
    assert [c.input_index for c in plan.physical_columns] == [0, 1, 2]
    assert [c.reference.header for c in plan.physical_columns] == ['NOME', 'CPF', 'IDADE']


def test_provenance_failure_precedes_vault_transaction_and_output(tmp_path, monkeypatch):
    source = tmp_path / 'source.csv'
    source.write_text(',CPF\nsecret,123\n', encoding='utf-8')
    plan = build_processing_plan(inspect_csv(source), [
        ColumnConfig('column_1', True, 'NAME'), ColumnConfig('CPF', action=Action.EXCLUDE)])
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
    def snapshot():
        with sqlite3.connect(repo.database_path) as db:
            return tuple(db.iterdump())
    before = snapshot()
    def forbidden_transaction():
        pytest.fail('Provenance must be checked before opening a vault transaction')
    monkeypatch.setattr(repo, 'transaction', forbidden_transaction)
    source.write_text('column_1,CPF\nPRIVATE_SENTINEL,123\n', encoding='utf-8')
    destination = tmp_path / 'output.csv'
    files_before = set(tmp_path.iterdir())
    with pytest.raises(CSVAnonymizationError) as error:
        anonymize_csv(source, destination, encoding='utf-8', delimiter=',', processing_plan=plan,
                      secret_key=b'H' * 32, vault_repository=repo)
    assert 'PRIVATE_SENTINEL' not in str(error.value)
    assert snapshot() == before
    assert not destination.exists()
    assert set(tmp_path.iterdir()) == files_before
