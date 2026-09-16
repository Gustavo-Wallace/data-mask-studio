from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from uuid import uuid4

import pytest

from data_mask_studio.anonymization import ColumnAction, ColumnConfig
from data_mask_studio.csv_tools.models import CSVInspectionResult
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource, build_processing_plan
from data_mask_studio.processing.composite_executor import CompositeExecutionError, execute_composite_row
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.vault import VaultCipher
from data_mask_studio.vault.composite_repository import CompositeVaultRepository

KEY = b"H" * 32


def plan_for(rules=(Rule.PERSON_NAME, Rule.CPF), headers=("NOME", "CPF")):
    inspection = CSVInspectionResult(Path("not-read.csv"), "utf-8", ",", list(headers))
    definition = CompositeColumnConfig("PESSOA", "CORR", tuple(
        CompositeSource(source_ref_at(inspection, index), rule) for index, rule in enumerate(rules)
    ), action=ColumnAction.MASK)
    return build_processing_plan(inspection, [ColumnConfig(h, action=ColumnAction.EXCLUDE) for h in headers], [definition])


def repository(tmp_path):
    return CompositeVaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))


def test_basic_execution_reads_excluded_original_sources_without_binding(monkeypatch, tmp_path):
    plan = plan_for()
    import data_mask_studio.csv_tools.source_binding as binding

    def forbidden(*args):
        pytest.fail("Binding não pertence à execução")

    monkeypatch.setattr(binding, "bind_source", forbidden)
    row = ("Gustavo Wallace", "999.999.999-99")
    result = execute_composite_row(row, plan, KEY)
    cell = result.cells[0]
    assert cell.composite_id == plan.outputs[0].identifier
    assert cell.output_index == 0 and cell.output_name == "PESSOA"
    assert cell.candidate.canonical_values == ("gustavo wallace", "99999999999")
    assert cell.candidate.original_values == row
    assert cell.candidate.normalization_rules == (Rule.PERSON_NAME, Rule.CPF)
    assert cell.candidate.identity_version == cell.candidate.payload_version == 1
    assert cell.value == cell.candidate.code == generate_composite_token(KEY, "CORR", cell.candidate.canonical_values)
    assert result.fallbacks == ()
    assert "Gustavo" not in repr(result)
    assert all(c.normalization_rule is Rule.EXACT for c in plan.physical_columns)
    with pytest.raises(FrozenInstanceError):
        cell.value = "changed"
    repo = repository(tmp_path)
    repo.upsert_composite_mapping(cell.candidate)
    assert repo.get_composite_mapping(cell.value).canonical_values == cell.candidate.canonical_values


@pytest.mark.parametrize("second", [("GUSTAVO WALLACE", "99999999999"), ("Gustavo Wallace", "999.999.999-99")])
def test_variations_and_accounting(tmp_path, second):
    repo = repository(tmp_path)
    first = ("Gustavo Wallace", "999.999.999-99")
    cells = [execute_composite_row(row, plan_for(), KEY).cells[0] for row in (first, second)]
    assert cells[0].value == cells[1].value
    for cell in cells:
        repo.upsert_composite_mapping(cell.candidate)
    mapping = repo.get_composite_mapping(cells[0].value)
    assert mapping.occurrence_count == 2
    assert mapping.normalization_rules == (Rule.PERSON_NAME, Rule.CPF)
    if second == first:
        assert len(mapping.variations) == 1 and mapping.variations[0].occurrence_count == 2
    else:
        assert [v.original_values for v in mapping.variations] == [first, second]
        assert [v.occurrence_count for v in mapping.variations] == [1, 1]


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\u00a0"])
def test_partial_blank_round_trip_preserves_original_whitespace(tmp_path, blank):
    cell = execute_composite_row(("Gustavo", blank), plan_for(), KEY).cells[0]
    assert cell.candidate.canonical_values == ("gustavo", "")
    assert cell.candidate.original_values == ("Gustavo", blank)
    repo = repository(tmp_path)
    repo.upsert_composite_mapping(cell.candidate)
    assert repo.get_composite_mapping(cell.value).variations[0].original_values == ("Gustavo", blank)


@pytest.mark.parametrize("row", [("", ""), (" ", "\t"), ("\u00a0", "")])
def test_all_blank_has_no_token_candidate_or_fallback(monkeypatch, row):
    import data_mask_studio.processing.composite_executor as executor

    def forbidden(*args):
        pytest.fail("Não deve normalizar nem gerar token")

    monkeypatch.setattr(executor, "generate_composite_token", forbidden)
    monkeypatch.setattr(executor, "_normalize_with_fallback", forbidden)
    result = execute_composite_row(row, plan_for(), KEY)
    assert result.cells[0].value == "" and result.cells[0].candidate is None
    assert result.fallbacks == ()


