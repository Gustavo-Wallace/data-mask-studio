import json
import os
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import (
    PROFILES_SCHEMA_VERSION,
    ProfileFormatError,
    ProfileRepository,
    ProfileService,
    ProfileStorageError,
    ProfileValidationError,
)


def configurations() -> list[ColumnConfig]:
    return [
        ColumnConfig("Nome", True, "NOME", NormalizationRule.COLLAPSE_WHITESPACE),
        ColumnConfig("CPF", True, "CPF_ID", NormalizationRule.CPF),
        ColumnConfig("Cidade", False, "", NormalizationRule.EXACT),
    ]


def make_service(tmp_path: Path) -> tuple[ProfileService, ProfileRepository]:
    repository = ProfileRepository(tmp_path / "profiles.json")
    return ProfileService(repository), repository


def test_first_profile_is_created_and_persisted_without_csv_data(
    tmp_path: Path,
) -> None:
    service, repository = make_service(tmp_path)

    profile = service.create("  Relatório de Logins  ", configurations())
    document = json.loads(repository.path.read_text(encoding="utf-8"))

    assert profile.name == "Relatório de Logins"
    assert document["schema_version"] == PROFILES_SCHEMA_VERSION
    assert len(document["profiles"]) == 1
    assert [column["header"] for column in document["profiles"][0]["columns"]] == [
        "Nome",
        "CPF",
    ]
    serialized = repository.path.read_text(encoding="utf-8")
    for forbidden in (
        "arquivo.csv",
        "C:\\\\dados",
        "123.456.789-00",
        "encoding",
        "delimiter",
        "token",
        "secret_key",
    ):
        assert forbidden not in serialized


def test_profiles_persist_between_repository_instances(tmp_path: Path) -> None:
    service, repository = make_service(tmp_path)
    created = service.create("Primeiro perfil", configurations())
    second_service = ProfileService(ProfileRepository(repository.path))

    loaded = second_service.list_profiles()

    assert loaded == [created]
    assert loaded[0].columns[0].prefix == "NOME"
    assert loaded[0].columns[1].normalization_rule is NormalizationRule.CPF


def test_person_name_normalization_round_trips_through_profiles(tmp_path: Path) -> None:
    service, repository = make_service(tmp_path)
    person_name_configuration = [
        ColumnConfig("Nome", True, "NOME", NormalizationRule.PERSON_NAME)
    ]

    created = service.create("Nomes de pessoas", person_name_configuration)
    loaded = ProfileRepository(repository.path).load()

    assert loaded == [created]
    assert loaded[0].columns[0].normalization_rule is NormalizationRule.PERSON_NAME


def test_resolved_empty_headers_are_stable_in_profiles(tmp_path: Path) -> None:
    source = tmp_path / "empty-header.csv"
    source.write_text(",CPF\nAna,123\n", encoding="utf-8")
    inspection = inspect_csv(source)
    service, repository = make_service(tmp_path)

    created = service.create(
        "Layout com coluna sintética",
        [ColumnConfig("column_1", True, "COLUNA_1")],
    )
    loaded = ProfileRepository(repository.path).load()[0]
    application = service.apply(loaded, inspection.headers)

    assert created.columns[0].header == "column_1"
    assert application.matched_headers == ("column_1",)
    assert application.is_complete


