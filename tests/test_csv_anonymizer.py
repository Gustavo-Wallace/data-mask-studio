import codecs
import csv
import re
import sqlite3
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnConfig, generate_token
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.normalization import NormalizationError
from data_mask_studio.restoration import (
    RestorationConfiguration,
    SelectedColumn,
    restore_csv,
)
from data_mask_studio.csv_tools.csv_anonymizer import (
    CSVAnonymizationError,
    ProcessingCancelled,
    anonymize_csv,
)
from data_mask_studio.csv_tools import inspect_csv
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


def test_single_column_csv_is_inspected_anonymized_and_keeps_structure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cpf.csv"
    source.write_text("CPF\n12345678900\n98765432100\n", encoding="utf-8")
    destination = tmp_path / "cpf-anonimizado.csv"
    inspection = inspect_csv(source)

    result = anonymize_csv(
        inspection.path,
        destination,
        encoding=inspection.encoding,
        delimiter=inspection.delimiter,
        configurations=[ColumnConfig("CPF", True, "CPF")],
        secret_key=KEY,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file, delimiter=inspection.delimiter))
    assert rows[0] == ["CPF"]
    assert len(rows) == 3
    assert all(len(row) == 1 for row in rows)
    assert all(re_full_base32_token(row[0], "CPF") for row in rows[1:])
    assert result.records_processed == 2


def test_single_unselected_column_still_requires_anonymization_selection(
    tmp_path: Path,
) -> None:
    source = tmp_path / "single.csv"
    source.write_text("Campo\nvalor\n", encoding="utf-8")
    destination = tmp_path / "output.csv"

    with pytest.raises(CSVAnonymizationError, match="Selecione ao menos uma coluna"):
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("Campo")],
            secret_key=KEY,
        )

    assert not destination.exists()


def test_single_column_preserves_empty_and_whitespace_only_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "empty-single.csv"
    source.write_text('Campo\n\n"   "\nvalor\n', encoding="utf-8")
    destination = tmp_path / "output.csv"

    result = anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=[ColumnConfig("Campo", True, "CAMPO")],
        secret_key=KEY,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file))
    assert rows[1:3] == [[""], ["   "]]
    assert re_full_base32_token(rows[3][0], "CAMPO")
    assert result.records_processed == 3


def test_empty_headers_are_resolved_in_output_without_modifying_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "empty-headers.csv"
    original = b",CPF,,NOME\nabc,123,x,Fulano\n"
    source.write_bytes(original)
    destination = tmp_path / "output.csv"

    anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=[
            ColumnConfig("column_1", True, "CAMPO"),
            ColumnConfig("CPF"),
            ColumnConfig("column_3"),
            ColumnConfig("NOME"),
        ],
        secret_key=KEY,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file))
    assert rows[0] == ["column_1", "CPF", "column_3", "NOME"]
    assert rows[1][0].startswith("CAMPO-")
    assert rows[1][1:] == ["123", "x", "Fulano"]
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    ("irregular_row", "found_columns"),
    [
        ("PRIVATE_MISSING", 1),
        ("PRIVATE_EXTRA_A,PRIVATE_EXTRA_B,PRIVATE_EXTRA_C", 3),
    ],
)
def test_irregular_csv_reports_safe_line_and_column_counts_and_rolls_back(
    tmp_path: Path,
    irregular_row: str,
    found_columns: int,
) -> None:
    source = tmp_path / "irregular.csv"
    source.write_text(
        f"Primeira,Segunda\nvalor-seguro,ok\n{irregular_row}\n",
        encoding="utf-8",
    )
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)

    with pytest.raises(CSVAnonymizationError) as captured:
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[
                ColumnConfig("Primeira", True, "PRIMEIRA"),
                ColumnConfig("Segunda"),
            ],
            secret_key=KEY,
            vault_repository=repository,
            mapping_batch_size=1,
        )

    message = str(captured.value)
    assert "linha 3" in message
    assert "conforme o cabeçalho" in message
    assert "esperadas" in message and "2" in message
    assert "encontradas" in message and str(found_columns) in message
    assert "PRIVATE_" not in message
    assert "valor-seguro" not in message
    assert repository.count() == 0
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))


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


