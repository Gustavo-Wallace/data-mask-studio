from dataclasses import dataclass
from pathlib import Path

from data_mask_studio.normalization import NormalizationRule


@dataclass(slots=True)
class ColumnConfig:
    """Configuração de anonimização associada a uma coluna do CSV."""

    header: str
    anonymize: bool = False
    prefix: str = ""
    normalization_rule: NormalizationRule = NormalizationRule.EXACT


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
class AnonymizationResult:
    """Resumo de um arquivo CSV anonimizado com sucesso."""

    output_path: Path
    records_processed: int
    duration_seconds: float
    new_mappings: int = 0
    updated_mappings: int = 0