def test_multiple_profiles_and_case_insensitive_duplicate_names(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    service.create("Relatório de Logins", configurations())
    service.create("Cadastro Geral", configurations())

    with pytest.raises(ProfileValidationError, match="Já existe"):
        service.create("relatório de logins", configurations())

    assert [profile.name for profile in service.list_profiles()] == [
        "Cadastro Geral",
        "Relatório de Logins",
    ]


@pytest.mark.parametrize("name", ["", "   ", "ab", "Perfil\ninválido"])
def test_invalid_short_or_empty_profile_name_is_rejected(
    tmp_path: Path, name: str
) -> None:
    service, repository = make_service(tmp_path)

    with pytest.raises(ProfileValidationError):
        service.create(name, configurations())

    assert not repository.path.exists()


def test_profile_application_uses_only_exact_header_matches(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    profile = service.create("Dados pessoais", configurations())

    complete = service.apply(profile, ["Nome", "CPF", "Extra"])
    partial = service.apply(profile, ["Nome", "Cidade"])
    none = service.apply(profile, ["nome", "C P F", "Nóme"])

    assert complete.is_complete
    assert complete.matched_headers == ("Nome", "CPF")
    assert [column.anonymize for column in complete.configurations] == [True, True, False]
    assert complete.configurations[0].normalization_rule is NormalizationRule.COLLAPSE_WHITESPACE
    assert partial.matched_headers == ("Nome",)
    assert partial.missing_headers == ("CPF",)
    assert not partial.is_complete
    assert not none.has_matches


def test_update_preserves_identifier_and_creation_date(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    created = service.create("Perfil original", configurations())
    replacement = [ColumnConfig("E-mail", True, "EMAIL", NormalizationRule.EMAIL)]

    updated = service.update(created.identifier, replacement)

    assert updated.identifier == created.identifier
    assert updated.created_at == created.created_at
    assert updated.modified_at >= created.modified_at
    assert [column.header for column in updated.columns] == ["E-mail"]


def test_rename_and_delete_preserve_other_profiles(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    first = service.create("Primeiro perfil", configurations())
    second = service.create("Segundo perfil", configurations())

    renamed = service.rename(first.identifier, "Perfil renomeado")
    service.delete(renamed.identifier)

    assert renamed.identifier == first.identifier
    assert renamed.columns == first.columns
    assert service.list_profiles() == [second]


def test_repository_uses_atomic_replace_in_the_same_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository = make_service(tmp_path)
    replacements: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def record_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr(
        "data_mask_studio.profiles.repository.os.replace", record_replace
    )

    service.create("Perfil atômico", configurations())

    assert len(replacements) == 1
    temporary, destination = replacements[0]
    assert temporary.parent == repository.path.parent
    assert destination == repository.path
    assert not temporary.exists()


def test_failed_atomic_replace_preserves_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository = make_service(tmp_path)
    profile = service.create("Perfil anterior", configurations())
    previous = repository.path.read_bytes()

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr(
        "data_mask_studio.profiles.repository.os.replace", fail_replace
    )

    with pytest.raises(ProfileStorageError):
        service.rename(profile.identifier, "Novo nome")

    assert repository.path.read_bytes() == previous
    assert list(tmp_path.glob("*.tmp")) == []


def test_missing_file_returns_empty_list_without_creating_it(tmp_path: Path) -> None:
    repository = ProfileRepository(tmp_path / "profiles.json")

    assert repository.load() == []
    assert not repository.path.exists()


@pytest.mark.parametrize(
    "document",
    [
        "not-json",
        json.dumps({"schema_version": 999, "profiles": []}),
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "identifier": "00000000-0000-0000-0000-000000000001",
                        "name": "Perfil inválido",
                        "format_version": 1,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "modified_at": "2026-01-01T00:00:00+00:00",
                        "columns": [
                            {
                                "header": "CPF",
                                "prefix": "invalid-prefix",
                                "normalization_rule": "cpf",
                                "anonymize": True,
                            }
                        ],
                    }
                ],
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "identifier": "00000000-0000-0000-0000-000000000001",
                        "name": "Perfil inválido",
                        "format_version": 1,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "modified_at": "2026-01-01T00:00:00+00:00",
                        "columns": [
                            {
                                "header": "CPF",
                                "prefix": "CPF_ID",
                                "normalization_rule": "unknown",
                                "anonymize": True,
                            }
                        ],
                    }
                ],
            }
        ),
    ],
)
def test_invalid_documents_are_rejected_without_being_modified(
    tmp_path: Path, document: str
) -> None:
    repository = ProfileRepository(tmp_path / "profiles.json")
    repository.path.write_text(document, encoding="utf-8")

    with pytest.raises(ProfileFormatError):
        repository.load()

    assert repository.path.read_text(encoding="utf-8") == document
