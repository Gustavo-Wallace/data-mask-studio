import pytest

from data_mask_studio.anonymization import (
    ColumnConfig,
    create_column_configs,
    normalize_prefix,
    validate_configuration,
)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("CPF/ID", "CPF_ID"),
        ("Nome Completo", "NOME_COMPLETO"),
        ("Endereço IP", "ENDERECO_IP"),
    ],
)
def test_prefix_suggestion(header: str, expected: str) -> None:
    assert normalize_prefix(header) == expected


def test_prefix_suggestion_removes_accents() -> None:
    assert normalize_prefix("Número da Operação") == "NUMERO_DA_OPERACAO"


def test_prefix_suggestion_replaces_spaces_and_symbols() -> None:
    assert normalize_prefix("  conta---cliente / id  ") == "CONTA_CLIENTE_ID"


def test_prefix_suggestion_is_limited_to_24_characters() -> None:
    prefix = normalize_prefix("Cabeçalho extremamente comprido para teste")

    assert len(prefix) == 24
    assert not prefix.endswith("_")


@pytest.mark.parametrize(
    ("prefix", "message_part"),
    [
        ("", "Informe"),
        ("A", "ao menos 2"),
        ("A" * 25, "máximo 24"),
        ("1CLIENTE", "começar"),
        ("CLIENTE-01", "somente"),
        ("cliente", "maiúsculas"),
    ],
)
def test_invalid_prefix(prefix: str, message_part: str) -> None:
    result = validate_configuration(
        [ColumnConfig("Cliente", anonymize=True, prefix=prefix)]
    )

    assert not result.is_valid
    assert message_part in (result.column_results[0].error_message or "")


def test_duplicate_prefixes_are_invalid() -> None:
    configurations = [
        ColumnConfig("Cliente", anonymize=True, prefix="PESSOA"),
        ColumnConfig("Fornecedor", anonymize=True, prefix="PESSOA"),
    ]

    result = validate_configuration(configurations)

    assert not result.is_valid
    assert all(not row.is_valid for row in result.column_results)
    assert all("repetido" in (row.error_message or "") for row in result.column_results)


def test_unselected_columns_are_ignored() -> None:
    configurations = [
        ColumnConfig("Cliente", anonymize=True, prefix="CLIENTE"),
        ColumnConfig("Documento", anonymize=False, prefix="prefixo inválido"),
        ColumnConfig("Cidade", anonymize=False, prefix="CLIENTE"),
    ]

    result = validate_configuration(configurations)

    assert result.is_valid
    assert result.selected_count == 1
    assert all(row.is_valid for row in result.column_results)


def test_column_order_is_preserved() -> None:
    headers = ["Terceira", "Primeira", "Segunda"]

    configurations = create_column_configs(headers)

    assert [configuration.header for configuration in configurations] == headers


def test_at_least_one_column_must_be_selected() -> None:
    result = validate_configuration(create_column_configs(["Nome", "CPF"]))

    assert not result.is_valid
    assert result.selected_count == 0
    assert "ao menos uma coluna" in (result.error_message or "")
