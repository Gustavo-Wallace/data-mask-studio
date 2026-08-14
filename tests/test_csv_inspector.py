from pathlib import Path

import pytest

from data_mask_studio.csv_tools import CSVInspectionError, inspect_csv


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


def test_file_without_valid_headers(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "no-headers.csv", b",,\n1,2,3\n")

    with pytest.raises(CSVInspectionError, match="cabeçalhos vazios"):
        inspect_csv(path)


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