def test_person_name_variations_generate_same_token_and_are_preserved(
    tmp_path: Path,
) -> None:
    variants = [
        "João da Silva",
        "JOÃO DA SILVA",
        "joao da silva",
        "João   da Silva",
    ]
    source = tmp_path / "names.csv"
    source.write_text("Nome\n" + "\n".join(variants) + "\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)

    anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=[
            ColumnConfig("Nome", True, "NOME", NormalizationRule.PERSON_NAME)
        ],
        secret_key=KEY,
        vault_repository=repository,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as output_file:
        rows = list(csv.reader(output_file))
    tokens = [row[0] for row in rows[1:]]
    assert len(set(tokens)) == 1

    mapping = repository.get_decrypted_mapping(tokens[0])
    assert mapping is not None
    assert mapping.canonical_value == "joao da silva"
    assert mapping.normalization_rule is NormalizationRule.PERSON_NAME
    assert mapping.occurrence_count == len(variants)
    assert {item.original_value for item in mapping.variations} == set(variants)


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


def test_incompatible_cpf_uses_exact_fallback_and_processing_continues(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cpf.csv"
    original_content = b"CPF\n123.456.789-00\ninvalid-sensitive-value\n"
    source.write_bytes(original_content)
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)

    result = anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=[ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF)],
        secret_key=KEY,
        vault_repository=repository,
        mapping_batch_size=1,
    )

    rows = list(csv.reader(destination.open("r", encoding="utf-8-sig", newline="")))
    fallback_mapping = repository.get_decrypted_mapping(rows[2][0])
    assert result.records_processed == 2
    assert [(item.header, item.count) for item in result.normalization_fallbacks] == [
        ("CPF", 1)
    ]
    assert fallback_mapping is not None
    assert fallback_mapping.normalization_rule is NormalizationRule.EXACT
    assert fallback_mapping.original_value == "invalid-sensitive-value"
    assert repository.count() == 2
    assert source.read_bytes() == original_content
    assert destination.exists()
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


def _single_column_fallback(
    tmp_path: Path,
    rule: NormalizationRule,
    value: str,
    *,
    header: str = "Campo",
    prefix: str = "CAMPO",
):
    source = tmp_path / f"{rule.value}.csv"
    source.write_text(f"{header}\n{value}\n", encoding="utf-8")
    destination = tmp_path / f"{rule.value}-output.csv"
    repository = make_vault(tmp_path)
    result = anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=[ColumnConfig(header, True, prefix, rule)],
        secret_key=KEY,
        vault_repository=repository,
    )
    with destination.open("r", encoding="utf-8-sig", newline="") as file:
        token = list(csv.reader(file))[1][0]
    return result, repository, token, source, destination


@pytest.mark.parametrize(
    ("rule", "value"),
    [
        (NormalizationRule.IP_ADDRESS, "unknown"),
        (NormalizationRule.IP_ADDRESS, "texto arbitrário"),
        (NormalizationRule.IP_ADDRESS, "192.168.1.999"),
        (NormalizationRule.CPF, "CPF indisponível"),
        (NormalizationRule.CNPJ, "CNPJ pendente"),
        (NormalizationRule.PHONE, "sem telefone"),
        (NormalizationRule.EMAIL, "sem arroba"),
        (NormalizationRule.DIGITS_ONLY, "sem dígitos"),
    ],
)
def test_structured_normalization_failures_use_exact_fallback(
    tmp_path: Path, rule: NormalizationRule, value: str
) -> None:
    result, repository, token, _, _ = _single_column_fallback(tmp_path, rule, value)

    mapping = repository.get_decrypted_mapping(token)
    assert token == generate_token(KEY, "CAMPO", value)
    assert mapping is not None
    assert mapping.original_value == value
    assert mapping.canonical_value == value
    assert mapping.normalization_rule is NormalizationRule.EXACT
    assert mapping.variations[0].normalization_rule is NormalizationRule.EXACT
    assert result.normalization_fallbacks[0].count == 1
    assert value not in repr(result)


