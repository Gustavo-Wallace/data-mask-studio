import codecs
import csv
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.csv_tools.csv_anonymizer import (
    CSVAnonymizationError,
    ProcessingCancelled,
    anonymize_csv,
)

KEY = b"K" * 32


def test_anonymization_preserves_structure_and_original_file(tmp_path: Path) -> None:
    source = tmp_path / "people.csv"
    original_content = (
        "id,name,city\n"
        "1,Ana,Recife\n"
        "2,Bruna,São Paulo\n"
        "3,Ana,Recife\n"
    ).encode("utf-8")
    source.write_bytes(original_content)
    destination = tmp_path / "people_anonymized.csv"
    progress: list[int] = []
    configurations = [
        ColumnConfig("id"),
        ColumnConfig("name", anonymize=True, prefix="NOME"),
        ColumnConfig("city", anonymize=True, prefix="CIDADE"),
    ]

    result = anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=configurations,
        secret_key=KEY,
        progress_callback=progress.append,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file))

    assert source.read_bytes() == original_content
    assert destination.read_bytes().startswith(codecs.BOM_UTF8)
    assert rows[0] == ["id", "name", "city"]
    assert [row[0] for row in rows[1:]] == ["1", "2", "3"]
    assert rows[1][1] == rows[3][1]
    assert rows[1][2] == rows[3][2]
    assert rows[1][1].startswith("NOME-")
    assert rows[1][2].startswith("CIDADE-")
    assert len(rows) == 4
    assert result.records_processed == 3
    assert progress == [1, 2, 3]


def test_semicolon_separator_is_preserved(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("id;name\n1;Ana\n", encoding="utf-8")
    destination = tmp_path / "output.csv"

    anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=";",
        configurations=[
            ColumnConfig("id"),
            ColumnConfig("name", anonymize=True, prefix="NOME"),
        ],
        secret_key=KEY,
    )

    output = destination.read_text(encoding="utf-8-sig")
    assert output.splitlines()[0] == "id;name"
    assert output.splitlines()[1].startswith("1;NOME-")


def test_utf8_bom_input_is_supported(tmp_path: Path) -> None:
    source = tmp_path / "bom.csv"
    source.write_text("nome,cidade\nJoão,São Paulo\n", encoding="utf-8-sig")
    destination = tmp_path / "output.csv"

    anonymize_csv(
        source,
        destination,
        encoding="utf-8-sig",
        delimiter=",",
        configurations=[
            ColumnConfig("nome", anonymize=True, prefix="NOME"),
            ColumnConfig("cidade"),
        ],
        secret_key=KEY,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file))
    assert rows[0] == ["nome", "cidade"]
    assert rows[1][0].startswith("NOME-")
    assert rows[1][1] == "São Paulo"


def test_windows_1252_input_is_supported(tmp_path: Path) -> None:
    source = tmp_path / "windows.csv"
    source.write_bytes("nome;cidade\nJoão;São Paulo\n".encode("cp1252"))
    destination = tmp_path / "output.csv"

    anonymize_csv(
        source,
        destination,
        encoding="windows-1252",
        delimiter=";",
        configurations=[
            ColumnConfig("nome"),
            ColumnConfig("cidade", anonymize=True, prefix="CIDADE"),
        ],
        secret_key=KEY,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file, delimiter=";"))
    assert rows[1][0] == "João"
    assert rows[1][1].startswith("CIDADE-")


def test_empty_and_whitespace_values_are_preserved_in_csv(tmp_path: Path) -> None:
    source = tmp_path / "empty-values.csv"
    source.write_text('name,note\n,"   "\n', encoding="utf-8")
    destination = tmp_path / "output.csv"
    configurations = [
        ColumnConfig("name", anonymize=True, prefix="NOME"),
        ColumnConfig("note", anonymize=True, prefix="NOTA"),
    ]

    anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=configurations,
        secret_key=KEY,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file))
    assert rows[1] == ["", "   "]


def test_temporary_file_is_removed_after_error(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("name\nAna\n", encoding="utf-8")
    destination = tmp_path / "output.csv"

    def fail_after_first_row(_: int) -> None:
        raise RuntimeError("forced test failure")

    with pytest.raises(CSVAnonymizationError) as captured:
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("name", True, "NOME")],
            secret_key=KEY,
            progress_callback=fail_after_first_row,
        )

    assert isinstance(captured.value.__cause__, RuntimeError)
    assert not destination.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_existing_destination_remains_intact_after_error(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("name\nAna\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    previous_content = b"previous complete file"
    destination.write_bytes(previous_content)

    def fail_after_first_row(_: int) -> None:
        raise RuntimeError("forced test failure")

    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("name", True, "NOME")],
            secret_key=KEY,
            overwrite=True,
            progress_callback=fail_after_first_row,
        )

    assert destination.read_bytes() == previous_content
    assert list(tmp_path.glob("*.tmp")) == []


def test_processing_can_be_cancelled_without_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("name\nAna\nBruna\nCarla\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    processed = 0

    def update_progress(value: int) -> None:
        nonlocal processed
        processed = value

    with pytest.raises(ProcessingCancelled):
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("name", True, "NOME")],
            secret_key=KEY,
            progress_callback=update_progress,
            should_cancel=lambda: processed == 1,
        )

    assert processed == 1
    assert not destination.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_original_path_cannot_be_used_as_destination(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    original_content = b"name\nAna\n"
    source.write_bytes(original_content)

    with pytest.raises(CSVAnonymizationError, match="mesmo arquivo"):
        anonymize_csv(
            source,
            source,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("name", True, "NOME")],
            secret_key=KEY,
        )

    assert source.read_bytes() == original_content
