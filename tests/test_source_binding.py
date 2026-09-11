from pathlib import Path

import pytest

from data_mask_studio.csv_tools.header_resolver import resolve_empty_headers
from data_mask_studio.csv_tools.models import CSVHeaderReplacement, CSVInspectionResult
from data_mask_studio.csv_tools.source_binding import (
    SourceBindingError,
    SourceColumnRef,
    bind_source,
    source_ref_at,
)


def inspection(*headers: str) -> CSVInspectionResult:
    resolved, replacements = resolve_empty_headers(headers)
    return CSVInspectionResult(Path("unused.csv"), "utf-8", ",", resolved, replacements)


def test_unique_real_header_follows_name_after_reordering():
    original = inspection("NOME", "CPF", "IDADE")
    reference = source_ref_at(original, 0)
    assert reference == SourceColumnRef("NOME", original_index=0)
    assert bind_source(reference, original) == 0
    assert bind_source(reference, inspection("IDADE", "NOME", "CPF")) == 1
    assert bind_source(source_ref_at(original, 2), inspection("IDADE")) == 0


def test_missing_header_fails():
    with pytest.raises(SourceBindingError, match="inexistente"):
        bind_source(SourceColumnRef("AUSENTE"), inspection("NOME", "CPF"))


def test_duplicate_requires_occurrence_even_with_original_position():
    target = inspection("NOME", "NOME", "CPF")
    for reference in (SourceColumnRef("NOME"), SourceColumnRef("NOME", original_index=0)):
        with pytest.raises(SourceBindingError, match="ambígua"):
            bind_source(reference, target)


@pytest.mark.parametrize("index", [0, 1])
def test_explicit_duplicate_occurrence(index):
    original = inspection("NOME", "NOME", "CPF")
    reference = source_ref_at(original, index)
    assert reference.occurrence == index
    assert bind_source(reference, original) == index
    assert bind_source(reference, inspection("CPF", "NOME", "NOME")) == index + 1


@pytest.mark.parametrize("occurrence", [-1, 2, True])
def test_invalid_or_missing_occurrence(occurrence):
    with pytest.raises(SourceBindingError, match="[Oo]corrência"):
        bind_source(SourceColumnRef("NOME", occurrence=occurrence), inspection("NOME", "NOME"))


def test_unique_reference_becomes_ambiguous():
    reference = source_ref_at(inspection("NOME"), 0)
    with pytest.raises(SourceBindingError, match="ambígua"):
        bind_source(reference, inspection("NOME", "NOME"))


def test_synthetic_reference_is_distinct_from_real_name():
    synthetic = inspection("", "CPF")
    real = inspection("column_1", "CPF")
    reference = source_ref_at(synthetic, 0)
    assert reference.header == "column_1"
    assert reference.is_synthetic
    assert reference.original_index == 0
    assert bind_source(reference, synthetic) == 0
    with pytest.raises(SourceBindingError, match="proveniência"):
        bind_source(reference, real)
    with pytest.raises(SourceBindingError, match="Proveniência"):
        bind_source(source_ref_at(real, 0), synthetic)


@pytest.mark.parametrize("headers", [("CPF", ""), ("", "IDADE"), ("", "CPF", "EXTRA"), ("",)])
def test_synthetic_requires_unchanged_structure(headers):
    reference = source_ref_at(inspection("", "CPF"), 0)
    with pytest.raises(SourceBindingError):
        bind_source(reference, inspection(*headers))


def test_synthetic_suffix_does_not_confuse_real_header():
    target = inspection("", "column_1")
    assert target.headers == ["column_1_2", "column_1"]
    assert bind_source(source_ref_at(target, 0), target) == 0
    assert bind_source(SourceColumnRef("column_1"), target) == 1


@pytest.mark.parametrize("index", [-1, 2, True])
def test_factory_rejects_invalid_index(index):
    with pytest.raises(SourceBindingError, match="Índice"):
        source_ref_at(inspection("NOME", "CPF"), index)


@pytest.mark.parametrize("reference", [
    SourceColumnRef("column_1", is_synthetic=True),
    SourceColumnRef("column_1", original_index=5, is_synthetic=True),
    SourceColumnRef("column_1", original_index=0, is_synthetic=True),
    SourceColumnRef("column_1", original_index=0, is_synthetic=True, occurrence=0),
])
def test_incomplete_synthetic_contract_is_rejected(reference):
    with pytest.raises(SourceBindingError):
        bind_source(reference, inspection("", "CPF"))


def test_inconsistent_inspection_provenance_is_rejected():
    target = inspection("NOME", "CPF")
    target.header_replacements = (CSVHeaderReplacement(0, "NOME"),)
    with pytest.raises(SourceBindingError, match="Metadados"):
        source_ref_at(target, 0)


def test_binding_uses_inspection_only_and_never_cell_values(tmp_path):
    from data_mask_studio.csv_tools import inspect_csv

    source = tmp_path / "source.csv"
    sensitive = "SENSITIVE_TEST_VALUE_DO_NOT_REPORT"
    source.write_text(f",CPF\n{sensitive},123\n", encoding="utf-8")
    target = inspect_csv(source)
    reference = source_ref_at(target, 0)
    source.unlink()
    assert bind_source(reference, target) == 0
    assert sensitive not in repr(reference)
    with pytest.raises(SourceBindingError) as error:
        bind_source(reference, inspection("column_1", "CPF"))
    assert sensitive not in str(error.value)