def test_unknown_ip_fallback_restores_original_and_later_rows_continue(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ip.csv"
    source.write_text("IP\n192.0.2.1\nunknown\n198.51.100.2\n", encoding="utf-8")
    anonymized = tmp_path / "ip-anonimizado.csv"
    restored = tmp_path / "ip-restaurado.csv"
    repository = make_vault(tmp_path)
    result = anonymize_csv(
        source,
        anonymized,
        encoding="utf-8",
        delimiter=",",
        configurations=[
            ColumnConfig("IP", True, "IP", NormalizationRule.IP_ADDRESS)
        ],
        secret_key=KEY,
        vault_repository=repository,
    )
    restore_csv(
        RestorationConfiguration(
            anonymized,
            "utf-8-sig",
            ",",
            ("IP",),
            (SelectedColumn(0, "IP"),),
        ),
        restored,
        repository,
    )

    assert result.records_processed == 3
    assert result.normalization_fallbacks[0].count == 1
    assert restored.read_text(encoding="utf-8-sig").splitlines() == [
        "IP",
        "192.0.2.1",
        "unknown",
        "198.51.100.2",
    ]


def test_empty_structured_value_creates_no_token_mapping_or_fallback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "empty-ip.csv"
    source.write_text('IP\n""\n"   "\n', encoding="utf-8")
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)

    result = anonymize_csv(
        source,
        destination,
        encoding="utf-8",
        delimiter=",",
        configurations=[ColumnConfig("IP", True, "IP", NormalizationRule.IP_ADDRESS)],
        secret_key=KEY,
        vault_repository=repository,
    )

    with destination.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows[1:] == [[""], ["   "]]
    assert repository.count() == 0
    assert result.normalization_fallbacks == ()


def test_valid_structured_value_keeps_configured_normalization(tmp_path: Path) -> None:
    result, repository, token, _, _ = _single_column_fallback(
        tmp_path,
        NormalizationRule.IP_ADDRESS,
        "2001:0db8::1",
        header="IP",
        prefix="IP",
    )

    mapping = repository.get_decrypted_mapping(token)
    assert token == generate_token(KEY, "IP", "2001:db8::1")
    assert mapping is not None
    assert mapping.normalization_rule is NormalizationRule.IP_ADDRESS
    assert mapping.canonical_value == "2001:db8::1"
    assert result.normalization_fallbacks == ()


def test_exact_fallback_token_is_deterministic_and_reuses_existing_mapping(
    tmp_path: Path,
) -> None:
    value = "unknown"
    code = generate_token(KEY, "IP", value)
    repository = make_vault(tmp_path)
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [MappingCandidate(code, "IP", value, "IP", normalization_rule=NormalizationRule.EXACT)]
        )
    source = tmp_path / "input.csv"
    source.write_text(f"IP\n{value}\n{value}\n", encoding="utf-8")
    outputs = [tmp_path / "first.csv", tmp_path / "second.csv"]

    results = [
        anonymize_csv(
            source,
            output,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("IP", True, "IP", NormalizationRule.IP_ADDRESS)],
            secret_key=KEY,
            vault_repository=repository,
        )
        for output in outputs
    ]

    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert all(result.new_mappings == 0 for result in results)
    assert all(result.updated_mappings == 1 for result in results)
    mapping = repository.get_decrypted_mapping(code)
    assert mapping is not None and mapping.occurrence_count == 5


