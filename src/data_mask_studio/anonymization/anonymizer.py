from collections.abc import Sequence

from data_mask_studio.anonymization.models import ColumnAction, ColumnConfig
from data_mask_studio.anonymization.token_generator import generate_token
from data_mask_studio.normalization import (
    NormalizationError,
    NormalizationRule,
    normalize_value,
)


def anonymize_row(
    row: Sequence[str],
    configurations: Sequence[ColumnConfig],
    secret_key: bytes,
) -> list[str]:
    """Anonimiza somente os índices selecionados de uma linha."""
    anonymized, _ = anonymize_row_with_canonical_values(
        row, configurations, secret_key
    )
    return anonymized


def anonymize_row_with_canonical_values(
    row: Sequence[str],
    configurations: Sequence[ColumnConfig],
    secret_key: bytes,
) -> tuple[list[str], dict[int, str]]:
    """Retorna a linha anonimizada e os valores canônicos usados nos tokens."""
    anonymized, canonical_values, _, _ = anonymize_row_with_metadata(
        row, configurations, secret_key
    )
    return anonymized, canonical_values


def anonymize_row_with_metadata(
    row: Sequence[str],
    configurations: Sequence[ColumnConfig],
    secret_key: bytes,
) -> tuple[
    list[str],
    dict[int, str],
    dict[int, NormalizationRule],
    tuple[int, ...],
]:
    """Anonimiza e informa regras efetivas e fallbacks sem reter valores extras."""
    transformed = list(row)
    canonical_values: dict[int, str] = {}
    effective_rules: dict[int, NormalizationRule] = {}
    fallback_indexes: list[int] = []
    for index, configuration in enumerate(configurations):
        if configuration.action is ColumnAction.MASK and index < len(transformed):
            original_value = transformed[index]
            if original_value == "" or original_value.isspace():
                continue
            try:
                canonical_value = normalize_value(
                    original_value, configuration.normalization_rule
                )
                effective_rule = configuration.normalization_rule
            except NormalizationError:
                canonical_value = normalize_value(original_value, NormalizationRule.EXACT)
                effective_rule = NormalizationRule.EXACT
                fallback_indexes.append(index)
            canonical_values[index] = canonical_value
            effective_rules[index] = effective_rule
            transformed[index] = generate_token(
                secret_key,
                configuration.prefix,
                canonical_value,
            )
    output = [
        value
        for index, value in enumerate(transformed)
        if index >= len(configurations)
        or configurations[index].action is not ColumnAction.EXCLUDE
    ]
    return output, canonical_values, effective_rules, tuple(fallback_indexes)
