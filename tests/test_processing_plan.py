from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from uuid import UUID

import pytest

from data_mask_studio.anonymization.column_config import validate_configuration
from data_mask_studio.anonymization.models import ColumnAction, ColumnConfig
from data_mask_studio.csv_tools.header_resolver import resolve_empty_headers
from data_mask_studio.csv_tools.models import CSVInspectionResult
from data_mask_studio.csv_tools.source_binding import SourceBindingError, SourceColumnRef, source_ref_at
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing import (
    CompositeColumnConfig, CompositeSource, PlanningError, build_processing_plan,
)
from data_mask_studio.processing.models import BoundCompositeColumn


def inspection(*headers):
    resolved, replacements = resolve_empty_headers(headers)
    return CSVInspectionResult(Path("not-opened.csv"), "utf-8", ",", resolved, replacements)


def scalars(target, action=ColumnAction.PRESERVE):
    return [ColumnConfig(header, action=action) for header in target.headers]


def composite(target, name="PESSOA"):
    return CompositeColumnConfig(name, "CORR", (
        CompositeSource(source_ref_at(target, 0), NormalizationRule.PERSON_NAME),
        CompositeSource(source_ref_at(target, 1), NormalizationRule.CPF),
    ), action=ColumnAction.MASK)


def test_basic_plan_and_component_order():
    target = inspection("NOME", "CPF")
    definition = composite(target)
    plan = build_processing_plan(target, scalars(target), [definition])
    assert plan.final_headers == ("NOME", "CPF", "PESSOA")
    bound = plan.outputs[-1]
    assert isinstance(bound, BoundCompositeColumn)
    assert bound.identifier == definition.identifier
    assert bound.prefix == "CORR"
    assert [source.input_index for source in bound.components] == [0, 1]
    assert [source.normalization_rule for source in bound.components] == [
        NormalizationRule.PERSON_NAME, NormalizationRule.CPF,
    ]
    reverse = replace(definition, components=definition.components[::-1])
    reversed_plan = build_processing_plan(target, scalars(target), [reverse])
    assert [s.input_index for s in reversed_plan.outputs[-1].components] == [1, 0]


def test_reorder_binds_to_original_physical_row_not_output():
    definition = composite(inspection("NOME", "CPF"))
    target = inspection("CPF", "NOME")
    plan = build_processing_plan(target, scalars(target), [definition])
    assert [s.input_index for s in plan.outputs[-1].components] == [1, 0]
    assert definition.components[0].reference.original_index == 0


def test_excluded_sources_remain_available_with_independent_normalizers():
    target = inspection("NOME", "CPF", "IDADE")
    configs = scalars(target, ColumnAction.EXCLUDE)
    configs[2].action = ColumnAction.PRESERVE
    definition = composite(target)
    plan = build_processing_plan(target, configs, [definition])
    assert plan.final_headers == ("IDADE", "PESSOA")
    assert len(plan.physical_columns) == 3
    assert plan.outputs[0].input_index == 2
    assert [s.input_index for s in plan.outputs[1].components] == [0, 1]
    assert plan.outputs[1].components[0].normalization_rule is NormalizationRule.PERSON_NAME
    assert configs[0].normalization_rule is NormalizationRule.EXACT


def test_all_exclude_only_allowed_with_composite_in_new_planner():
    target = inspection("NOME", "CPF")
    configs = scalars(target, ColumnAction.EXCLUDE)
    assert not validate_configuration(configs).is_valid
    assert build_processing_plan(target, configs, [composite(target)]).final_headers == ("PESSOA",)
    with pytest.raises(PlanningError, match="Ao menos"):
        build_processing_plan(target, configs)


@pytest.mark.parametrize("count", [0, 1])
def test_at_least_two_sources(count):
    target = inspection("NOME", "CPF")
    definition = composite(target)
    with pytest.raises(PlanningError, match="duas fontes"):
        build_processing_plan(target, scalars(target), [replace(definition, components=definition.components[:count])])


@pytest.mark.parametrize("alternate", [False, True])
def test_repeated_physical_source_rejected_even_with_different_refs(alternate):
    target = inspection("NOME", "CPF")
    first = CompositeSource(source_ref_at(target, 0))
    second = CompositeSource(SourceColumnRef("NOME")) if alternate else first
    definition = CompositeColumnConfig("PESSOA", "CORR", (first, second), action=ColumnAction.MASK)
    with pytest.raises(PlanningError, match="Fonte física repetida"):
        build_processing_plan(target, scalars(target), [definition])


@pytest.mark.parametrize("headers, message", [(("CPF", "IDADE"), "inexistente"), (("NOME", "NOME", "CPF"), "ambígua")])
def test_binding_errors_preserve_controlled_cause(headers, message):
    definition = composite(inspection("NOME", "CPF"))
    target = inspection(*headers)
    # Exclude avoids a separate scalar output-name collision for duplicate headers.
    with pytest.raises(PlanningError, match=message) as error:
        build_processing_plan(target, scalars(target, ColumnAction.EXCLUDE), [definition])
    assert isinstance(error.value.__cause__, SourceBindingError)


