from collections.abc import Sequence
from uuid import UUID

from data_mask_studio.anonymization.column_config import validate_configuration
from data_mask_studio.anonymization.models import ColumnAction, ColumnConfig
from data_mask_studio.processing.composite_actions import composite_action_error
from data_mask_studio.csv_tools.models import CSVInspectionResult
from data_mask_studio.csv_tools.source_binding import (
    SourceBindingError, SourceColumnRef, bind_source, source_ref_at,
)
from data_mask_studio.normalization.models import NormalizationRule
from data_mask_studio.processing.models import (
    BoundCompositeColumn, BoundCompositeSource, BoundScalarColumn,
    CompositeColumnConfig, CompositeSource, ProcessingPlan,
)


class PlanningError(ValueError):
    """Configuração ou binding inválido; nenhuma linha foi processada."""


def bind_execution_sources(plan: ProcessingPlan, inspection: CSVInspectionResult) -> tuple[int, ...]:
    """Map opened physical positions into the immutable plan's logical row view.

    Both scalar and composite executors then consume this validated projection,
    not stale physical positions. Output ordering remains the approved plan's.
    """
    try:
        if tuple(c.input_index for c in plan.physical_columns) != tuple(range(len(inspection.headers))):
            raise SourceBindingError("Estrutura física incompatível com o plano.")
        indices = tuple(bind_source(c.reference, inspection) for c in plan.physical_columns)
        if set(indices) != set(range(len(inspection.headers))):
            raise SourceBindingError("Colunas físicas desconhecidas ou ambíguas.")
        for output in plan.outputs:
            if isinstance(output, BoundCompositeColumn):
                for component in output.components:
                    if bind_source(component.reference, inspection) != indices[component.input_index]:
                        raise SourceBindingError("Identidade de componente incompatível.")
        return indices
    except (SourceBindingError, IndexError):
        raise PlanningError("A estrutura ou proveniência da origem mudou. Inspecione o CSV novamente.") from None


def build_processing_plan(
    inspection: CSVInspectionResult,
    scalar_configs: Sequence[ColumnConfig],
    composites: Sequence[CompositeColumnConfig] = (),
) -> ProcessingPlan:
    """Políticas escalares devem corresponder à ordem física da inspeção.

    Composites são vinculadas à entrada integral antes de qualquer projeção.
    Este contrato não executa normalizadores, HMAC, escrita ou acesso ao cofre.
    """
    if [config.header for config in scalar_configs] != inspection.headers:
        raise PlanningError("Configurações escalares incompatíveis com a ordem física da entrada.")
    for config in scalar_configs:
        if not isinstance(config.action, ColumnAction):
            raise PlanningError("Ação escalar inválida.")
        if not isinstance(config.normalization_rule, NormalizationRule):
            raise PlanningError("Normalizador escalar inválido.")
        if not isinstance(config.output_name, str) or not isinstance(config.prefix, str):
            raise PlanningError("Nome ou prefixo escalar inválido.")
    # Reutiliza as validações atuais sem mudar a proibição pública de all EXCLUDE.
    if any(config.action is not ColumnAction.EXCLUDE for config in scalar_configs):
        validation = validate_configuration(scalar_configs)
        if not validation.is_valid:
            raise PlanningError(validation.error_message)

    physical: list[BoundScalarColumn] = []
    try:
        for index, config in enumerate(scalar_configs):
            reference = source_ref_at(inspection, index)
            physical.append(BoundScalarColumn(
                index, reference, config.action, config.normalization_rule,
                config.prefix, config.effective_output_header,
            ))
    except SourceBindingError as error:
        raise PlanningError(str(error)) from error

    outputs: list[BoundScalarColumn | BoundCompositeColumn] = [
        column for column in physical if column.action is not ColumnAction.EXCLUDE
    ]
    identifiers: set[UUID] = set()
    for composite in composites:
        if not isinstance(composite.identifier, UUID) or composite.identifier.int == 0:
            raise PlanningError("Identificador de composite inválido.")
        if composite.identifier in identifiers:
            raise PlanningError("Identificador de composite repetido.")
        identifiers.add(composite.identifier)
        if not isinstance(composite.output_name, str) or not composite.output_name.strip():
            raise PlanningError("Informe um cabeçalho de saída para a composite.")
        if not isinstance(composite.prefix, str):
            raise PlanningError("Prefixo de composite inválido.")
        prefix_error = composite_action_error(composite.action, composite.prefix)
        if prefix_error:
            raise PlanningError(prefix_error)
        if len(composite.components) < 2:
            raise PlanningError("Uma composite precisa de ao menos duas fontes.")
        sources: list[BoundCompositeSource] = []
        seen_indexes: set[int] = set()
        for component in composite.components:
            if not isinstance(component, CompositeSource) or not isinstance(component.reference, SourceColumnRef):
                raise PlanningError("Componente de origem inválido.")
            if not isinstance(component.normalization_rule, NormalizationRule):
                raise PlanningError("Normalizador de componente inválido.")
            try:
                index = bind_source(component.reference, inspection)
            except SourceBindingError as error:
                raise PlanningError(str(error)) from error
            # Referências diferentes também não podem apontar à mesma coluna física.
            if index in seen_indexes:
                raise PlanningError("Fonte física repetida na mesma composite.")
            seen_indexes.add(index)
            sources.append(BoundCompositeSource(index, component.reference, component.normalization_rule))
        outputs.append(BoundCompositeColumn(
            composite.identifier, composite.output_name.strip(), composite.prefix, tuple(sources), composite.action,
        ))

    if not outputs:
        raise PlanningError("Ao menos uma coluna precisa permanecer no plano de saída.")
    names: set[str] = set()
    for output in outputs:
        if output.output_name in names:
            raise PlanningError(f"Cabeçalho de saída repetido: {output.output_name}.")
        names.add(output.output_name)
    return ProcessingPlan(tuple(physical), tuple(outputs))
