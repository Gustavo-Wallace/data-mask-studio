import codecs
import csv
import re
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnConfig, generate_token
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.csv_tools.csv_anonymizer import (
    CSVAnonymizationError,
    ProcessingCancelled,
    anonymize_csv,
)
from data_mask_studio.vault import (
    MappingCandidate,
    VaultCipher,
    VaultRepository,
)

KEY = b"K" * 32
VAULT_KEY = b"V" * 32


def make_vault(tmp_path: Path) -> VaultRepository:
    return VaultRepository(tmp_path / "vault.db", VaultCipher(VAULT_KEY))


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


def test_repeated_processing_of_same_file_produces_same_tokens(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("name\nAna\nBruna\nAna\n", encoding="utf-8")
    first_output = tmp_path / "first.csv"
    second_output = tmp_path / "second.csv"
    configurations = [ColumnConfig("name", anonymize=True, prefix="NOME")]

    for destination in (first_output, second_output):
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=configurations,
            secret_key=KEY,
        )

    assert first_output.read_bytes() == second_output.read_bytes()
    with first_output.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file))
    assert rows[1][0] == rows[3][0]
    assert re_full_base32_token(rows[1][0], "NOME")


def test_same_value_in_different_files_produces_same_token(tmp_path: Path) -> None:
    first_source = tmp_path / "first-input.csv"
    second_source = tmp_path / "second-input.csv"
    first_source.write_text("name\nAna\n", encoding="utf-8")
    second_source.write_text("name\nAna\n", encoding="utf-8")
    configurations = [ColumnConfig("name", anonymize=True, prefix="NOME")]
    generated_tokens: list[str] = []

    for index, source in enumerate((first_source, second_source)):
        destination = tmp_path / f"output-{index}.csv"
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=configurations,
            secret_key=KEY,
        )
        with destination.open(
            "r", encoding="utf-8-sig", newline=""
        ) as output_file:
            generated_tokens.append(list(csv.reader(output_file))[1][0])

    assert generated_tokens[0] == generated_tokens[1]


def re_full_base32_token(token: str, prefix: str) -> bool:
    return re.fullmatch(rf"{prefix}-[A-Z2-7]{{12}}", token) is not None


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


def test_csv_processing_creates_and_updates_vault_mappings(tmp_path: Path) -> None:
    source = tmp_path / "people.csv"
    source.write_text("name\nAna\nAna\nBruna\n", encoding="utf-8")
    repository = make_vault(tmp_path)
    configuration = [ColumnConfig("name", True, "NOME")]

    first_result = anonymize_csv(
        source,
        tmp_path / "first-output.csv",
        encoding="utf-8",
        delimiter=",",
        configurations=configuration,
        secret_key=KEY,
        vault_repository=repository,
    )

    ana_code = generate_token(KEY, "NOME", "Ana")
    bruna_code = generate_token(KEY, "NOME", "Bruna")
    ana_record = repository.get_record(ana_code)
    bruna_record = repository.get_record(bruna_code)
    assert first_result.new_mappings == 2
    assert first_result.updated_mappings == 0
    assert repository.count() == 2
    assert ana_record is not None and ana_record.occurrence_count == 2
    assert bruna_record is not None and bruna_record.occurrence_count == 1

    second_result = anonymize_csv(
        source,
        tmp_path / "second-output.csv",
        encoding="utf-8",
        delimiter=",",
        configurations=configuration,
        secret_key=KEY,
        vault_repository=repository,
    )

    ana_record = repository.get_record(ana_code)
    assert second_result.new_mappings == 0
    assert second_result.updated_mappings == 2
    assert repository.count() == 2
    assert ana_record is not None and ana_record.occurrence_count == 4


def test_vault_rolls_back_after_processing_error(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("name\nAna\nBruna\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)

    def fail_after_first_row(_: int) -> None:
        raise RuntimeError("forced failure")

    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("name", True, "NOME")],
            secret_key=KEY,
            vault_repository=repository,
            mapping_batch_size=1,
            progress_callback=fail_after_first_row,
        )

    assert repository.count() == 0
    assert not destination.exists()


