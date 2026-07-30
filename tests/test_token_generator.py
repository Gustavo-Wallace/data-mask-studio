import re

from data_mask_studio.anonymization import generate_token

KEY = b"fixed-test-key-with-at-least-32b"


def test_token_is_stable_with_the_same_key() -> None:
    first = generate_token(KEY, "CPF_ID", "123.456.789-00")
    second = generate_token(KEY, "CPF_ID", "123.456.789-00")

    assert first == second
    assert re.fullmatch(r"CPF_ID-[0-9A-F]{24}", first)
    assert "123" not in first


def test_different_keys_generate_different_codes() -> None:
    first = generate_token(b"A" * 32, "CLIENTE", "Ana")
    second = generate_token(b"B" * 32, "CLIENTE", "Ana")

    assert first != second


def test_same_value_with_different_prefixes_generates_different_codes() -> None:
    first = generate_token(KEY, "CLIENTE", "Ana")
    second = generate_token(KEY, "CONTATO", "Ana")

    assert first.split("-", maxsplit=1)[1] != second.split("-", maxsplit=1)[1]


def test_different_values_generate_different_codes() -> None:
    assert generate_token(KEY, "NOME", "Ana") != generate_token(
        KEY, "NOME", "Bruna"
    )


def test_empty_value_remains_empty() -> None:
    assert generate_token(KEY, "NOME", "") == ""


def test_value_containing_only_spaces_is_preserved() -> None:
    assert generate_token(KEY, "NOME", "   ") == "   "


def test_original_value_is_not_normalized() -> None:
    variants = ["José", "JOSE", " José", "José!"]

    assert len({generate_token(KEY, "NOME", value) for value in variants}) == 4

