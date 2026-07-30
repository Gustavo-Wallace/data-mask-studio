from collections import Counter
from collections.abc import Sequence

from data_mask_studio.anonymization.models import (
    ColumnConfig,
    ColumnValidationResult,
    ConfigurationValidationResult,
)
from data_mask_studio.anonymization.prefix_rules import validate_prefix


def create_column_configs(headers: Sequence[str]) -> list[ColumnConfig]:
    """Cria uma configuração por cabeçalho, preservando a ordem recebida."""
    return [ColumnConfig(header=header) for header in headers]


def validate_configuration(
    configurations: Sequence[ColumnConfig],
) -> ConfigurationValidationResult:
    """Valida as colunas selecionadas e ignora as demais."""
    selected = [configuration for configuration in configurations if configuration.anonymize]
    if not selected:
        return ConfigurationValidationResult(
            is_valid=False,
            selected_count=0,
            column_results=[
                ColumnValidationResult(configuration.header, is_valid=True)
                for configuration in configurations
            ],
            error_message="Selecione ao menos uma coluna para anonimizar.",
        )

    prefix_counts = Counter(configuration.prefix for configuration in selected)
    column_results: list[ColumnValidationResult] = []

    for configuration in configurations:
        if not configuration.anonymize:
            column_results.append(
                ColumnValidationResult(configuration.header, is_valid=True)
            )
            continue

        error_message = validate_prefix(configuration.prefix)
        if error_message is None and prefix_counts[configuration.prefix] > 1:
            error_message = "O prefixo está repetido em outra coluna selecionada."

        column_results.append(
            ColumnValidationResult(
                header=configuration.header,
                is_valid=error_message is None,
                error_message=error_message,
            )
        )

    is_valid = all(result.is_valid for result in column_results)
    return ConfigurationValidationResult(
        is_valid=is_valid,
        selected_count=len(selected),
        column_results=column_results,
        error_message=(
            None
            if is_valid
            else "Corrija as configurações inválidas antes de continuar."
        ),
    )

