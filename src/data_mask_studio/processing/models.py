from dataclasses import dataclass, field
from uuid import UUID, uuid4

from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.normalization.models import NormalizationRule


@dataclass(frozen=True, slots=True)
class CompositeSource:
    reference: SourceColumnRef
    normalization_rule: NormalizationRule = NormalizationRule.EXACT


@dataclass(frozen=True, slots=True)
class CompositeColumnConfig:
    output_name: str
    prefix: str = ""
    components: tuple[CompositeSource, ...] = ()
    identifier: UUID = field(default_factory=uuid4)
    action: ColumnAction = ColumnAction.PRESERVE

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(self.components))


@dataclass(frozen=True, slots=True)
class BoundScalarColumn:
    """Snapshot da política de uma coluna física, inclusive quando excluída."""

    input_index: int
    reference: SourceColumnRef
    action: ColumnAction
    normalization_rule: NormalizationRule
    prefix: str
    output_name: str


@dataclass(frozen=True, slots=True)
class BoundCompositeSource:
    """input_index sempre referencia a linha ORIGINAL, nunca a projeção."""

    input_index: int
    reference: SourceColumnRef
    normalization_rule: NormalizationRule


@dataclass(frozen=True, slots=True)
class BoundCompositeColumn:
    identifier: UUID
    output_name: str
    prefix: str
    components: tuple[BoundCompositeSource, ...]
    action: ColumnAction = ColumnAction.PRESERVE


@dataclass(frozen=True, slots=True)
class ProcessingPlan:
    """Plano construído pelo planner; índices físicos são sempre 0-based.

    Não contém linhas, valores, conexões ou configurações escalares mutáveis.
    """

    physical_columns: tuple[BoundScalarColumn, ...]
    outputs: tuple[BoundScalarColumn | BoundCompositeColumn, ...]

    @property
    def final_headers(self) -> tuple[str, ...]:
        return tuple(column.output_name for column in self.outputs)

    @property
    def requires_masking(self) -> bool:
        return any(column.action is ColumnAction.MASK for column in self.outputs)
