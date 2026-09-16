from collections import Counter
from collections.abc import Sequence

from data_mask_studio.anonymization.models import (
    ColumnAction,
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
    """Valida os nomes finais e os campos usados para mascaramento."""
    remaining = [
        configuration
        for configuration in configurations
        if configuration.action is not ColumnAction.EXCLUDE
    ]
    if not remaining:
        return ConfigurationValidationResult(
            is_valid=False,
            selected_count=0,
            column_results=[
                ColumnValidationResult(configuration.header, is_valid=True)
                for configuration in configurations
            ],
            error_message="Ao menos uma coluna precisa permanecer no arquivo de saída.",
        )

    selected = [
        configuration
        for configuration in configurations
        if configuration.action is ColumnAction.MASK
    ]

    prefix_counts = Counter(configuration.prefix for configuration in selected)
    output_errors = output_header_errors(configurations)
    column_results: list[ColumnValidationResult] = []

    for index, configuration in enumerate(configurations):
        if configuration.action is not ColumnAction.MASK:
            column_results.append(
                ColumnValidationResult(
                    configuration.header,
                    is_valid=index not in output_errors,
                    error_message=output_errors.get(index),
                )
            )
            continue

        error_message = validate_prefix(configuration.prefix)
        if error_message is None and prefix_counts[configuration.prefix] > 1:
            error_message = "O prefixo está repetido em outra coluna selecionada."
        error_message = error_message or output_errors.get(index)

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
            else next(iter(output_errors.values()),
                      "Corrija as configurações inválidas antes de continuar.")
        ),
    )


def output_header_errors(configurations: Sequence[ColumnConfig]) -> dict[int, str]:
    """Compara nomes literalmente, somente entre colunas presentes na saída."""
    groups: dict[str, list[int]] = {}
    for index, configuration in enumerate(configurations):
        if configuration.action is not ColumnAction.EXCLUDE:
            groups.setdefault(configuration.effective_output_header, []).append(index)
    errors: dict[int, str] = {}
    for name, indexes in groups.items():
        if len(indexes) < 2:
            continue
        origins = ", ".join(configurations[index].header for index in indexes)
        message = f"Cabeçalho de saída repetido “{name}” nas colunas: {origins}."
        errors.update((index, message) for index in indexes)
    return errors
