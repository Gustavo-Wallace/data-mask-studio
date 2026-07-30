import re

from data_mask_studio.anonymization import TokenGenerator, generate_token

KEY = b"fixed-test-key-with-at-least-32b"


def test_token_is_stable_with_the_same_key() -> None:
    first = generate_token(KEY, "CPF_ID", "123.456.789-00")
    second = generate_token(KEY, "CPF_ID", "123.456.789-00")

    assert first == second
    assert re.fullmatch(r"CPF_ID-[A-Z2-7]{12}", first)
    assert first == "CPF_ID-IQPEWAE2ES36"
    assert "123" not in first


def test_complete_token_has_prefix_and_exact_base32_code() -> None:
    token = generate_token(KEY, "CPF_ID", "123.456.789-00")
    prefix, code = token.rsplit("-", maxsplit=1)

    assert prefix == "CPF_ID"
    assert len(code) == 12
    assert re.fullmatch(r"[A-Z2-7]{12}", code)
    assert "=" not in token
    assert "/" not in token
    assert "+" not in token
    assert token == token.upper()


def test_different_keys_generate_different_codes() -> None:
    first = generate_token(b"A" * 32, "CLIENTE", "Ana")
    second = generate_token(b"B" * 32, "CLIENTE", "Ana")

    assert first != second


def test_new_generators_with_the_same_key_are_deterministic() -> None:
    first_generator = TokenGenerator(KEY)
    second_generator = TokenGenerator(KEY)

    assert first_generator.generate("NOME", "Ana") == second_generator.generate(
        "NOME", "Ana"
    )


def test_new_generators_with_different_keys_generate_different_codes() -> None:
    first_generator = TokenGenerator(b"A" * 32)
    second_generator = TokenGenerator(b"B" * 32)

    assert first_generator.generate("NOME", "Ana") != second_generator.generate(
        "NOME", "Ana"
    )


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
    variants = [
        "José",
        "JOSE",
        " José",
        "José!",
        "123.456.789-00",
        "12345678900",
    ]

    assert len({generate_token(KEY, "NOME", value) for value in variants}) == 6