def test_vault_rolls_back_after_cancellation(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("name\nAna\nBruna\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)
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
            vault_repository=repository,
            mapping_batch_size=1,
            progress_callback=update_progress,
            should_cancel=lambda: processed == 1,
        )

    assert repository.count() == 0
    assert not destination.exists()


def test_vault_collision_blocks_csv_without_exposing_values(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    original_content = b"name\nAna\n"
    source.write_bytes(original_content)
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)
    code = generate_token(KEY, "NOME", "Ana")
    sensitive_sentinel = "SENSITIVE_SENTINEL"
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [MappingCandidate(code, "NOME", sensitive_sentinel, "name")]
        )

    with pytest.raises(CSVAnonymizationError) as captured:
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("name", True, "NOME")],
            secret_key=KEY,
            vault_repository=repository,
            mapping_batch_size=1,
        )

    visible_error = str(captured.value)
    technical_cause = str(captured.value.__cause__)
    assert "Ana" not in visible_error + technical_cause
    assert sensitive_sentinel not in visible_error + technical_cause
    assert source.read_bytes() == original_content
    assert repository.count() == 1
    assert not destination.exists()


def test_cpf_variations_generate_same_token_and_are_preserved(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cpf.csv"
    source.write_text(
        "CPF\n123.456.789-00\n12345678900\n123.456.789-00\n",
        encoding="utf-8",
    )
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)
    configuration = [
        ColumnConfig(
            "CPF",
            True,
            "CPF",
            NormalizationRule.CPF,
        )
    ]

    anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=configuration,
        secret_key=KEY,
        vault_repository=repository,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file))
    assert rows[1][0] == rows[2][0] == rows[3][0]
    mapping = repository.get_decrypted_mapping(rows[1][0])
    assert mapping is not None
    assert mapping.normalization_rule is NormalizationRule.CPF
    assert mapping.occurrence_count == 3
    assert len(mapping.variations) == 2
    occurrences = {
        variation.original_value: variation.occurrence_count
        for variation in mapping.variations
    }
    assert occurrences == {"123.456.789-00": 2, "12345678900": 1}
    for database_file in tmp_path.glob("vault.db*"):
        stored_bytes = database_file.read_bytes()
        assert b"12345678900" not in stored_bytes
        assert b"123.456.789-00" not in stored_bytes


def test_same_canonical_value_with_different_prefixes_has_different_tokens(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cpf.csv"
    source.write_text(
        "primary,secondary\n123.456.789-00,12345678900\n",
        encoding="utf-8",
    )
    destination = tmp_path / "output.csv"
    configurations = [
        ColumnConfig("primary", True, "CPF_A", NormalizationRule.CPF),
        ColumnConfig("secondary", True, "CPF_B", NormalizationRule.CPF),
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
        row = list(csv.reader(output_file))[1]
    assert row[0] != row[1]
    assert row[0].startswith("CPF_A-")
    assert row[1].startswith("CPF_B-")


def test_normalization_error_rolls_back_vault_and_removes_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cpf.csv"
    original_content = b"CPF\n123.456.789-00\ninvalid-sensitive-value\n"
    source.write_bytes(original_content)
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)

    with pytest.raises(CSVAnonymizationError) as captured:
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[
                ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF)
            ],
            secret_key=KEY,
            vault_repository=repository,
            mapping_batch_size=1,
        )

    message = str(captured.value)
    cause_messages: list[str] = []
    cause = captured.value.__cause__
    while cause is not None:
        cause_messages.append(str(cause))
        cause = cause.__cause__
    assert "coluna ‘CPF’" in message
    assert "linha 3" in message
    assert "invalid-sensitive-value" not in message
    assert all("invalid-sensitive-value" not in text for text in cause_messages)
    assert repository.count() == 0
    assert source.read_bytes() == original_content
    assert not destination.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_real_collision_compares_canonical_value(tmp_path: Path) -> None:
    source = tmp_path / "cpf.csv"
    source.write_text("CPF\n123.456.789-00\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)
    code = generate_token(KEY, "CPF", "12345678900")
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    code,
                    "CPF",
                    "different-sensitive-value",
                    "CPF",
                    canonical_value="00000000000",
                    normalization_rule=NormalizationRule.CPF,
                )
            ]
        )

    with pytest.raises(CSVAnonymizationError, match="conflito de código"):
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[
                ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF)
            ],
            secret_key=KEY,
            vault_repository=repository,
        )

    assert repository.count() == 1
    assert not destination.exists()