def test_explicit_duplicates_and_synthetic_binding():
    for target in (inspection("NOME", "NOME"), inspection("", "CPF")):
        plan = build_processing_plan(target, scalars(target, ColumnAction.EXCLUDE), [composite(target)])
        assert [s.input_index for s in plan.outputs[0].components] == [0, 1]
    synthetic = inspection("", "CPF")
    incompatible = inspection("column_1", "CPF")
    with pytest.raises(PlanningError, match="proveniência"):
        build_processing_plan(incompatible, scalars(incompatible), [composite(synthetic)])


def test_scalar_composite_collision_after_trim():
    target = inspection("NOME", "CPF")
    configs = scalars(target)
    configs[0].output_name = " PESSOA "
    with pytest.raises(PlanningError, match="Cabeçalho de saída repetido"):
        build_processing_plan(target, configs, [composite(target, "PESSOA ")])


def test_composite_collision_order_and_literal_names():
    target = inspection("NOME", "CPF")
    first, second = composite(target), composite(target, " PESSOA ")
    with pytest.raises(PlanningError, match="Cabeçalho de saída repetido"):
        build_processing_plan(target, scalars(target), [first, second])
    second = replace(second, output_name=" pessoa  completa ")
    plan = build_processing_plan(target, scalars(target), [second, first])
    assert plan.final_headers == ("NOME", "CPF", "pessoa  completa", "PESSOA")
    assert [c.identifier for c in plan.outputs[2:]] == [second.identifier, first.identifier]
    assert build_processing_plan(target, scalars(target), [first, replace(second, output_name="pessoa")])


@pytest.mark.parametrize("name", ["", " \t ", None])
def test_invalid_output_name(name):
    target = inspection("NOME", "CPF")
    with pytest.raises(PlanningError, match="cabeçalho de saída"):
        build_processing_plan(target, scalars(target), [composite(target, name)])


@pytest.mark.parametrize("prefix", ["", "A", "1CORR", "corr", "CO-RR", "A" * 25, None])
def test_invalid_prefix(prefix):
    target = inspection("NOME", "CPF")
    with pytest.raises(PlanningError):
        build_processing_plan(target, scalars(target), [replace(composite(target), prefix=prefix)])


def test_invalid_normalizer():
    target = inspection("NOME", "CPF")
    definition = composite(target)
    invalid = replace(definition.components[0], normalization_rule="unsupported")
    with pytest.raises(PlanningError, match="Normalizador"):
        build_processing_plan(target, scalars(target), [replace(definition, components=(invalid, definition.components[1]))])


def test_identifiers_are_stable_independent_and_unique_in_plan():
    target = inspection("NOME", "CPF")
    definition = composite(target)
    renamed = replace(definition, output_name="OUTRO")
    assert definition.identifier == renamed.identifier
    assert definition.identifier != composite(target).identifier
    with pytest.raises(PlanningError, match="Identificador.*repetido"):
        build_processing_plan(target, scalars(target), [definition, renamed])
    with pytest.raises(PlanningError, match="Identificador.*inválido"):
        build_processing_plan(target, scalars(target), [replace(definition, identifier=UUID(int=0))])


def test_plan_is_immutable_snapshot():
    target = inspection("NOME", "CPF")
    configs = scalars(target)
    original = composite(target)
    components = list(original.components)
    definition = replace(original, components=components)
    plan = build_processing_plan(target, configs, [definition])
    components.clear()
    configs[0].output_name = "ALTERADO"
    configs[0].action = ColumnAction.EXCLUDE
    target.headers.clear()
    assert plan.final_headers == ("NOME", "CPF", "PESSOA")
    assert len(definition.components) == 2
    for obj, attribute, value in (
        (plan, "outputs", ()), (definition, "output_name", "OUTRO"),
        (plan.physical_columns[0], "action", ColumnAction.EXCLUDE),
        (plan.outputs[-1].components[0], "input_index", 3),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, attribute, value)


def test_scalar_policy_alignment_and_existing_validation():
    target = inspection("NOME", "CPF")
    with pytest.raises(PlanningError, match="ordem física"):
        build_processing_plan(target, scalars(target)[::-1])
    configs = scalars(target)
    configs[0].action = ColumnAction.MASK
    with pytest.raises(PlanningError):
        build_processing_plan(target, configs)
    configs[0].prefix = "NM"
    configs[0].output_name = " Nome final "
    plan = build_processing_plan(target, configs)
    assert plan.final_headers == ("Nome final", "CPF")
    assert plan.physical_columns[0].prefix == "NM"
