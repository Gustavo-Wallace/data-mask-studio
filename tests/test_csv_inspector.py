import codecs
from pathlib import Path

import pytest

from data_mask_studio.csv_tools import (
    CSVInspectionError,
    format_header_replacement_warning,
    inspect_csv,
    resolve_empty_headers,
)


def write_bytes(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def encode_with_bom(content: str, encoding: str) -> bytes:
    bom = {
        "utf-16-le": codecs.BOM_UTF16_LE,
        "utf-16-be": codecs.BOM_UTF16_BE,
        "utf-32-le": codecs.BOM_UTF32_LE,
        "utf-32-be": codecs.BOM_UTF32_BE,
    }[encoding]
    return bom + content.encode(encoding)


def test_csv_separated_by_comma(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "people.csv", b"name,age,city\nAna,30,Recife\n")

    result = inspect_csv(path)

    assert result.delimiter == ","
    assert result.headers == ["name", "age", "city"]
    assert result.encoding == "utf-8"


def test_valid_single_column_csv_uses_safe_default_delimiter(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "cpf.csv", b"CPF\n12345678900\n98765432100\n")

    result = inspect_csv(path)

    assert result.delimiter == ","
    assert result.headers == ["CPF"]
    assert result.encoding == "utf-8"


def test_csv_separated_by_semicolon(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "people.csv", b"name;age;city\nAna;30;Recife\n")

    result = inspect_csv(path)

    assert result.delimiter == ";"
    assert result.headers == ["name", "age", "city"]


def test_csv_utf8_with_bom(tmp_path: Path) -> None:
    path = write_bytes(
        tmp_path / "people.csv",
        "nome,cidade\nJoão,São Paulo\n".encode("utf-8-sig"),
    )

    result = inspect_csv(path)

    assert result.encoding == "utf-8-sig"
    assert result.headers == ["nome", "cidade"]


def test_csv_windows_1252(tmp_path: Path) -> None:
    path = write_bytes(
        tmp_path / "products.csv",
        "descrição;preço\nAção;10,00\n".encode("cp1252"),
    )

    result = inspect_csv(path)

    assert result.encoding == "windows-1252"
    assert result.headers == ["descrição", "preço"]


@pytest.mark.parametrize(
    ("encoding", "delimiter"),
    [
        ("utf-16-le", ","),
        ("utf-16-be", ";"),
        ("utf-32-le", ","),
        ("utf-32-be", ";"),
    ],
)
def test_unicode_bom_encodings_are_detected_without_contaminating_header(
    tmp_path: Path, encoding: str, delimiter: str
) -> None:
    content = (
        f"CPF{delimiter}NOME{delimiter}CIDADE\r\n"
        f"123{delimiter}João da Silva{delimiter}Brasília\r\n"
    )
    path = write_bytes(
        tmp_path / f"{encoding}.csv",
        encode_with_bom(content, encoding),
    )

    result = inspect_csv(path)

    assert result.encoding == encoding
    assert result.delimiter == delimiter
    assert result.headers == ["CPF", "NOME", "CIDADE"]
    assert not result.headers[0].startswith("\ufeff")


def test_utf32_le_bom_is_checked_before_ambiguous_utf16_le_bom(
    tmp_path: Path,
) -> None:
    path = write_bytes(
        tmp_path / "ambiguous.csv",
        encode_with_bom("CPF,NOME\n123,Márcia Gonçalves\n", "utf-32-le"),
    )

    assert inspect_csv(path).encoding == "utf-32-le"


def test_utf16_empty_headers_use_the_existing_header_resolver(tmp_path: Path) -> None:
    path = write_bytes(
        tmp_path / "empty-headers.csv",
        encode_with_bom(",CPF,,NOME\r\nx,123,y,João\r\n", "utf-16-le"),
    )

    result = inspect_csv(path)

    assert result.headers == ["column_1", "CPF", "column_3", "NOME"]
    assert [item.position for item in result.header_replacements] == [1, 3]


@pytest.mark.parametrize(
    ("payload", "declared_encoding"),
    [
        (codecs.BOM_UTF16_BE + b"\x00", "UTF-16-BE"),
        (codecs.BOM_UTF32_LE + b"\x00\x00", "UTF-32-LE"),
    ],
)
def test_truncated_declared_unicode_encoding_fails_without_legacy_fallback(
    tmp_path: Path, payload: bytes, declared_encoding: str
) -> None:
    path = write_bytes(tmp_path / "truncated.csv", payload)

    with pytest.raises(CSVInspectionError, match=declared_encoding):
        inspect_csv(path)


def test_bomless_null_encoded_file_is_rejected_conservatively(tmp_path: Path) -> None:
    path = write_bytes(
        tmp_path / "bomless.csv",
        "CPF,NOME\n123,João\n".encode("utf-16-le"),
    )

    with pytest.raises(CSVInspectionError, match="sem BOM"):
        inspect_csv(path)


def test_latin1_fallback_remains_available(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "latin1.csv", b"name,note\nAna,\x81\n")

    assert inspect_csv(path).encoding == "latin-1"


def test_empty_file(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "empty.csv", b"")

    with pytest.raises(CSVInspectionError, match="vazio"):
        inspect_csv(path)


def test_all_empty_headers_are_recovered(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "no-headers.csv", b",,\n1,2,3\n")

    result = inspect_csv(path)

    assert result.headers == ["column_1", "column_2", "column_3"]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (",CPF,NOME\nvalor,123,Ana\n", ["column_1", "CPF", "NOME"]),
        ("CPF,,NOME\n123,valor,Ana\n", ["CPF", "column_2", "NOME"]),
        ("CPF,NOME,\n123,Ana,valor\n", ["CPF", "NOME", "column_3"]),
        (
            ",CPF,,NOME\nvalor,123,outro,Ana\n",
            ["column_1", "CPF", "column_3", "NOME"],
        ),
    ],
)
def test_empty_headers_are_resolved_by_one_based_position(
    tmp_path: Path, content: str, expected: list[str]
) -> None:
    result = inspect_csv(write_bytes(tmp_path / "headers.csv", content.encode()))

    assert result.headers == expected


