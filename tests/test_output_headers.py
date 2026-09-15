import csv
import json
import sqlite3
from dataclasses import replace

import pytest

from data_mask_studio.anonymization import ColumnAction, ColumnConfig, generate_token, validate_configuration
from data_mask_studio.csv_tools import anonymize_csv, inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService, ProfileFormatError, ProfileValidationError
from data_mask_studio.restoration import RestorationConfiguration, SelectedColumn, restore_csv
from data_mask_studio.vault import VaultCipher, VaultRepository

KEY = b"R" * 32


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.reader(file))


@pytest.mark.parametrize(("output_name", "expected"), [
    ("NOME_COMPLETO", "NOME_COMPLETO"), ("", "NOME"), (" \t ", "NOME"),
    ("Pessoa, nome", "Pessoa, nome"), ("pessoa", "pessoa"), (" Pessoa ", "Pessoa"),
    ("  DOCUMENTO  ", "DOCUMENTO"), ("NOME COMPLETO", "NOME COMPLETO"),
    ("\t Nome  Completo \r\n", "Nome  Completo"),
])
def test_optional_output_name_preserves_source_and_identity(tmp_path, output_name, expected):
    source = tmp_path / "source.csv"
    original = "NOME\nJoão\n".encode("utf-8")
    source.write_bytes(original)
    output = tmp_path / "output.csv"
    config = ColumnConfig("NOME", output_name=output_name)
    anonymize_csv(source, output, encoding="utf-8", delimiter=",", configurations=[config], secret_key=KEY)
    assert read_rows(output) == [[expected], ["João"]]
    assert source.read_bytes() == original
    assert config.header == "NOME"


