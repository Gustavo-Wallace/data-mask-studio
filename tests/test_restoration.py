import codecs
import csv
import sqlite3
from pathlib import Path

import pytest

from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.restoration import (
    CellClassification,
    MissingCodeError,
    MissingCodePolicy,
    RepresentationPolicy,
    RestorationCancelled,
    RestorationConfiguration,
    RestorationError,
    RestorationSecurityError,
    RestorationService,
    SelectedColumn,
    classify_cell_format,
    restore_csv,
    suggested_output_path,
)
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

KEY = b"R" * 32
CPF_CODE = "CPF-ABCDEFGHI234"
NAME_CODE = "NOME-BCDEFGHI234A"
MISSING_CODE = "CPF-CDEFGHI234AB"


def make_repository(tmp_path: Path) -> VaultRepository:
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(KEY))
    cpf = MappingCandidate(
        CPF_CODE,
        "CPF",
        "123.456.789-00",
        "CPF",
        canonical_value="12345678900",
        normalization_rule=NormalizationRule.CPF,
    )
    cpf.add_variation("12345678900", NormalizationRule.CPF)
    name = MappingCandidate(NAME_CODE, "NOME", "João da Silva", "Nome")
    with repository.transaction() as transaction:
        transaction.upsert_batch([cpf, name])
    return repository


def configuration(
    source: Path,
    *,
    encoding: str = "utf-8",
    delimiter: str = ";",
    headers: tuple[str, ...] = ("CPF", "Nome", "Tipo"),
    indexes: tuple[int, ...] = (0,),
    missing_policy: MissingCodePolicy = MissingCodePolicy.KEEP,
    representation: RepresentationPolicy = RepresentationPolicy.FIRST_ORIGINAL,
) -> RestorationConfiguration:
    return RestorationConfiguration(
        source,
        encoding,
        delimiter,
        headers,
        tuple(SelectedColumn(index, headers[index]) for index in indexes),
        missing_policy,
        representation,
    )


def read_rows(path: Path, delimiter: str = ";") -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.reader(file, delimiter=delimiter))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (CPF_CODE, CellClassification.NOT_FOUND),
        (CPF_CODE.lower(), CellClassification.NOT_FOUND),
        ("CPF-curto", CellClassification.INVALID_CODE_LIKE),
        ("WEB", CellClassification.COMMON),
        ("  ", CellClassification.EMPTY),
    ],
)
def test_cell_format_classification(value: str, expected: CellClassification) -> None:
    assert classify_cell_format(value).classification is expected


