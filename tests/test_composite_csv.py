import csv
import sqlite3
from contextlib import contextmanager

import pytest

from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv, CSVAnonymizationError, ProcessingCancelled
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource, build_processing_plan
from data_mask_studio.processing.planner import PlanningError
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.vault import VaultRepository, VaultCipher, VaultError
from data_mask_studio.vault.composite_repository import CompositeVaultRepository

KEY = b'H' * 32


def setup_case(tmp_path, rows, configs=None, *, encoding='utf-8', delimiter=',', second=False):
    source = tmp_path / 'input.csv'
    headers = ['NOME', 'CPF', 'IDADE']
    with source.open('w', encoding=encoding, newline='') as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(rows)
    inspection = inspect_csv(source)
    configs = configs or [ColumnConfig('NOME', action=Action.EXCLUDE),
                          ColumnConfig('CPF', action=Action.EXCLUDE), ColumnConfig('IDADE')]
    definitions = [CompositeColumnConfig('PESSOA', 'CORR', (
        CompositeSource(source_ref_at(inspection, 0), Rule.PERSON_NAME),
        CompositeSource(source_ref_at(inspection, 1), Rule.CPF),
    ))]
    if second:
        definitions.append(CompositeColumnConfig('OUTRA', 'OTHER', (
            CompositeSource(source_ref_at(inspection, 1)),
            CompositeSource(source_ref_at(inspection, 0)),
        )))
    plan = build_processing_plan(inspection, configs, definitions)
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
    destination = tmp_path / 'output.csv'
    kwargs = dict(encoding=inspection.encoding, delimiter=inspection.delimiter,
                  secret_key=KEY, processing_plan=plan, vault_repository=repo)
    return source, destination, repo, kwargs


def output_rows(path, delimiter=','):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.reader(stream, delimiter=delimiter))


def counts(repo):
    with sqlite3.connect(repo.database_path) as connection:
        return tuple(connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                     for table in ('vault_mappings', 'composite_mappings', 'composite_variations'))


@pytest.mark.parametrize('encoding,delimiter', [('utf-8', ','), ('utf-16', ';'), ('utf-32', ';')])
def test_streaming_basic_encoding_and_source_unchanged(tmp_path, encoding, delimiter):
    args = setup_case(tmp_path, [('Gustavo Wallace', '999.999.999-99', '24')],
                      encoding=encoding, delimiter=delimiter)
    source, destination, repo, kwargs = args
    before = source.read_bytes()
    progress = []
    result = anonymize_csv(source, destination, **kwargs, progress_callback=progress.append)
    token = generate_composite_token(KEY, 'CORR', ('gustavo wallace', '99999999999'))
    assert output_rows(destination, delimiter) == [['IDADE', 'PESSOA'], ['24', token]]
    assert destination.read_bytes().startswith(b'\xef\xbb\xbf')
    assert source.read_bytes() == before and progress == [1]
    mapping = repo.composite_repository().get_composite_mapping(token)
    assert mapping.variations[0].original_values == ('Gustavo Wallace', '999.999.999-99')
    assert result.composite_tokens_generated == result.composite_mapping_occurrences == 1


def test_composite_only_and_accounting(tmp_path):
    configs = [ColumnConfig(h, action=Action.EXCLUDE) for h in ('NOME', 'CPF', 'IDADE')]
    source, destination, repo, kwargs = setup_case(tmp_path, [
        ('Gustavo Wallace', '999.999.999-99', '24'),
        ('GUSTAVO WALLACE', '99999999999', '24'),
        ('Gustavo Wallace', '999.999.999-99', '24'),
        (' ', '\t', '24'), ('Gustavo', '', '24'),
    ], configs)
    result = anonymize_csv(source, destination, **kwargs)
    rows = output_rows(destination)
    assert rows[0] == ['PESSOA'] and all(len(row) == 1 for row in rows)
    assert rows[1] == rows[2] == rows[3] and rows[4] == ['']
    mapping = repo.composite_repository().get_composite_mapping(rows[1][0])
    assert mapping.occurrence_count == 3
    assert [v.occurrence_count for v in mapping.variations] == [2, 1]
    assert repo.composite_repository().get_composite_mapping(rows[5][0]).canonical_values == ('gustavo', '')
    assert counts(repo) == (0, 2, 3)
    assert result.composite_mapping_occurrences == 4
    with pytest.raises(PlanningError):
        build_processing_plan(inspect_csv(source), configs)
    kwargs.pop('processing_plan')
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, tmp_path / 'invalid.csv', **kwargs, configurations=configs)


