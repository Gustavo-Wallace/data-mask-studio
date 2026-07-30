from collections.abc import Sequence

from data_mask_studio.anonymization.models import ColumnConfig
from data_mask_studio.anonymization.token_generator import generate_token
from data_mask_studio.normalization import NormalizationError, normalize_value


class ColumnNormalizationError(NormalizationError):
    def __init__(self, column_index: int) -> None:
        super().__init__("Um valor não é compatível com a regra da coluna.")
        self.column_index = column_index


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
    anonymized = list(row)
    canonical_values: dict[int, str] = {}
    for index, configuration in enumerate(configurations):
        if configuration.anonymize and index < len(anonymized):
            original_value = anonymized[index]
            if original_value == "" or original_value.isspace():
                continue
            try:
                canonical_value = normalize_value(
                    original_value, configuration.normalization_rule
                )
            except NormalizationError as error:
                raise ColumnNormalizationError(index) from error
            canonical_values[index] = canonical_value
            anonymized[index] = generate_token(
                secret_key,
                configuration.prefix,
                canonical_value,
            )
    return anonymized, canonical_values
