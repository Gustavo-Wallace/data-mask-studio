import pytest

from data_mask_studio.anonymization import generate_token
from data_mask_studio.normalization import (
    NormalizationError,
    NormalizationRule,
    normalize_value,
)


def test_exact_value_is_preserved() -> None:
    value = "  José da Silva!  "
    assert normalize_value(value, NormalizationRule.EXACT) == value


def test_digits_only_removes_non_digits() -> None:
    assert normalize_value("ID: 12.345-A", NormalizationRule.DIGITS_ONLY) == "12345"


def test_digits_only_rejects_result_without_digits() -> None:
    with pytest.raises(NormalizationError):
        normalize_value("abc-!", NormalizationRule.DIGITS_ONLY)


def test_cpf_with_and_without_punctuation_are_equivalent() -> None:
    formatted = normalize_value("123.456.789-00", NormalizationRule.CPF)
    plain = normalize_value("12345678900", NormalizationRule.CPF)
    assert formatted == plain == "12345678900"


def test_cpf_rejects_invalid_length() -> None:
    with pytest.raises(NormalizationError):
        normalize_value("123.456", NormalizationRule.CPF)


def test_cnpj_with_and_without_punctuation_are_equivalent() -> None:
    formatted = normalize_value("12.345.678/0001-90", NormalizationRule.CNPJ)
    plain = normalize_value("12345678000190", NormalizationRule.CNPJ)
    assert formatted == plain == "12345678000190"
    assert generate_token(b"N" * 32, "CNPJ", formatted) == generate_token(
        b"N" * 32, "CNPJ", plain
    )


def test_phone_formatted_and_plain_are_equivalent() -> None:
    formatted = normalize_value("+55 (81) 99999-0000", NormalizationRule.PHONE)
    plain = normalize_value("5581999990000", NormalizationRule.PHONE)
    assert formatted == plain == "5581999990000"
    assert generate_token(b"N" * 32, "FONE", formatted) == generate_token(
        b"N" * 32, "FONE", plain
    )


@pytest.mark.parametrize("value", ["1234567", "1" * 16])
def test_phone_rejects_invalid_length(value: str) -> None:
    with pytest.raises(NormalizationError):
        normalize_value(value, NormalizationRule.PHONE)


def test_email_trims_normalizes_unicode_and_lowercases() -> None:
    assert (
        normalize_value("  USER＠Example.COM  ", NormalizationRule.EMAIL)
        == "user@example.com"
    )


@pytest.mark.parametrize("value", ["missing-at", "a@@example.com", "@example.com", "a@"]) 
def test_email_rejects_invalid_value(value: str) -> None:
    with pytest.raises(NormalizationError):
        normalize_value(value, NormalizationRule.EMAIL)


def test_ipv4_is_canonical() -> None:
    assert normalize_value(" 192.168.1.1 ", NormalizationRule.IP_ADDRESS) == "192.168.1.1"


def test_expanded_and_compressed_ipv6_are_equivalent() -> None:
    expanded = normalize_value(
        "2001:0DB8:0000:0000:0000:0000:0000:0001",
        NormalizationRule.IP_ADDRESS,
    )
    compressed = normalize_value("2001:db8::1", NormalizationRule.IP_ADDRESS)
    assert expanded == compressed == "2001:db8::1"
    assert generate_token(b"N" * 32, "IP", expanded) == generate_token(
        b"N" * 32, "IP", compressed
    )


def test_invalid_ip_is_rejected() -> None:
    with pytest.raises(NormalizationError):
        normalize_value("999.1.1.1", NormalizationRule.IP_ADDRESS)


def test_text_collapses_repeated_whitespace() -> None:
    value = "  João\t  da\nSilva  "
    assert (
        normalize_value(value, NormalizationRule.COLLAPSE_WHITESPACE)
        == "João da Silva"
    )


@pytest.mark.parametrize("rule", list(NormalizationRule))
@pytest.mark.parametrize("value", ["", "   "])
def test_empty_and_whitespace_values_are_preserved(
    value: str, rule: NormalizationRule
) -> None:
    assert normalize_value(value, rule) == value
