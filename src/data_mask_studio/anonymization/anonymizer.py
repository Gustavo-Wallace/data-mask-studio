from collections.abc import Sequence

from data_mask_studio.anonymization.models import ColumnConfig
from data_mask_studio.anonymization.token_generator import generate_token


def anonymize_row(
    row: Sequence[str],
    configurations: Sequence[ColumnConfig],
    secret_key: bytes,
) -> list[str]:
    """Anonimiza somente os índices selecionados de uma linha."""
    anonymized = list(row)
    for index, configuration in enumerate(configurations):
        if configuration.anonymize and index < len(anonymized):
            anonymized[index] = generate_token(
                secret_key,
                configuration.prefix,
                anonymized[index],
            )
    return anonymized

