"""Configuração das colunas que serão anonimizadas."""

from data_mask_studio.anonymization.column_config import (
    create_column_configs,
    validate_configuration,
)
from data_mask_studio.anonymization.models import (
    ColumnConfig,
    ColumnValidationResult,
    ConfigurationValidationResult,
)
from data_mask_studio.anonymization.prefix_rules import normalize_prefix

__all__ = [
    "ColumnConfig",
    "ColumnValidationResult",
    "ConfigurationValidationResult",
    "create_column_configs",
    "normalize_prefix",
    "validate_configuration",
]