def test_tenth_empty_header_uses_one_based_position(tmp_path: Path) -> None:
    path = write_bytes(
        tmp_path / "ten.csv",
        b"a,b,c,d,e,f,g,h,i,\n1,2,3,4,5,6,7,8,9,10\n",
    )

    assert inspect_csv(path).headers[-1] == "column_10"


@pytest.mark.parametrize("blank", [" ", "    ", "\t"])
def test_whitespace_only_headers_are_recovered(
    tmp_path: Path, blank: str
) -> None:
    path = tmp_path / "whitespace.csv"
    path.write_text(f'CPF,"{blank}",EMAIL\n123,value,a@example.com\n', encoding="utf-8")

    result = inspect_csv(path)

    assert result.headers == ["CPF", "column_2", "EMAIL"]


def test_single_whitespace_only_header_is_recovered(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "single-whitespace.csv", b"   \n")

    result = inspect_csv(path)

    assert result.headers == ["column_1"]
    assert result.header_replacements[0].position == 1


def test_valid_headers_are_preserved_byte_for_byte_in_memory(tmp_path: Path) -> None:
    path = tmp_path / "valid.csv"
    path.write_text("  Nome  ,NÓME,CPF/ID\nAna,Ana,123\n", encoding="utf-8")

    assert inspect_csv(path).headers == ["  Nome  ", "NÓME", "CPF/ID"]


def test_collision_resolution_considers_all_real_headers() -> None:
    headers = ["CPF", "", "column_2", "column_2_2", "column_2_3"]

    first, replacements = resolve_empty_headers(headers)
    second, _ = resolve_empty_headers(headers)

    assert first == ["CPF", "column_2_4", "column_2", "column_2_2", "column_2_3"]
    assert first == second
    assert replacements[0].position == 2
    assert replacements[0].synthetic_name == "column_2_4"


def test_header_replacement_warning_is_safe_and_descriptive(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "warning.csv", b",CPF,,NOME\na,1,b,Ana\n")
    result = inspect_csv(path)

    assert format_header_replacement_warning(result.header_replacements) == (
        "2 cabeçalhos vazios foram substituídos: "
        "coluna 1 → column_1; coluna 3 → column_3."
    )


def test_valid_headers_do_not_generate_replacement_warning(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "valid.csv", b"CPF,NOME\n123,Ana\n")
    result = inspect_csv(path)

    assert result.header_replacements == ()
    assert format_header_replacement_warning(result.header_replacements) == ""


def test_header_order_is_preserved(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "ordered.csv", b"third|first|second\n3|1|2\n")

    result = inspect_csv(path)

    assert result.delimiter == "|"
    assert result.headers == ["third", "first", "second"]


def test_tab_separator(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "tab.csv", b"name\tage\nAna\t30\n")

    assert inspect_csv(path).delimiter == "\t"


def test_invalid_csv_header(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "invalid.csv", b'"name,age\n')

    with pytest.raises(CSVInspectionError):
        inspect_csv(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CSVInspectionError, match="não existe"):
        inspect_csv(tmp_path / "missing.csv")