def test_empty_canonical_fallback_exact_and_persistence(tmp_path):
    result = execute_composite_row(("\u0301", "99999999999"), plan_for(), KEY)
    candidate = result.cells[0].candidate
    assert candidate.canonical_values == ("\u0301", "99999999999")
    assert candidate.normalization_rules == (Rule.EXACT, Rule.CPF)
    assert [(f.rule, f.count) for f in result.fallbacks] == [(Rule.PERSON_NAME, 1)]
    assert "\u0301" not in repr(result.fallbacks)
    repo = repository(tmp_path)
    repo.upsert_composite_mapping(candidate)
    assert repo.get_composite_mapping(candidate.code).canonical_values == candidate.canonical_values


def test_multiple_composites_order_and_fatal_error_returns_no_partial_result(monkeypatch):
    plan = plan_for((Rule.EXACT, Rule.EXACT))
    first = plan.outputs[0]
    second = replace(first, identifier=uuid4(), output_name="SECOND", components=first.components[::-1])
    plan = replace(plan, outputs=(first, second))
    result = execute_composite_row(("A", "B"), plan, KEY)
    assert [c.output_name for c in result.cells] == ["PESSOA", "SECOND"]
    assert [c.output_index for c in result.cells] == [0, 1]
    assert result.cells[0].value != result.cells[1].value
    import data_mask_studio.anonymization.anonymizer as scalar
    original = scalar.normalize_value
    calls = 0

    def fail_later(value, rule):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("PRIVATE_ROW_VALUE")
        return original(value, rule)

    monkeypatch.setattr(scalar, "normalize_value", fail_later)
    with pytest.raises(CompositeExecutionError) as error:
        execute_composite_row(("A", "B"), plan, KEY)
    assert calls == 3  # Nenhuma tentativa de fallback para RuntimeError.
    assert "PRIVATE_ROW_VALUE" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("row", [("private",), ("private", "x", "y"), (None, "x"), "private"])
def test_invalid_row_is_controlled(row):
    with pytest.raises(CompositeExecutionError) as error:
        execute_composite_row(row, plan_for(), KEY)
    assert "private" not in str(error.value)


def test_zero_composites_and_unicode_metadata_independence(tmp_path):
    inspection = CSVInspectionResult(Path("none"), "utf-8", ",", ["A", "B"])
    scalar = build_processing_plan(inspection, [ColumnConfig("A"), ColumnConfig("B")])
    result = execute_composite_row(("José", "🔒"), scalar, KEY)
    assert result.cells == result.fallbacks == ()
    first = execute_composite_row(("José", "🔒"), plan_for((Rule.EXACT, Rule.EXACT)), KEY).cells[0]
    other_plan = plan_for((Rule.EXACT, Rule.EXACT), ("OUTRO", "HEADER"))
    other_plan = replace(other_plan, outputs=(replace(other_plan.outputs[0], output_name="NEW"),))
    second = execute_composite_row(("José", "🔒"), other_plan, KEY).cells[0]
    assert first.composite_id != second.composite_id
    assert first.value == second.value
    repo = repository(tmp_path)
    repo.upsert_composite_mapping(first.candidate)
    assert repo.get_composite_mapping(first.value).variations[0].original_values == ("José", "🔒")


def test_aggregate_fallbacks_and_legacy_whitespace_remain_readable(tmp_path):
    result = execute_composite_row(("\u0301", "\u0300"), plan_for((Rule.PERSON_NAME, Rule.PERSON_NAME)), KEY)
    assert [(f.rule, f.count) for f in result.fallbacks] == [(Rule.PERSON_NAME, 2)]
    repo = repository(tmp_path)
    repo.upsert_composite_mapping(result.cells[0].candidate)
    assert repo.get_composite_mapping(result.cells[0].value).normalization_rules == (Rule.EXACT, Rule.EXACT)
    from data_mask_studio.vault.composite_models import CompositeMappingCandidate
    values = ("A", "   ")
    legacy = CompositeMappingCandidate(generate_composite_token(KEY, "CORR", values), "CORR",
                                       values, values, (Rule.EXACT, Rule.EXACT))
    repo.upsert_composite_mapping(legacy)
    assert repo.get_composite_mapping(legacy.code).canonical_values == values


def test_physical_indexes_are_not_scalar_projection_indexes():
    inspection = CSVInspectionResult(Path("none"), "utf-8", ",", ["NOME", "CPF", "IDADE"])
    definition = CompositeColumnConfig("PESSOA", "CORR", (
        CompositeSource(source_ref_at(inspection, 0), Rule.PERSON_NAME),
        CompositeSource(source_ref_at(inspection, 1), Rule.CPF),
    ), action=ColumnAction.MASK)
    plan = build_processing_plan(inspection, [
        ColumnConfig("NOME", action=ColumnAction.EXCLUDE),
        ColumnConfig("CPF", action=ColumnAction.EXCLUDE), ColumnConfig("IDADE"),
    ], [definition])
    result = execute_composite_row(("Gustavo", "99999999999", "42"), plan, KEY)
    assert result.cells[0].output_index == 1
    assert result.cells[0].candidate.original_values == ("Gustavo", "99999999999")
