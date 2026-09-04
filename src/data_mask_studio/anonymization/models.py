from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from data_mask_studio.normalization import NormalizationRule


class ColumnAction(StrEnum):
    """Ação aplicada a uma coluna no CSV de saída."""

    PRESERVE = "preserve"
    MASK = "mask"
    EXCLUDE = "exclude"


@dataclass(slots=True, init=False)
class ColumnConfig:
    """Configuração de transformação associada a uma coluna do CSV."""

    header: str
    prefix: str
    normalization_rule: NormalizationRule
    action: ColumnAction

    def __init__(
        self,
        header: str,
        anonymize: bool = False,
        prefix: str = "",
        normalization_rule: NormalizationRule = NormalizationRule.EXACT,
        *,
        action: ColumnAction | None = None,
    ) -> None:
        self.header = header
        self.prefix = prefix
        self.normalization_rule = normalization_rule
        self.action = action or (
            ColumnAction.MASK if anonymize else ColumnAction.PRESERVE
        )

    @property
    def anonymize(self) -> bool:
        """Compatibilidade com a seleção booleana usada antes das ações."""
        return self.action is ColumnAction.MASK

    @anonymize.setter
    def anonymize(self, value: bool) -> None:
        self.action = ColumnAction.MASK if value else ColumnAction.PRESERVE


@dataclass(frozen=True, slots=True)
class ColumnValidationResult:
    """Resultado da validação de uma única coluna."""

    header: str
    is_valid: bool
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigurationValidationResult:
    """Resultado da validação do conjunto de colunas."""

    is_valid: bool
    selected_count: int
    column_results: list[ColumnValidationResult]
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizationFallback:
    """Contagem agregada, sem valores ou posições do arquivo."""

    header: str
    count: int


@dataclass(frozen=True, slots=True)
class AnonymizationResult:
    """Resumo de um arquivo CSV anonimizado com sucesso."""

    output_path: Path
    records_processed: int
    duration_seconds: float
    new_mappings: int = 0
    updated_mappings: int = 0
    normalization_fallbacks: tuple[NormalizationFallback, ...] = ()
