from pathlib import Path

import pytest

from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.detection import (
    ConfidenceLevel,
    DetectionCancelled,
    SuggestedType,
    analyze_csv_columns,
)
from data_mask_studio.normalization import NormalizationRule


def analyze(tmp_path: Path, header: str, values: list[str], *, limit: int = 100):
    path = tmp_path / "sample.csv"
    path.write_text(
        "\n".join([f"{header},aux", *(f"{value},x" for value in values)]) + "\n",
        encoding="utf-8",
    )
    return analyze_csv_columns(inspect_csv(path), row_limit=limit)


def test_detects_cpf_from_header_with_high_confidence(tmp_path: Path) -> None:
    suggestion = analyze(tmp_path, "documento_cpf", ["123.456.789-00"] * 3).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.CPF
    assert suggestion.confidence is ConfidenceLevel.HIGH
    assert suggestion.normalization_rule is NormalizationRule.CPF
    assert suggestion.anonymize


def test_detects_cpf_from_sample_with_medium_confidence(tmp_path: Path) -> None:
    suggestion = analyze(tmp_path, "valor", ["12345678900", "98765432100"]).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.CPF
    assert suggestion.confidence is ConfidenceLevel.MEDIUM


@pytest.mark.parametrize(
    ("header", "values", "expected", "normalization"),
    [
        ("CNPJ", ["12.345.678/0001-90"], SuggestedType.CNPJ, NormalizationRule.CNPJ),
        ("email", ["ana@example.com"], SuggestedType.EMAIL, NormalizationRule.EMAIL),
        ("telefone", ["(11) 99999-1234"], SuggestedType.PHONE, NormalizationRule.PHONE),
        ("ip", ["192.0.2.1"], SuggestedType.IP_ADDRESS, NormalizationRule.IP_ADDRESS),
        ("endereco_ip", ["2001:db8::1"], SuggestedType.IP_ADDRESS, NormalizationRule.IP_ADDRESS),
    ],
)
def test_detects_supported_structural_types(
    tmp_path: Path,
    header: str,
    values: list[str],
    expected: SuggestedType,
    normalization: NormalizationRule,
) -> None:
    suggestion = analyze(tmp_path, header, values).suggestions[0]

    assert suggestion.suggested_type is expected
    assert suggestion.normalization_rule is normalization
    assert suggestion.confidence is ConfidenceLevel.HIGH


def test_detects_name_from_header(tmp_path: Path) -> None:
    suggestion = analyze(tmp_path, "Nome Completo", ["Ana Silva"]).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.NAME
    assert suggestion.prefix == "NOME"
    assert suggestion.normalization_rule is NormalizationRule.COLLAPSE_WHITESPACE


def test_detects_generic_identifier(tmp_path: Path) -> None:
    suggestion = analyze(tmp_path, "identificador", ["USR-101", "USR-102"]).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.GENERIC_ID
    assert suggestion.anonymize
    assert suggestion.normalization_rule is NormalizationRule.EXACT


def test_common_and_unknown_columns_are_not_selected(tmp_path: Path) -> None:
    common = analyze(tmp_path, "descricao", ["Produto disponível"]).suggestions[0]
    unknown = analyze(tmp_path, "x", ["sem padrão", "outro valor"]).suggestions[0]

    assert common.suggested_type is SuggestedType.COMMON_TEXT
    assert common.confidence is ConfidenceLevel.HIGH
    assert not common.anonymize
    assert unknown.suggested_type is SuggestedType.UNKNOWN
    assert unknown.confidence is ConfidenceLevel.UNDEFINED
    assert not unknown.anonymize


def test_header_and_values_conflict_has_low_confidence(tmp_path: Path) -> None:
    suggestion = analyze(
        tmp_path,
        "telefone",
        ["ana@example.com", "bia@example.com"],
    ).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.PHONE
    assert suggestion.confidence is ConfidenceLevel.LOW
    assert "conflitantes" in suggestion.reason


def test_analysis_respects_row_limit_and_ignores_empty_values(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        "contato",
        ["", "   ", *(["ana@example.com"] * 5), "12345678900"],
        limit=7,
    )
    suggestion = result.suggestions[0]

    assert result.rows_analyzed == 7
    assert suggestion.sampled_values == 5
    assert suggestion.suggested_type is SuggestedType.EMAIL


def test_result_and_errors_never_expose_sample_values(tmp_path: Path) -> None:
    sensitive_value = "pessoa.secreta@example.com"
    result = analyze(tmp_path, "email", [sensitive_value])

    assert sensitive_value not in repr(result)
    assert sensitive_value not in result.suggestions[0].reason


def test_analysis_is_cooperatively_cancelled(tmp_path: Path) -> None:
    path = tmp_path / "cancel.csv"
    path.write_text("cpf,aux\n12345678900,x\n", encoding="utf-8")

    with pytest.raises(DetectionCancelled, match="cancelada") as raised:
        analyze_csv_columns(inspect_csv(path), should_cancel=lambda: True)

    assert "12345678900" not in str(raised.value)


def test_analysis_does_not_create_persistent_files(tmp_path: Path) -> None:
    path = tmp_path / "only.csv"
    path.write_text("email,aux\nana@example.com,x\n", encoding="utf-8")
    before = {item.name for item in tmp_path.iterdir()}

    analyze_csv_columns(inspect_csv(path))

    assert {item.name for item in tmp_path.iterdir()} == before
