import csv
import json
from dataclasses import replace

import pytest

from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig
from data_mask_studio.batch import BatchFile, BatchFileStatus, BatchService
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError, anonymize_csv
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource, build_processing_plan
from data_mask_studio.processing.composite_executor import CompositeExecutionError, execute_composite_row
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.profiles import ProfileFormatError, ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository

KEY = b'H' * 32


def setup(tmp_path, row=('Gustavo', '123'), rules=(Rule.EXACT, Rule.EXACT)):
    source = tmp_path / 'input.csv'
    with source.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['NOME', 'CPF'])
        writer.writerow(row)
    inspection = inspect_csv(source)
    columns = [ColumnConfig(header, action=Action.EXCLUDE) for header in inspection.headers]
    composite = CompositeColumnConfig('PESSOA', components=tuple(
        CompositeSource(source_ref_at(inspection, i), rule) for i, rule in enumerate(rules)
    ))
    return inspection, columns, composite


@pytest.mark.parametrize('row', [
    ('Gustavo', '123'), ('José 🔒', 'ação'), ('a|b,', '"texto"\\\n'),
    ('Gustavo', ''), ('Gustavo', ' \t'), ('', ''), (' ', '\t'),
])
def test_preserve_defaults_json_and_no_crypto(tmp_path, monkeypatch, row):
    inspection, columns, composite = setup(tmp_path, row)
    assert composite.action is Action.PRESERVE and composite.prefix == ''
    plan = build_processing_plan(inspection, columns, [composite])
    assert not plan.requires_masking

    def forbidden(*args, **kwargs):
        pytest.fail('PRESERVE não deve gerar token nem candidato')

    monkeypatch.setattr('data_mask_studio.processing.composite_executor.generate_composite_token', forbidden)
    monkeypatch.setattr('data_mask_studio.processing.composite_executor.CompositeMappingCandidate', forbidden)
    result = execute_composite_row(row, plan)
    cell = result.cells[0]
    assert cell.action is Action.PRESERVE and cell.candidate is None
    assert result == execute_composite_row(row, plan)
    if all(not value.strip() for value in row):
        assert cell.value == ''
    else:
        values = [value if value.strip() else '' for value in row]
        assert cell.value == json.dumps(values, ensure_ascii=False, separators=(',', ':'))
        assert json.loads(cell.value) == values


@pytest.mark.parametrize('action,prefix', [(Action.PRESERVE, 'CORR'), (Action.MASK, ''),
                                         (Action.MASK, 'invalid'), (Action.EXCLUDE, ''), ('preserve', '')])
def test_action_prefix_contract_rejected(tmp_path, action, prefix):
    from data_mask_studio.processing.planner import PlanningError
    inspection, columns, composite = setup(tmp_path)
    with pytest.raises(PlanningError):
        build_processing_plan(inspection, columns, [replace(composite, action=action, prefix=prefix)])


@pytest.mark.parametrize('row,expected,fallbacks', [
    (('GUSTAVO WALLACE', '999.999.999-99'), ['gustavo wallace', '99999999999'], 0),
    (('\u0301', '99999999999'), ['\u0301', '99999999999'], 1),
])
def test_preserve_normalization_fallback_and_csv_without_vault(tmp_path, monkeypatch, row, expected, fallbacks):
    inspection, columns, composite = setup(tmp_path, row, (Rule.PERSON_NAME, Rule.CPF))
    plan = build_processing_plan(inspection, columns, [composite])
    class ForbiddenVault:
        def transaction(self): pytest.fail('Sem acesso ao cofre')
        def composite_repository(self): pytest.fail('Sem acesso composto')
    before = inspection.path.read_bytes()
    destination = tmp_path / 'output.csv'
    result = anonymize_csv(inspection.path, destination, encoding=inspection.encoding, delimiter=',',
                          processing_plan=plan, vault_repository=ForbiddenVault())
    with destination.open(encoding='utf-8-sig', newline='') as stream:
        headers, cells = list(csv.reader(stream))
    assert headers == ['PESSOA'] and json.loads(cells[0]) == expected
    assert inspection.path.read_bytes() == before
    assert result.composite_mapping_occurrences == result.composite_tokens_generated == 0
    assert result.new_mappings == result.updated_mappings == 0
    assert sum(f.count for f in result.composite_normalization_fallbacks) == fallbacks