def test_analysis_classifies_selected_cells_and_does_not_modify_vault(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "analysis.csv"
    source.write_text(
        "CPF;Nome;Tipo\n"
        f"{CPF_CODE.lower()};{NAME_CODE};WEB\n"
        f"{MISSING_CODE};NOME-curto;MOB\n"
        "  ;Brasília;Ativo\n",
        encoding="utf-8",
    )
    before = repository.database_path.read_bytes()
    service = RestorationService(lambda: repository)

    result = service.analyze(configuration(source, indexes=(0, 1)))

    assert result.rows_processed == 3
    assert result.cells_analyzed == 6
    assert result.valid_codes == 3
    assert result.found_codes == 2
    assert result.missing_codes == 1
    assert result.invalid_formats == 1
    assert result.empty_cells == 1
    assert result.common_values == 1
    assert result.prefixes == ("CPF", "NOME")
    assert repository.database_path.read_bytes() == before


def test_restores_selected_columns_preserving_structure_and_source(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "relatorio_anonimizado.csv"
    original = (
        "CPF;Nome;Tipo\n"
        f"{CPF_CODE};{NAME_CODE};WEB\n"
        f"{CPF_CODE};Comum;MOB\n"
    ).encode("utf-8")
    source.write_bytes(original)
    vault_before = repository.database_path.read_bytes()
    destination = tmp_path / "relatorio_restaurado.csv"
    progress = []

    result = restore_csv(
        configuration(source, indexes=(0, 1)),
        destination,
        repository,
        progress_callback=progress.append,
    )

    assert source.read_bytes() == original
    assert repository.database_path.read_bytes() == vault_before
    assert destination.read_bytes().startswith(codecs.BOM_UTF8)
    assert read_rows(destination) == [
        ["CPF", "Nome", "Tipo"],
        ["123.456.789-00", "João da Silva", "WEB"],
        ["123.456.789-00", "Comum", "MOB"],
    ]
    assert result.rows_processed == 2
    assert result.restored_codes == 3
    assert result.preserved_common_values == 1
    assert [item.rows_processed for item in progress] == [1, 2]


def test_restoration_resolves_empty_header_and_writes_synthetic_name(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "empty-header-anonymized.csv"
    original = f";Tipo\n{NAME_CODE};WEB\n".encode()
    source.write_bytes(original)
    destination = tmp_path / "restored.csv"

    restore_csv(
        configuration(
            source,
            headers=("column_1", "Tipo"),
            indexes=(0,),
        ),
        destination,
        repository,
    )

    assert read_rows(destination) == [
        ["column_1", "Tipo"],
        ["João da Silva", "WEB"],
    ]
    assert source.read_bytes() == original


def test_unselected_column_is_preserved_even_when_it_contains_a_code(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(
        f"CPF;Nome;Tipo\n{CPF_CODE};{NAME_CODE};WEB\n", encoding="utf-8"
    )
    destination = tmp_path / "output.csv"

    restore_csv(configuration(source, indexes=(0,)), destination, repository)

    assert read_rows(destination)[1] == [
        "123.456.789-00",
        NAME_CODE,
        "WEB",
    ]


def test_canonical_representation_and_first_historical_variation(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Nome;Tipo\n{CPF_CODE};;\n", encoding="utf-8")
    first = tmp_path / "first.csv"
    canonical = tmp_path / "canonical.csv"

    restore_csv(configuration(source), first, repository)
    restore_csv(
        configuration(source, representation=RepresentationPolicy.CANONICAL),
        canonical,
        repository,
    )

    assert read_rows(first)[1][0] == "123.456.789-00"
    assert read_rows(canonical)[1][0] == "12345678900"


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (MissingCodePolicy.KEEP, MISSING_CODE),
        (MissingCodePolicy.EMPTY, ""),
    ],
)
def test_missing_code_non_aborting_policies(
    tmp_path: Path, policy: MissingCodePolicy, expected: str
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Nome;Tipo\n{MISSING_CODE};;\n", encoding="utf-8")
    destination = tmp_path / f"{policy.value}.csv"

    result = restore_csv(
        configuration(source, missing_policy=policy), destination, repository
    )

    assert read_rows(destination)[1][0] == expected
    assert result.missing_codes == 1


def test_abort_policy_removes_temporary_and_does_not_publish(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Nome;Tipo\n{MISSING_CODE};;\n", encoding="utf-8")
    destination = tmp_path / "output.csv"

    with pytest.raises(MissingCodeError) as raised:
        restore_csv(
            configuration(source, missing_policy=MissingCodePolicy.ABORT),
            destination,
            repository,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".output.csv.*.tmp"))
    assert MISSING_CODE not in str(raised.value)


def test_cooperative_cancellation_removes_temporary_file(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(
        "CPF;Nome;Tipo\n" + f"{CPF_CODE};;\n" * 3, encoding="utf-8"
    )
    destination = tmp_path / "output.csv"

    with pytest.raises(RestorationCancelled):
        restore_csv(
            configuration(source),
            destination,
            repository,
            should_cancel=lambda: True,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".output.csv.*.tmp"))


def test_wrong_key_and_tampering_fail_without_sensitive_values(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Nome;Tipo\n{CPF_CODE};;\n", encoding="utf-8")
    wrong_key_repository = VaultRepository(
        repository.database_path, VaultCipher(b"W" * 32), read_only=True
    )
    destination = tmp_path / "wrong-key.csv"

    with pytest.raises(RestorationSecurityError) as wrong_key_error:
        restore_csv(configuration(source), destination, wrong_key_repository)
    assert "123.456.789-00" not in str(wrong_key_error.value)
    assert not destination.exists()

    with sqlite3.connect(repository.database_path) as connection:
        encrypted = connection.execute(
            "SELECT encrypted_value FROM vault_variations WHERE code = ?",
            (CPF_CODE,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE vault_variations SET encrypted_value = ? WHERE code = ?",
            (bytes([encrypted[0] ^ 1]) + encrypted[1:], CPF_CODE),
        )
    with pytest.raises(RestorationSecurityError) as tampered_error:
        restore_csv(configuration(source), tmp_path / "tampered.csv", repository)
    assert "123.456.789-00" not in str(tampered_error.value)


@pytest.mark.parametrize(
    ("encoding", "payload"),
    [
        ("utf-8-sig", codecs.BOM_UTF8 + f"CPF;Nome;Tipo\n{CPF_CODE};;\n".encode()),
        ("windows-1252", f"CPF;Nome;Tipo\n{CPF_CODE};João;WEB\n".encode("cp1252")),
    ],
)
def test_supported_input_encodings_produce_utf8_bom(
    tmp_path: Path, encoding: str, payload: bytes
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / f"{encoding}.csv"
    source.write_bytes(payload)
    destination = tmp_path / f"{encoding}-output.csv"

    restore_csv(configuration(source, encoding=encoding), destination, repository)

    assert destination.read_bytes().startswith(codecs.BOM_UTF8)
    assert read_rows(destination)[0] == ["CPF", "Nome", "Tipo"]


def test_validation_blocks_input_path_existing_destination_and_empty_selection(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Nome;Tipo\n{CPF_CODE};;\n", encoding="utf-8")
    empty = configuration(source, indexes=())
    with pytest.raises(RestorationError):
        restore_csv(empty, tmp_path / "output.csv", repository)
    with pytest.raises(RestorationError):
        restore_csv(configuration(source), source, repository)
    existing = tmp_path / "existing.csv"
    existing.write_text("keep", encoding="utf-8")
    with pytest.raises(RestorationError):
        restore_csv(configuration(source), existing, repository)
    assert existing.read_text(encoding="utf-8") == "keep"


def test_output_name_suggestion() -> None:
    assert suggested_output_path("relatorio_anonimizado.csv").name == (
        "relatorio_restaurado.csv"
    )
    assert suggested_output_path("arquivo.csv").name == "arquivo_restaurado.csv"


def test_successful_write_uses_a_temporary_file(tmp_path: Path, monkeypatch) -> None:
    import data_mask_studio.restoration.csv_restorer as restorer_module

    repository = make_repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Nome;Tipo\n{CPF_CODE};;\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    original_factory = restorer_module.tempfile.NamedTemporaryFile
    temporary_names: list[str] = []

    def recording_factory(*args, **kwargs):
        temporary_file = original_factory(*args, **kwargs)
        temporary_names.append(temporary_file.name)
        return temporary_file

    monkeypatch.setattr(
        restorer_module.tempfile, "NamedTemporaryFile", recording_factory
    )

    restore_csv(configuration(source), destination, repository)

    assert len(temporary_names) == 1
    assert Path(temporary_names[0]).parent == destination.parent
    assert Path(temporary_names[0]).suffix == ".tmp"
    assert not Path(temporary_names[0]).exists()


def test_readme_mentions_csv_restoration_compactly() -> None:
    english = Path("README.md").read_text(encoding="utf-8")
    portuguese = Path("README.pt-BR.md").read_text(encoding="utf-8")

    assert "selectively restore masked CSV files" in english
    assert "“Restore CSV” tab" in english
    assert "permite restaurar seletivamente CSVs mascarados" in portuguese
    assert "aba “Restaurar CSV”" in portuguese
    assert "## Roadmap" not in english
    assert "## Roadmap" not in portuguese