def test_scalar_and_multiple_composites_share_one_transaction(tmp_path, monkeypatch):
    configs = [ColumnConfig('NOME', action=Action.EXCLUDE),
               ColumnConfig('CPF', True, 'CPF_ID'), ColumnConfig('IDADE', output_name='ANOS')]
    source, destination, repo, kwargs = setup_case(tmp_path, [('Ana', '999.999.999-99', '24')], configs, second=True)
    original = repo.transaction
    transactions = []

    @contextmanager
    def observed():
        with original() as transaction:
            statements = []
            transaction._connection.set_trace_callback(statements.append)
            transactions.append(statements)
            yield transaction

    monkeypatch.setattr(repo, 'transaction', observed)

    def forbidden_transaction(*args):
        pytest.fail('O writer composto não deve abrir outra transação')

    monkeypatch.setattr(CompositeVaultRepository, 'transaction', forbidden_transaction)
    result = anonymize_csv(source, destination, **kwargs, mapping_batch_size=1)
    rows = output_rows(destination)
    assert rows[0] == ['CPF', 'ANOS', 'PESSOA', 'OUTRA']
    assert rows[1][1] == '24'
    assert repo.get_decrypted_mapping(rows[1][0]) is not None
    assert counts(repo) == (1, 2, 2)
    assert len(transactions) == 1
    statements = transactions[0]
    assert sum(statement == 'COMMIT' for statement in statements) == 1
    assert any('INSERT INTO vault_mappings' in s for s in statements)
    assert any('INSERT INTO composite_mappings' in s for s in statements)
    assert result.scalar_mapping_occurrences == 1 and result.composite_mapping_occurrences == 2


def test_preserve_normalization_uses_original_for_composite_and_fallbacks(tmp_path):
    configs = [ColumnConfig('NOME', normalization_rule=Rule.PERSON_NAME),
               ColumnConfig('CPF', action=Action.EXCLUDE), ColumnConfig('IDADE')]
    source, destination, repo, kwargs = setup_case(tmp_path, [('\u0301', '99999999999', '24')], configs)
    result = anonymize_csv(source, destination, **kwargs)
    assert result.normalization_fallbacks[0].count == 1
    assert [(f.rule, f.count) for f in result.composite_normalization_fallbacks] == [(Rule.PERSON_NAME, 1)]
    assert '\u0301' not in repr(result)
    mapping = repo.composite_repository().get_composite_mapping(output_rows(destination)[1][-1])
    assert mapping.normalization_rules == (Rule.EXACT, Rule.CPF)


@pytest.mark.parametrize('failure', ['composite', 'callback', 'cancel', 'collision'])
@pytest.mark.parametrize('batch_size', [1, 1000])
def test_failure_rolls_back_both_types_and_cleans_temp(tmp_path, monkeypatch, failure, batch_size):
    configs = [ColumnConfig('NOME', action=Action.EXCLUDE),
               ColumnConfig('CPF', True, 'CPF_ID'), ColumnConfig('IDADE')]
    source, destination, repo, kwargs = setup_case(tmp_path, [
        ('Ana', '99999999999', '24'), ('Bia', '88888888888', '25'),
    ], configs)
    import data_mask_studio.csv_tools.csv_anonymizer as processor
    real_executor = processor.execute_composite_row
    progress = []

    def execute(row, plan, key):
        if progress:
            raise RuntimeError('PRIVATE_VALUE')
        return real_executor(row, plan, key)

    def callback(n):
        progress.append(n)
        if failure == 'callback':
            raise RuntimeError('PRIVATE_VALUE')

    if failure == 'composite':
        monkeypatch.setattr(processor, 'execute_composite_row', execute)
    if failure == 'collision':
        import data_mask_studio.processing.composite_executor as executor
        from data_mask_studio.anonymization import generate_token
        from dataclasses import replace
        # Mesmo prefixo é permitido entre tipos; force uma colisão visual real.
        plan = kwargs['processing_plan']
        kwargs['processing_plan'] = replace(plan, outputs=plan.outputs[:-1] + (
            replace(plan.outputs[-1], prefix='CPF_ID'),))
        monkeypatch.setattr(executor, 'generate_composite_token',
                            lambda *args: generate_token(KEY, 'CPF_ID', '99999999999'))
    with pytest.raises(CSVAnonymizationError) as error:
        anonymize_csv(source, destination, **kwargs, mapping_batch_size=batch_size,
                      progress_callback=callback,
                      should_cancel=lambda: failure == 'cancel' and bool(progress))
    if failure == 'cancel':
        assert isinstance(error.value, ProcessingCancelled)
    assert 'PRIVATE_VALUE' not in str(error.value)
    assert not destination.exists() and not list(tmp_path.glob('.*.tmp'))
    assert counts(repo) == (0, 0, 0)