@pytest.mark.parametrize('mode', ['preserve', 'mask', 'mixed'])
def test_profile_and_batch_round_trip_actions(tmp_path, mode):
    inspection, columns, preserve = setup(tmp_path)
    mask = CompositeColumnConfig('TOKEN', 'CORR', preserve.components, action=Action.MASK)
    composites = [preserve] if mode == 'preserve' else [mask] if mode == 'mask' else [preserve, mask]
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    profile = service.create('Ações compostas', columns, composites=composites)
    assert service.list_profiles() == [profile]
    document = json.loads(service.repository.path.read_text(encoding='utf-8'))
    assert [c['action'] for c in document['profiles'][0]['composites']] == [c.action.value for c in composites]
    assert profile.format_version == document['schema_version'] == 2
    files = [BatchFile(inspection.path)]
    batch = BatchService(service)
    batch.validate(files, profile)
    output = tmp_path / 'out'
    output.mkdir()
    calls = []
    class Key:
        def get_key(self):
            calls.append('key')
            return KEY
    def vault():
        calls.append('vault')
        return VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
    kwargs = {} if mode == 'preserve' else dict(key_provider=Key(), vault_repository_factory=vault)
    summary = batch.process(files, profile, output, **kwargs)
    assert files[0].status is BatchFileStatus.COMPLETED
    result = summary.results[0].processing_result
    assert result.composite_tokens_generated == result.composite_mapping_occurrences == (0 if mode == 'preserve' else 1)
    with files[0].output_path.open(encoding='utf-8-sig', newline='') as stream:
        header, values = list(csv.reader(stream))
    assert header == [c.output_name for c in composites]
    if mode != 'mask': assert values[0] == '["Gustavo","123"]'
    if mode != 'preserve':
        assert calls == ['key', 'vault']
        token = generate_composite_token(KEY, 'CORR', ('Gustavo', '123'))
        assert values[-1] == token
        repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32)).composite_repository()
        assert repo.get_composite_mapping(token).occurrence_count == 1
    else:
        assert not calls and not (tmp_path / 'vault.db').exists()


@pytest.mark.parametrize('action', [None, 'exclude', 'invalid'])
def test_profile_v2_requires_supported_explicit_action(tmp_path, action):
    _, columns, composite = setup(tmp_path)
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    service.create('Perfil explícito', columns, composites=[composite])
    document = json.loads(service.repository.path.read_text(encoding='utf-8'))
    raw = document['profiles'][0]['composites'][0]
    if action is None: del raw['action']
    else: raw['action'] = action
    with pytest.raises(ProfileFormatError):
        ProfileRepository.parse_bytes(json.dumps(document).encode('utf-8'))


def test_mask_requires_key_and_preserve_mask_transitions(tmp_path):
    inspection, columns, preserve = setup(tmp_path)
    mask = replace(preserve, action=Action.MASK, prefix='CORR')
    plan = build_processing_plan(inspection, columns, [mask])
    assert plan.requires_masking
    with pytest.raises(CompositeExecutionError):
        execute_composite_row(('Gustavo', '123'), plan)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(inspection.path, tmp_path / 'invalid.csv', encoding='utf-8', delimiter=',', processing_plan=plan)
    reset = replace(mask, action=Action.PRESERVE, prefix='')
    assert reset.identifier == preserve.identifier
    assert not build_processing_plan(inspection, columns, [reset]).requires_masking


def test_unexpected_preserve_error_is_fatal_without_publication(tmp_path, monkeypatch):
    inspection, columns, preserve = setup(tmp_path)
    plan = build_processing_plan(inspection, columns, [preserve])
    def fail(*args): raise RuntimeError('PRIVATE_VALUE')
    monkeypatch.setattr('data_mask_studio.processing.composite_executor._normalize_with_fallback', fail)
    output = tmp_path / 'output.csv'
    with pytest.raises(CSVAnonymizationError) as error:
        anonymize_csv(inspection.path, output, encoding='utf-8', delimiter=',', processing_plan=plan)
    assert 'PRIVATE_VALUE' not in str(error.value)
    assert not output.exists() and not list(tmp_path.glob('.*.tmp'))
