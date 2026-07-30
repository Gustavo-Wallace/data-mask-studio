import pytest

from data_mask_studio.consultant import is_valid_code, parse_codes


@pytest.mark.parametrize(
    "code",
    [
        "CPF_ID-ABCDEFGHI234",
        "ID2-BCDEFGHI234A",
        "IP-CDEFGHI234AB",
    ],
)
def test_valid_codes(code: str) -> None:
    assert is_valid_code(code)


@pytest.mark.parametrize(
    "code",
    [
        "IP-ABCDEFGHI23",
        "IP-ABCDEFGHI234A",
        "IP-ABCDEFGHI238",
        "IP-ABCDEFGHI23+",
        "A-ABCDEFGHI234",
        "1IP-ABCDEFGHI234",
        "IP-INVALID-PREFIX1",
    ],
)
def test_invalid_codes(code: str) -> None:
    assert not is_valid_code(code)


def test_parser_accepts_lines_commas_and_semicolons() -> None:
    raw_text = (
        "CPF_ID-ABCDEFGHI234\n"
        "IP-BCDEFGHI234A, NOME-CDEFGHI234AB;EMAIL-DEFGHI234ABC"
    )

    assert parse_codes(raw_text) == [
        "CPF_ID-ABCDEFGHI234",
        "IP-BCDEFGHI234A",
        "NOME-CDEFGHI234AB",
        "EMAIL-DEFGHI234ABC",
    ]


def test_parser_normalizes_case_and_removes_duplicates_in_order() -> None:
    raw_text = (
        " cpf_id-abcdefghi234 ; IP-BCDEFGHI234A,"
        "CPF_ID-ABCDEFGHI234\nip-bcdefghi234a "
    )

    assert parse_codes(raw_text) == [
        "CPF_ID-ABCDEFGHI234",
        "IP-BCDEFGHI234A",
    ]

