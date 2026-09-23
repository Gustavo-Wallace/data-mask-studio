"""Formato lógico de composites: não faz binding nem persiste estado de execução."""
from uuid import UUID

from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.processing.composite_actions import composite_action_error
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource


def require_fields(value: object, fields: set[str]) -> dict:
    # v2 é fechado: extensões semânticas exigem uma nova versão explícita.
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Estrutura do perfil incompatível.")
    return value


def validate_reference(reference: SourceColumnRef) -> None:
    if not isinstance(reference, SourceColumnRef):
        raise ValueError("Referência de origem inválida.")
    if not isinstance(reference.header, str) or not reference.header:
        raise ValueError("Cabeçalho de origem inválido.")
    if type(reference.is_synthetic) is not bool:
        raise ValueError("Proveniência de origem inválida.")
    for number in (reference.original_index, reference.occurrence):
        if number is not None and (type(number) is not int or number < 0):
            raise ValueError("Posição de origem inválida.")
    structure = reference.structure
    if structure is not None and (
        not isinstance(structure, tuple) or not structure
        or any(not isinstance(entry, tuple) or len(entry) != 2
               or not isinstance(entry[0], str) or not entry[0]
               or type(entry[1]) is not bool for entry in structure)
    ):
        raise ValueError("Estrutura de origem inválida.")
    if reference.is_synthetic and (
        reference.original_index is None or structure is None
        or reference.original_index >= len(structure)
        or structure[reference.original_index] != (reference.header, True)
        or reference.occurrence is not None
    ):
        raise ValueError("Proveniência sintética incompleta.")


def validate_composite(composite: CompositeColumnConfig) -> None:
    if not isinstance(composite, CompositeColumnConfig):
        raise ValueError("Configuração composta inválida.")
    if not isinstance(composite.identifier, UUID) or not composite.identifier.int:
        raise ValueError("Identificador composto inválido.")
    if not isinstance(composite.output_name, str) or not composite.output_name.strip():
        raise ValueError("Cabeçalho de saída composto inválido.")
    if composite_action_error(composite.action, composite.prefix):
        raise ValueError("Prefixo composto inválido.")
    if len(composite.components) < 2:
        raise ValueError("Uma composite precisa de ao menos duas fontes.")
    for source in composite.components:
        if not isinstance(source, CompositeSource) or not isinstance(source.normalization_rule, NormalizationRule):
            raise ValueError("Componente composto inválido.")
        validate_reference(source.reference)


def serialize_composite(composite: CompositeColumnConfig) -> dict:
    validate_composite(composite)
    return dict(identifier=str(composite.identifier), action=composite.action.value, output_name=composite.output_name,
                prefix=composite.prefix, components=[
                    dict(reference=serialize_reference(source.reference), normalization_rule=source.normalization_rule.value)
                    for source in composite.components
                ])


def parse_composite(value: object) -> CompositeColumnConfig:
    value = require_fields(value, {"identifier", "action", "output_name", "prefix", "components"})
    if not isinstance(value["identifier"], str) or not isinstance(value["components"], list):
        raise ValueError("Formato composto inválido.")
    sources = []
    for source in value["components"]:
        source = require_fields(source, {"reference", "normalization_rule"})
        sources.append(CompositeSource(parse_reference(source["reference"]), NormalizationRule(source["normalization_rule"])))
    composite = CompositeColumnConfig(value["output_name"], value["prefix"], tuple(sources), UUID(value["identifier"]), ColumnAction(value["action"]))
    validate_composite(composite)
    return composite


def serialize_reference(reference: SourceColumnRef) -> dict:
    validate_reference(reference)
    return dict(header=reference.header, original_index=reference.original_index,
                is_synthetic=reference.is_synthetic, occurrence=reference.occurrence,
                structure=reference.structure)


def parse_reference(value: object) -> SourceColumnRef:
    reference = require_fields(value, {
        "header", "original_index", "is_synthetic", "occurrence", "structure",
    }).copy()
    structure = reference["structure"]
    if structure is not None:
        if not isinstance(structure, list) or not all(isinstance(entry, list) for entry in structure):
            raise ValueError("Estrutura de origem inválida.")
        reference["structure"] = tuple(tuple(entry) for entry in structure)
    result = SourceColumnRef(**reference)
    validate_reference(result)
    return result