def test_preserve_normalize_rename_synthetic_header_without_mappings(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(",Extra\nJOÃO     DA SILVA,descartar\n", encoding="utf-8")
    original = source.read_bytes()
    inspection = inspect_csv(source)
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    output = tmp_path / "output.csv"
    configs = [
        ColumnConfig(inspection.headers[0], normalization_rule=NormalizationRule.PERSON_NAME, output_name="PESSOA"),
        ColumnConfig("Extra", action=ColumnAction.EXCLUDE, output_name="PESSOA"),
    ]
    result = anonymize_csv(source, output, encoding=inspection.encoding, delimiter=inspection.delimiter,
                           configurations=configs, secret_key=KEY, vault_repository=repository)
    assert configs[0].header == "column_1"
    assert read_rows(output) == [["PESSOA"], ["joao da silva"]]
    assert source.read_bytes() == original
    assert result.new_mappings == result.updated_mappings == 0
    with sqlite3.connect(repository.database_path) as connection:
        for table in ("vault_mappings", "vault_variations"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_mask_tokens_independent_of_rename_and_restoration_keeps_header(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("CPF\n123.456.789-00\n", encoding="utf-8")
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    for index, name in enumerate(("", "  DOCUMENTO  ")):
        output = tmp_path / f"output-{index}.csv"
        anonymize_csv(source, output, encoding="utf-8", delimiter=",", secret_key=KEY,
                      configurations=[ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF, output_name=name)],
                      vault_repository=repository)
        assert read_rows(output) == [[name.strip() or "CPF"], [generate_token(KEY, "CPF", "12345678900")]]
    assert repository.count() == 1
    mapping = repository.get_decrypted_mapping(generate_token(KEY, "CPF", "12345678900"))
    assert mapping.source_header == "CPF"
    assert mapping.occurrence_count == 2
    restored = tmp_path / "restored.csv"
    restore_csv(RestorationConfiguration(output, "utf-8-sig", ",", ("DOCUMENTO",),
                                         (SelectedColumn(0, "DOCUMENTO"),)), restored, repository)
    assert read_rows(restored) == [["DOCUMENTO"], ["123.456.789-00"]]


@pytest.mark.parametrize("names", [("PESSOA", "PESSOA"), ("CPF", ""), ("CPF ", ""), (" PESSOA", "PESSOA\t")])
def test_output_collisions_rejected_before_output_or_vault_transaction(tmp_path, names):
    configs = [ColumnConfig("NOME", output_name=names[0]), ColumnConfig("CPF", output_name=names[1])]
    validation = validate_configuration(configs)
    assert not validation.is_valid
    assert all(not result.is_valid for result in validation.column_results)
    assert "NOME" in validation.error_message and "CPF" in validation.error_message
    source = tmp_path / "source.csv"
    source.write_text("NOME,CPF\nAna,123\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    with pytest.raises(CSVAnonymizationError, match="Nome de saída repetido"):
        anonymize_csv(source, output, encoding="utf-8", delimiter=",", configurations=configs, secret_key=KEY)
    assert not output.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_output_headers_use_literal_case_sensitive_comparison():
    assert validate_configuration([ColumnConfig("A", output_name="nome"), ColumnConfig("B", output_name="NOME")]).is_valid


def test_profile_rejects_conflicting_output_names(tmp_path):
    repository = ProfileRepository(tmp_path / "profiles.json")
    service = ProfileService(repository)
    with pytest.raises(ProfileValidationError, match="Nome de saída repetido"):
        service.create("Conflito", [ColumnConfig("A", output_name=" B "), ColumnConfig("B")])
    assert not repository.path.exists()


def test_profile_rename_round_trip_and_legacy_default(tmp_path):
    repository = ProfileRepository(tmp_path / "profiles.json")
    service = ProfileService(repository)
    created = service.create("Renomeação", [
        ColumnConfig("NOME", normalization_rule=NormalizationRule.PERSON_NAME, output_name="PESSOA"),
        ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF, output_name="DOCUMENTO"),
        ColumnConfig("OBS", action=ColumnAction.EXCLUDE, output_name="IGNORADA"),
    ])
    loaded = ProfileRepository(repository.path).load()[0]
    assert loaded == created
    applied = service.apply(loaded, ["CPF", "NOME", "OBS"])
    assert [c.output_name for c in applied.configurations] == ["DOCUMENTO", "PESSOA", "IGNORADA"]
    assert [c.header for c in applied.configurations] == ["CPF", "NOME", "OBS"]
    document = json.loads(repository.path.read_text(encoding="utf-8"))
    # Documento v1 real: container/formato legados, booleano obrigatório e sem
    # campos exclusivos de v2. A action recente continua autoritativa em v1.
    document["schema_version"] = 1
    profile = document["profiles"][0]
    profile["format_version"] = 1
    del profile["composites"], profile["unknown_column_policy"]
    for column in document["profiles"][0]["columns"]:
        column["anonymize"] = column["action"] == "mask"
        del column["output_name"]
    legacy = json.dumps(document).encode("utf-8")
    legacy_columns = repository.parse_bytes(legacy)[0].columns
    assert all(c.output_name == "" for c in legacy_columns)
    assert [c.action for c in legacy_columns] == [ColumnAction.PRESERVE, ColumnAction.MASK, ColumnAction.EXCLUDE]
    document["profiles"][0]["columns"][0]["output_name"] = 123
    with pytest.raises(ProfileFormatError):
        repository.parse_bytes(json.dumps(document).encode("utf-8"))


def test_profile_v2_missing_required_output_name_is_rejected(tmp_path):
    repository = ProfileRepository(tmp_path / "profiles.json")
    ProfileService(repository).create("Perfil v2", [ColumnConfig("NOME", output_name="PESSOA")])
    document = json.loads(repository.path.read_text(encoding="utf-8"))
    assert document["schema_version"] == document["profiles"][0]["format_version"] == 2
    del document["profiles"][0]["columns"][0]["output_name"]
    with pytest.raises(ProfileFormatError):
        repository.parse_bytes(json.dumps(document).encode("utf-8"))


def test_batch_matches_individual_headers_and_values(tmp_path):
    from data_mask_studio.batch import BatchFile, BatchService, BatchFileStatus, validate_file

    class KeyProvider:
        def get_key(self):
            return KEY

    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    configs = [
        ColumnConfig("NOME", normalization_rule=NormalizationRule.PERSON_NAME, output_name=" PESSOA "),
        ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF, output_name="\tDOCUMENTO "),
        ColumnConfig("OBS", action=ColumnAction.EXCLUDE, output_name="DOCUMENTO"),
    ]
    service.create("Lote renomeado", configs)
    profile = service.list_profiles()[0]
    source = tmp_path / "source.csv"
    source.write_text("NOME,CPF,OBS\nJOÃO DA SILVA,123.456.789-00,drop\n", encoding="utf-8")
    individual = tmp_path / "individual.csv"
    anonymize_csv(source, individual, encoding="utf-8", delimiter=",", configurations=configs, secret_key=KEY)
    item = BatchFile(source)
    validate_file(item, profile, service)
    output = tmp_path / "batch"
    output.mkdir()
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    summary = BatchService(service).process([item], profile, output, KeyProvider(), lambda: repository)
    assert summary.completed_files == 1
    assert item.status is BatchFileStatus.COMPLETED
    assert item.output_path.read_bytes() == individual.read_bytes()
    assert read_rows(item.output_path)[0] == ["PESSOA", "DOCUMENTO"]
    assert repository.count() == 1


def test_batch_validation_catches_profile_rename_collision_with_extra_column(tmp_path):
    from data_mask_studio.batch import BatchFile, BatchFileStatus, validate_file

    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create("Nome em conflito", [ColumnConfig("NOME", output_name=" CPF ")])
    source = tmp_path / "extra.csv"
    source.write_text("NOME,CPF\nAna,123\n", encoding="utf-8")
    item = BatchFile(source)
    validate_file(item, profile, service)
    assert item.status is BatchFileStatus.INCOMPATIBLE
    assert "Colunas adicionais não conhecidas pelo perfil: CPF" in item.result_message
    assert "Nome de saída repetido" not in item.result_message


def test_batch_validation_catches_profile_rename_collision_with_known_columns(tmp_path):
    from data_mask_studio.batch import BatchFile, BatchFileStatus, validate_file

    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create("Colunas conhecidas", [ColumnConfig("NOME"), ColumnConfig("CPF")])
    # O serviço já rejeita salvar esse conflito; injeta-o em memória para testar
    # a defesa da validação batch com todas as colunas físicas conhecidas.
    profile = replace(profile, columns=(replace(profile.columns[0], output_name=" CPF "), profile.columns[1]))
    source = tmp_path / "known.csv"
    source.write_text("NOME,CPF\nAna,123\n", encoding="utf-8")
    item = BatchFile(source)
    validate_file(item, profile, service)
    assert item.status is BatchFileStatus.INCOMPATIBLE
    assert "Nome de saída repetido" in item.result_message
    assert "NOME" in item.result_message and "CPF" in item.result_message
    assert "Colunas adicionais" not in item.result_message