def test_zero_composites_matches_legacy_bytes(tmp_path):
    source, destination, repo, kwargs = setup_case(tmp_path, [('Ana', '99999999999', '24')])
    configs = [ColumnConfig('NOME', True, 'NAME'), ColumnConfig('CPF'), ColumnConfig('IDADE')]
    kwargs['processing_plan'] = build_processing_plan(inspect_csv(source), configs)
    planned = anonymize_csv(source, destination, **kwargs)
    del kwargs['processing_plan']
    legacy = anonymize_csv(source, tmp_path / 'legacy.csv', **kwargs, configurations=configs)
    assert destination.read_bytes() == legacy.output_path.read_bytes()
    assert planned.normalization_fallbacks == legacy.normalization_fallbacks
    assert planned.composite_tokens_generated == 0


def test_composites_require_vault_before_output(tmp_path):
    source, destination, _, kwargs = setup_case(tmp_path, [('Ana', '', '24')])
    kwargs['vault_repository'] = None
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs)
    assert not destination.exists() and not list(tmp_path.glob('.*.tmp'))


def test_preserve_changes_output_but_not_composite_original(tmp_path):
    from dataclasses import replace

    configs = [ColumnConfig('NOME', normalization_rule=Rule.PERSON_NAME),
               ColumnConfig('CPF', action=Action.EXCLUDE), ColumnConfig('IDADE')]
    source, destination, repo, kwargs = setup_case(tmp_path, [('  ÁNA  ', '99999999999', '24')], configs)
    plan = kwargs['processing_plan']
    composite = plan.outputs[-1]
    composite = replace(composite, components=(
        replace(composite.components[0], normalization_rule=Rule.EXACT), composite.components[1],
    ))
    kwargs['processing_plan'] = replace(plan, outputs=plan.outputs[:-1] + (composite,))
    anonymize_csv(source, destination, **kwargs)
    row = output_rows(destination)[1]
    assert row[0] == 'ana'
    mapping = repo.composite_repository().get_composite_mapping(row[-1])
    assert mapping.canonical_values[0] == mapping.variations[0].original_values[0] == '  ÁNA  '


def test_composite_writer_rejects_transaction_from_another_vault(tmp_path):
    from data_mask_studio.processing.composite_executor import execute_composite_row

    _, _, repo, kwargs = setup_case(tmp_path, [('Ana', '', '24')])
    writer = repo.composite_repository()
    other = VaultRepository(tmp_path / 'other.db', repo._cipher)
    candidate = execute_composite_row(('Ana', '', '24'), kwargs['processing_plan'], KEY).cells[0].candidate
    with other.transaction() as transaction:
        with pytest.raises(VaultError, match='incompatível'):
            writer.upsert_composite_mapping(candidate, transaction=transaction)
    assert counts(repo) == counts(other) == (0, 0, 0)


def test_empty_header_resolver_and_entirely_blank_composite(tmp_path):
    source = tmp_path / 'input.csv'
    source.write_text('NOME,,IDADE\n ,\t,24\n', encoding='utf-8')
    inspection = inspect_csv(source)
    configs = [ColumnConfig(header) for header in inspection.headers]
    definition = CompositeColumnConfig('PESSOA', 'CORR', tuple(
        CompositeSource(source_ref_at(inspection, index)) for index in (0, 1)
    ))
    plan = build_processing_plan(inspection, configs, [definition])
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(b'V' * 32))
    destination = tmp_path / 'output.csv'
    result = anonymize_csv(source, destination, encoding=inspection.encoding,
                           delimiter=inspection.delimiter, processing_plan=plan,
                           secret_key=KEY, vault_repository=repo)
    assert output_rows(destination) == [list(plan.final_headers), [' ', '\t', '24', '']]
    assert counts(repo) == (0, 0, 0)
    assert result.composite_mapping_occurrences == result.composite_tokens_generated == 0
    assert result.composite_normalization_fallbacks == ()