def test_fallback_counts_are_aggregated_by_column_without_values(tmp_path: Path) -> None:
    source = tmp_path / "mixed.csv"
    sentinels = ("private-ip-value", "private-cpf-one", "private-cpf-two")
    source.write_text(
        "IP,CPF\n"
        f"{sentinels[0]},{sentinels[1]}\n"
        f"192.0.2.1,{sentinels[2]}\n",
        encoding="utf-8",
    )
    repository = make_vault(tmp_path)
    result = anonymize_csv(
        source,
        tmp_path / "output.csv",
        encoding="utf-8",
        delimiter=",",
        configurations=[
            ColumnConfig("IP", True, "IP", NormalizationRule.IP_ADDRESS),
            ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF),
        ],
        secret_key=KEY,
        vault_repository=repository,
    )

    assert [(item.header, item.count) for item in result.normalization_fallbacks] == [
        ("IP", 1),
        ("CPF", 2),
    ]
    assert all(value not in repr(result) for value in sentinels)


def test_unexpected_normalizer_error_remains_fatal_and_rolls_back(
    tmp_path: Path, monkeypatch
) -> None:
    import data_mask_studio.anonymization.anonymizer as anonymizer_module

    source = tmp_path / "input.csv"
    source.write_text("IP\nunknown\nlater\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)
    sentinel = "private-unexpected-value"

    def fail_unexpectedly(_value, _rule):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(anonymizer_module, "normalize_value", fail_unexpectedly)
    with pytest.raises(CSVAnonymizationError) as raised:
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("IP", True, "IP", NormalizationRule.IP_ADDRESS)],
            secret_key=KEY,
            vault_repository=repository,
            mapping_batch_size=1,
        )

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert sentinel not in str(raised.value)
    assert repository.count() == 0
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_normalization_error_only_triggers_exact_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    import data_mask_studio.anonymization.anonymizer as anonymizer_module

    original = anonymizer_module.normalize_value

    def expected_failure(value, rule):
        if rule is NormalizationRule.IP_ADDRESS:
            raise NormalizationError("expected format mismatch")
        return original(value, rule)

    monkeypatch.setattr(anonymizer_module, "normalize_value", expected_failure)
    result, repository, token, _, _ = _single_column_fallback(
        tmp_path, NormalizationRule.IP_ADDRESS, "192.0.2.1", header="IP", prefix="IP"
    )

    mapping = repository.get_decrypted_mapping(token)
    assert result.normalization_fallbacks[0].count == 1
    assert mapping is not None and mapping.normalization_rule is NormalizationRule.EXACT


def test_sqlite_failure_after_fallback_is_fatal_and_cleans_output(
    tmp_path: Path, monkeypatch
) -> None:
    from data_mask_studio.vault.repository import VaultTransaction

    source = tmp_path / "input.csv"
    source.write_text("IP\nunknown\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    repository = make_vault(tmp_path)

    def fail_sqlite(_self, _candidates):
        raise sqlite3.OperationalError("private sqlite detail")

    monkeypatch.setattr(VaultTransaction, "upsert_batch", fail_sqlite)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(
            source,
            destination,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("IP", True, "IP", NormalizationRule.IP_ADDRESS)],
            secret_key=KEY,
            vault_repository=repository,
            mapping_batch_size=1,
        )

    assert repository.count() == 0
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_fallback_output_is_equivalent_across_mapping_batch_sizes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.csv"
    source.write_text("IP\nunknown\n192.0.2.1\nother\n", encoding="utf-8")
    outputs = []
    fallback_counts = []
    for batch_size in (1, 1000):
        folder = tmp_path / str(batch_size)
        folder.mkdir()
        output = folder / "output.csv"
        repository = VaultRepository(folder / "vault.db", VaultCipher(VAULT_KEY))
        result = anonymize_csv(
            source,
            output,
            encoding="utf-8",
            delimiter=",",
            configurations=[ColumnConfig("IP", True, "IP", NormalizationRule.IP_ADDRESS)],
            secret_key=KEY,
            vault_repository=repository,
            mapping_batch_size=batch_size,
        )
        outputs.append(output.read_bytes())
        fallback_counts.append(result.normalization_fallbacks)

    assert outputs[0] == outputs[1]
    assert fallback_counts[0] == fallback_counts[1]
