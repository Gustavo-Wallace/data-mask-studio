import pytest

from data_mask_studio.anonymization import (
    ColumnAction,
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


def test_legacy_anonymize_flag_maps_to_typed_actions() -> None:
    masked = ColumnConfig("Nome", anonymize=True, prefix="NOME")
    preserved = ColumnConfig("Cidade", anonymize=False)

    assert masked.action is ColumnAction.MASK
    assert preserved.action is ColumnAction.PRESERVE
    preserved.anonymize = True
    assert preserved.action is ColumnAction.MASK


def test_preserve_and_exclude_ignore_masking_fields_during_validation() -> None:
    result = validate_configuration(
        [
            ColumnConfig(
                "Preservada",
                prefix="prefixo inválido",
                action=ColumnAction.PRESERVE,
            ),
            ColumnConfig(
                "Excluída",
                prefix="OUTRO-inválido",
                action=ColumnAction.EXCLUDE,
            ),
        ]
    )

    assert result.is_valid
    assert all(item.is_valid for item in result.column_results)


def test_all_columns_can_be_preserved_without_masking() -> None:
    result = validate_configuration(create_column_configs(["Nome", "CPF"]))

    assert result.is_valid
    assert result.selected_count == 0


def test_at_least_one_column_must_remain_in_output() -> None:
    configurations = [
        ColumnConfig("Nome", action=ColumnAction.EXCLUDE),
        ColumnConfig("CPF", action=ColumnAction.EXCLUDE),
    ]

    result = validate_configuration(configurations)

    assert not result.is_valid
    assert result.selected_count == 0
    assert "Ao menos uma coluna" in (result.error_message or "")
