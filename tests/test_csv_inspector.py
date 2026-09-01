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
