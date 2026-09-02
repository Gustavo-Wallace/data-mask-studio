import codecs
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
from data_mask_studio.detection.value_detectors import matches_datetime, matches_type


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
    assert suggestion.normalization_rule is NormalizationRule.PERSON_NAME


@pytest.mark.parametrize(
    "header", ["nome", "nome_completo", "usuario", "responsavel", "titular"]
)
def test_person_name_headers_suggest_person_name_normalization(
    tmp_path: Path, header: str
) -> None:
    suggestion = analyze(tmp_path, header, ["Ana Silva"]).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.NAME
    assert suggestion.normalization_rule is NormalizationRule.PERSON_NAME


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


def test_detection_uses_resolved_empty_header(tmp_path: Path) -> None:
    path = tmp_path / "empty-header.csv"
    path.write_text(",CPF\nAna,12345678900\n", encoding="utf-8")

    result = analyze_csv_columns(inspect_csv(path))

    assert [item.header for item in result.suggestions] == ["column_1", "CPF"]


def test_utf16_person_name_detection_reads_unicode_values(tmp_path: Path) -> None:
    path = tmp_path / "names-utf16.csv"
    content = "NOME,CIDADE\r\nJoão     da Silva,Brasília\r\n"
    path.write_bytes(codecs.BOM_UTF16_BE + content.encode("utf-16-be"))

    result = analyze_csv_columns(inspect_csv(path))
    suggestion = result.suggestions[0]

    assert suggestion.header == "NOME"
    assert suggestion.suggested_type is SuggestedType.NAME
    assert suggestion.normalization_rule is NormalizationRule.PERSON_NAME


@pytest.mark.parametrize(
    "value",
    [
        "31/07/2026 23:59",
        "31/07/2026 23:59:59",
        "31/07/2026",
        "2026-07-31",
        "2026-07-31 23:59",
        "2026-07-31 23:59:59",
        "2026-07-31T23:59:59",
        "2026-07-31T23:59:59-03:00",
        "2026-07-31T23:59:59Z",
        "29/02/2024 12:30",
    ],
)
def test_valid_calendar_date_and_datetime_formats_are_detected(value: str) -> None:
    assert matches_datetime(value)
    assert matches_type(value, SuggestedType.DATETIME)


@pytest.mark.parametrize(
    "value",
    [
        "31/13/2026 23:59",
        "29/02/2023",
        "2026-02-30T23:59:59Z",
        "2026-07-31T25:00:00-03:00",
    ],
)
def test_impossible_dates_are_rejected_by_calendar_validation(value: str) -> None:
    assert not matches_datetime(value)


def test_real_data_hora_brt_case_is_not_a_phone(tmp_path: Path) -> None:
    value = "31/07/2026 23:59"
    suggestion = analyze(tmp_path, "Data/Hora (BRT)", [value] * 4).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.DATETIME
    assert suggestion.confidence is ConfidenceLevel.HIGH
    assert not suggestion.anonymize
    assert suggestion.prefix == ""
    assert suggestion.normalization_rule is NormalizationRule.EXACT
    assert not matches_type(value, SuggestedType.PHONE)
    assert value not in suggestion.reason


@pytest.mark.parametrize(
    "header",
    [
        "data",
        "Hora UTC",
        "data-hora",
        "datetime",
        "date",
        "time",
        "timestamp",
        "created_at",
        "updated_at",
        "login_time",
    ],
)
def test_portuguese_and_english_datetime_headers(tmp_path: Path, header: str) -> None:
    suggestion = analyze(tmp_path, header, ["2026-07-31T23:59:59Z"]).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.DATETIME
    assert suggestion.confidence is ConfidenceLevel.HIGH


def test_datetime_from_sample_only_has_medium_confidence(tmp_path: Path) -> None:
    suggestion = analyze(
        tmp_path,
        "evento",
        ["31/07/2026", "2026-08-01T10:20:30Z"],
    ).suggestions[0]

    assert suggestion.suggested_type is SuggestedType.DATETIME
    assert suggestion.confidence is ConfidenceLevel.MEDIUM


def test_empty_values_are_ignored_and_mixed_column_is_not_datetime(
    tmp_path: Path,
) -> None:
    with_empty = analyze(
        tmp_path,
        "evento",
        ["", "   ", "31/07/2026", "2026-08-01 10:20"],
    ).suggestions[0]
    mixed = analyze(
        tmp_path,
        "evento",
        ["31/07/2026", "produto", "pendente", "sem data"],
    ).suggestions[0]

    assert with_empty.suggested_type is SuggestedType.DATETIME
    assert with_empty.sampled_values == 2
    assert mixed.suggested_type is not SuggestedType.DATETIME


@pytest.mark.parametrize(
    "value",
    [
        "(61) 99999-9999",
        "61 99999-9999",
        "+55 61 99999-9999",
        "61999999999",
        "6133334444",
        "123456789012",
    ],
)
def test_phone_values_remain_phones_and_are_not_dates(value: str) -> None:
    assert matches_type(value, SuggestedType.PHONE)
    assert not matches_type(value, SuggestedType.DATETIME)


def test_existing_structural_types_are_not_regressed(tmp_path: Path) -> None:
    cases = (
        ("cpf", "123.456.789-00", SuggestedType.CPF),
        ("cnpj", "12.345.678/0001-90", SuggestedType.CNPJ),
        ("email", "ana@example.com", SuggestedType.EMAIL),
        ("endereco_ip", "192.0.2.1", SuggestedType.IP_ADDRESS),
        ("nome", "Ana Silva", SuggestedType.NAME),
        ("telefone", "(61) 99999-9999", SuggestedType.PHONE),
    )

    for header, value, expected in cases:
        assert analyze(tmp_path, header, [value]).suggestions[0].suggested_type is expected
