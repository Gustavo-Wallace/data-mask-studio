import codecs
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.batch import (
    BatchError,
    BatchFile,
    BatchFileStatus,
    BatchService,
    CancellationRequest,
    add_files,
    discover_csv_files,
    invalidate_files,
    reserve_output_path,
    suggested_output_name,
    validate_file,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultEncryptionError, VaultRepository


class FixedKeyProvider:
    def get_key(self) -> bytes:
        return b"B" * 32


class CountingRepository(VaultRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path, VaultCipher(b"C" * 32))
        self.transaction_count = 0

    def transaction(self):
        self.transaction_count += 1
        return super().transaction()


class FailingRepository(CountingRepository):
    def transaction(self):
        self.transaction_count += 1
        if self.transaction_count == 2:
            raise VaultEncryptionError("O cofre não pode ser autenticado.")
        return VaultRepository.transaction(self)


def make_profile_service(tmp_path: Path) -> tuple[ProfileService, object]:
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create(
        "Perfil do lote",
        [
            ColumnConfig("Nome", True, "NOME", NormalizationRule.EXACT),
            ColumnConfig("CPF", True, "CPF_ID", NormalizationRule.CPF),
        ],
    )
    return service, profile


def write_csv(path: Path, rows: str = "Ana,12345678900,x\n") -> None:
    path.write_text(f"Nome,CPF,Extra\n{rows}", encoding="utf-8")


def validate_all(
    files: list[BatchFile], service: ProfileService, profile: object
) -> None:
    for item in files:
        validate_file(item, profile, service)


def test_add_files_prevents_duplicates_and_preserves_order(tmp_path: Path) -> None:
    first = tmp_path / "b.csv"
    second = tmp_path / "a.csv"
    files: list[BatchFile] = []

    added = add_files(files, [first, second, first, tmp_path / "notes.txt"])

    assert added == 2
    assert [item.path for item in files] == [first.absolute(), second.absolute()]


def test_folder_discovery_is_sorted_and_not_recursive(tmp_path: Path) -> None:
    (tmp_path / "z.csv").touch()
    (tmp_path / "A.CSV").touch()
    (tmp_path / "notes.txt").touch()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "hidden.csv").touch()

    discovered = discover_csv_files(tmp_path)

    assert [item.name for item in discovered] == ["A.CSV", "z.csv"]
    assert discover_csv_files(nested) == [nested / "hidden.csv"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert discover_csv_files(empty) == []


def test_validation_requires_all_exact_headers_and_allows_extra_columns(
    tmp_path: Path,
) -> None:
    service, profile = make_profile_service(tmp_path)
    compatible_path = tmp_path / "compatible.csv"
    write_csv(compatible_path)
    missing_path = tmp_path / "missing.csv"
    missing_path.write_text("Nome,Extra\nAna,x\n", encoding="utf-8")
    approximate_path = tmp_path / "approximate.csv"
    approximate_path.write_text("nome,Cpf,Extra\nAna,123,x\n", encoding="utf-8")
    accent_path = tmp_path / "accent.csv"
    accent_path.write_text("Nóme,CPF,Extra\nAna,123,x\n", encoding="utf-8")
    items = [
        BatchFile(compatible_path),
        BatchFile(missing_path),
        BatchFile(approximate_path),
        BatchFile(accent_path),
    ]

    validate_all(items, service, profile)

    assert items[0].status is BatchFileStatus.COMPATIBLE
    assert items[0].column_count == 3
    assert items[1].missing_headers == ("CPF",)
    assert items[2].missing_headers == ("Nome", "CPF")
    assert items[3].missing_headers == ("Nome",)
    assert all(item.status is BatchFileStatus.INCOMPATIBLE for item in items[1:])


def test_batch_validation_uses_synthetic_header_and_reports_warning(
    tmp_path: Path,
) -> None:
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create(
        "Layout recuperado",
        [ColumnConfig("column_1", True, "COLUNA_1")],
    )
    path = tmp_path / "empty-header.csv"
    path.write_text(",CPF\nAna,123\n", encoding="utf-8")
    item = BatchFile(path)

    validate_file(item, profile, service)

    assert item.status is BatchFileStatus.COMPATIBLE
    assert item.headers == ("column_1", "CPF")
    assert "1 cabeçalho vazio foi substituído" in item.result_message
    assert "coluna 1 → column_1" in item.result_message


def test_profile_change_and_new_file_require_validation_again(tmp_path: Path) -> None:
    service, profile = make_profile_service(tmp_path)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_csv(first)
    write_csv(second)
    files = [BatchFile(first)]
    validate_file(files[0], profile, service)
    assert files[0].status is BatchFileStatus.COMPATIBLE

    add_files(files, [second])
    assert files[1].status is BatchFileStatus.PENDING

    invalidate_files(files)
    assert all(item.status is BatchFileStatus.PENDING for item in files)


def test_output_name_and_numeric_conflict_reservation(tmp_path: Path) -> None:
    source = tmp_path / "relatorio.csv"
    source.touch()
    (tmp_path / "relatorio_anonimizado.csv").touch()

    reserved = reserve_output_path(tmp_path, source)

    assert suggested_output_name(source) == "relatorio_anonimizado.csv"
    assert reserved.name == "relatorio_anonimizado_2.csv"
    assert reserved.exists()


def test_batch_processes_sequentially_with_one_transaction_per_file(
    tmp_path: Path,
) -> None:
    service, profile = make_profile_service(tmp_path)
    sources = [tmp_path / "one.csv", tmp_path / "two.csv"]
    for source in sources:
        write_csv(source)
    files = [BatchFile(source) for source in sources]
    validate_all(files, service, profile)
    output = tmp_path / "out"
    output.mkdir()
    repository = CountingRepository(tmp_path / "vault.db")

    summary = BatchService(service).process(
        files,
        profile,
        output,
        FixedKeyProvider(),
        lambda: repository,
    )

    assert repository.transaction_count == 2
    assert [item.status for item in files] == [
        BatchFileStatus.COMPLETED,
        BatchFileStatus.COMPLETED,
    ]
    assert summary.completed_files == 2
    assert summary.records_processed == 2
    assert len(list(output.glob("*.csv"))) == 2


def test_batch_propagates_utf32_encoding_through_validation_and_processing(
    tmp_path: Path,
) -> None:
    service, profile = make_profile_service(tmp_path)
    source = tmp_path / "utf32.csv"
    content = "Nome,CPF,Extra\r\nMárcia,12345678900,Brasília\r\n"
    original = codecs.BOM_UTF32_LE + content.encode("utf-32-le")
    source.write_bytes(original)
    item = BatchFile(source)
    validate_file(item, profile, service)
    output = tmp_path / "out"
    output.mkdir()
    repository = CountingRepository(tmp_path / "vault.db")

    summary = BatchService(service).process(
        [item],
        profile,
        output,
        FixedKeyProvider(),
        lambda: repository,
    )

    generated = output / "utf32_anonimizado.csv"
    assert item.encoding == "utf-32-le"
    assert item.status is BatchFileStatus.COMPLETED
    assert summary.completed_files == 1
    assert generated.read_bytes().startswith(codecs.BOM_UTF8)
    assert source.read_bytes() == original


def test_batch_uses_fallback_for_invalid_value_and_continues(tmp_path: Path) -> None:
    service, profile = make_profile_service(tmp_path)
    first = tmp_path / "first.csv"
    invalid = tmp_path / "invalid.csv"
    third = tmp_path / "third.csv"
    write_csv(first, "Ana,12345678900,x\n")
    write_csv(invalid, "Sensitive Sentinel,invalid-cpf,x\n")
    write_csv(third, "Bia,98765432100,x\n")
    files = [BatchFile(path) for path in (first, invalid, third)]
    validate_all(files, service, profile)
    output = tmp_path / "out"
    output.mkdir()
    repository = CountingRepository(tmp_path / "vault.db")

    summary = BatchService(service).process(
        files, profile, output, FixedKeyProvider(), lambda: repository
    )

    assert [item.status for item in files] == [
        BatchFileStatus.COMPLETED,
        BatchFileStatus.COMPLETED,
        BatchFileStatus.COMPLETED,
    ]
    assert summary.completed_files == 3
    assert summary.error_files == 0
    assert "Sensitive Sentinel" not in files[1].result_message
    assert (output / "invalid_anonimizado.csv").exists()
    assert [(item.header, item.count) for item in summary.normalization_fallbacks] == [
        ("CPF", 1)
    ]
    assert "CPF: 1" in files[1].result_message
    assert repository.count() == 6


def test_removed_file_after_validation_does_not_stop_following_file(
    tmp_path: Path,
) -> None:
    service, profile = make_profile_service(tmp_path)
    removed = tmp_path / "removed.csv"
    valid = tmp_path / "valid.csv"
    write_csv(removed)
    write_csv(valid)
    files = [BatchFile(removed), BatchFile(valid)]
    validate_all(files, service, profile)
    removed.unlink()
    output = tmp_path / "out"
    output.mkdir()
    repository = CountingRepository(tmp_path / "vault.db")

    summary = BatchService(service).process(
        files, profile, output, FixedKeyProvider(), lambda: repository
    )

    assert files[0].status is BatchFileStatus.ERROR
    assert files[1].status is BatchFileStatus.COMPLETED
    assert summary.completed_files == 1


def test_structural_vault_error_stops_remaining_files(tmp_path: Path) -> None:
    service, profile = make_profile_service(tmp_path)
    sources = [tmp_path / f"file-{index}.csv" for index in range(3)]
    for source in sources:
        write_csv(source)
    files = [BatchFile(source) for source in sources]
    validate_all(files, service, profile)
    output = tmp_path / "out"
    output.mkdir()
    repository = FailingRepository(tmp_path / "vault.db")

    summary = BatchService(service).process(
        files, profile, output, FixedKeyProvider(), lambda: repository
    )

    assert [item.status for item in files] == [
        BatchFileStatus.COMPLETED,
        BatchFileStatus.ERROR,
        BatchFileStatus.SKIPPED,
    ]
    assert summary.completed_files == 1
    assert summary.cancelled_or_skipped_files == 1
    assert len(list(output.glob("*.csv"))) == 1


def test_cancellation_rolls_back_current_file_and_skips_following_files(
    tmp_path: Path,
) -> None:
    service, profile = make_profile_service(tmp_path)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_csv(first, "Ana,12345678900,x\nBia,98765432100,x\n")
    write_csv(second)
    files = [BatchFile(first), BatchFile(second)]
    validate_all(files, service, profile)
    output = tmp_path / "out"
    output.mkdir()
    repository = CountingRepository(tmp_path / "vault.db")
    cancellation = CancellationRequest()

    summary = BatchService(service).process(
        files,
        profile,
        output,
        FixedKeyProvider(),
        lambda: repository,
        cancellation=cancellation,
        progress_callback=lambda _progress: cancellation.request(),
    )

    assert [item.status for item in files] == [
        BatchFileStatus.CANCELLED,
        BatchFileStatus.CANCELLED,
    ]
    assert summary.cancelled_or_skipped_files == 2
    assert repository.count() == 0
    assert list(output.iterdir()) == []


@pytest.mark.parametrize(
    "files,output,message",
    [([], ".", "Adicione"), ([BatchFile(Path("pending.csv"))], ".", "Valide")],
)
def test_batch_cannot_start_without_required_validation(
    tmp_path: Path, files: list[BatchFile], output: str, message: str
) -> None:
    service, profile = make_profile_service(tmp_path)

    with pytest.raises(BatchError, match=message):
        BatchService(service).process(
            files,
            profile,
            tmp_path / output,
            FixedKeyProvider(),
            lambda: CountingRepository(tmp_path / "vault.db"),
        )
