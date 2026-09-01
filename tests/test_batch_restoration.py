import hashlib
from pathlib import Path

import pytest

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.batch_restoration import (
    BatchMissingCodePolicy,
    BatchRestorationFile,
    BatchRestorationFileType,
    BatchRestorationOptions,
    BatchRestorationService,
    BatchRestorationStatus,
    BatchRestorationStructuralError,
    add_files,
    available_output_path,
    discover_files,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultError, VaultRepository

KEY = b"V" * 32
HMAC_KEY = b"H" * 32
ORIGINAL = "123.456.789-00"
CANONICAL = "12345678900"
CODE = TokenGenerator(HMAC_KEY).generate("CPF", CANONICAL)
MISSING = "CPF-ABCDEFGHI234"


def repository(tmp_path: Path) -> VaultRepository:
    result = VaultRepository(tmp_path / "vault.db", VaultCipher(KEY))
    with result.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    CODE,
                    "CPF",
                    ORIGINAL,
                    "CPF",
                    canonical_value=CANONICAL,
                    normalization_rule=NormalizationRule.CPF,
                )
            ]
        )
    return result


def service(tmp_path: Path) -> tuple[BatchRestorationService, VaultRepository]:
    vault = repository(tmp_path)
    return BatchRestorationService(lambda: vault.as_read_only()), vault


def csv_file(path: Path, *values: str) -> Path:
    path.write_text(
        "CPF,Observacao\n" + "\n".join(f"{value},comum" for value in values) + "\n",
        encoding="utf-8",
    )
    return path


def select_candidates(item: BatchRestorationFile) -> None:
    for column in item.columns:
        column.selected = column.is_candidate


def test_add_multiple_files_prevents_duplicates_and_preserves_order(tmp_path: Path) -> None:
    first = csv_file(tmp_path / "first.csv", CODE)
    second = (tmp_path / "second.html")
    second.write_text(f"<p>{CODE}</p>", encoding="utf-8")
    files: list[BatchRestorationFile] = []

    added = add_files(files, [first, second, first, first.resolve()])

    assert added == 2
    assert [item.path for item in files] == [first.absolute(), second.absolute()]


def test_folder_discovery_is_not_recursive(tmp_path: Path) -> None:
    direct_csv = csv_file(tmp_path / "b.csv", CODE)
    direct_html = tmp_path / "a.html"
    direct_html.write_text("<p></p>", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    csv_file(nested / "hidden.csv", CODE)
    (tmp_path / "ignored.txt").write_text("text", encoding="utf-8")

    assert discover_files(tmp_path) == [direct_html.absolute(), direct_csv.absolute()]


def test_analyzes_csv_and_html_and_exposes_csv_candidates(tmp_path: Path) -> None:
    restoration, _ = service(tmp_path)
    csv_path = csv_file(tmp_path / "data.csv", CODE, MISSING)
    html_path = tmp_path / "dashboard.html"
    html_path.write_text(f'<div data-code="{CODE}">{MISSING}</div>', encoding="utf-8")
    files: list[BatchRestorationFile] = []
    add_files(files, [csv_path, html_path])

    restoration.analyze_files(files)

    assert files[0].encoding == "utf-8"
    assert files[0].columns[0].is_candidate
    assert files[0].codes_in_vault == 1
    assert files[0].missing_codes == 1
    assert files[0].status is BatchRestorationStatus.REVIEW_REQUIRED
    assert files[1].codes_found == 2
    assert files[1].codes_in_vault == 1
    assert files[1].missing_codes == 1


def test_batch_restoration_reports_resolved_empty_header(tmp_path: Path) -> None:
    restoration, _ = service(tmp_path)
    source = tmp_path / "empty-header.csv"
    source.write_text(f",Observacao\n{CODE},comum\n", encoding="utf-8")
    files: list[BatchRestorationFile] = []
    add_files(files, [source])

    restoration.analyze_files(files)

    assert files[0].headers == ("column_1", "Observacao")
    assert "1 cabeçalho vazio foi substituído" in files[0].result_message
    assert "coluna 1 → column_1" in files[0].result_message


@pytest.mark.parametrize(
    ("representation", "expected"),
    [
        (RepresentationPolicy.FIRST_ORIGINAL, ORIGINAL),
        (RepresentationPolicy.CANONICAL, CANONICAL),
    ],
)
def test_csv_selection_and_representation(
    tmp_path: Path, representation: RepresentationPolicy, expected: str
) -> None:
    restoration, _ = service(tmp_path)
    source = csv_file(tmp_path / "data.csv", CODE)
    files: list[BatchRestorationFile] = []
    add_files(files, [source])
    restoration.analyze_files(files)
    files[0].columns[0].selected = True
    output = tmp_path / "output"
    output.mkdir()

    summary = restoration.restore_files(
        files, output, BatchRestorationOptions(representation_policy=representation)
    )

    content = files[0].output_path.read_text(encoding="utf-8-sig")
    assert expected in content
    assert "comum" in content
    assert files[0].output_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert summary.completed_files == 1


def test_unselected_csv_column_is_preserved(tmp_path: Path) -> None:
    restoration, _ = service(tmp_path)
    source = tmp_path / "data.csv"
    source.write_text(f"Primeiro,Segundo\n{CODE},{CODE}\n", encoding="utf-8")
    files: list[BatchRestorationFile] = []
    add_files(files, [source])
    restoration.analyze_files(files)
    files[0].columns[0].selected = True
    output = tmp_path / "output"
    output.mkdir()

    restoration.restore_files(files, output, BatchRestorationOptions())

    row = files[0].output_path.read_text(encoding="utf-8-sig").splitlines()[1]
    assert row == f"{ORIGINAL},{CODE}"


def test_html_restoration_preserves_markup_and_replaces_all_contexts(tmp_path: Path) -> None:
    restoration, _ = service(tmp_path)
    source = tmp_path / "dashboard.html"
    source.write_text(
        f'<div data-code="{CODE}">{CODE}</div><script>const x="{CODE}";</script>',
        encoding="utf-8",
    )
    files: list[BatchRestorationFile] = []
    add_files(files, [source])
    restoration.analyze_files(files)
    output = tmp_path / "output"
    output.mkdir()

    restoration.restore_files(files, output, BatchRestorationOptions())

    content = files[0].output_path.read_text(encoding="utf-8")
    assert content == (
        f'<div data-code="{ORIGINAL}">{ORIGINAL}</div>'
        f'<script>const x="{ORIGINAL}";</script>'
    )


def _two_csv_batch(tmp_path: Path) -> tuple[BatchRestorationService, list[BatchRestorationFile], Path]:
    restoration, _ = service(tmp_path)
    files: list[BatchRestorationFile] = []
    add_files(
        files,
        [csv_file(tmp_path / "missing.csv", MISSING), csv_file(tmp_path / "known.csv", CODE)],
    )
    restoration.analyze_files(files)
    for item in files:
        for column in item.columns:
            column.selected = column.valid_codes > 0
    output = tmp_path / "output"
    output.mkdir()
    return restoration, files, output


def test_missing_keep_policy_preserves_code_and_continues(tmp_path: Path) -> None:
    restoration, files, output = _two_csv_batch(tmp_path)

    summary = restoration.restore_files(files, output, BatchRestorationOptions())

    assert summary.completed_files == 2
    assert MISSING in files[0].output_path.read_text(encoding="utf-8-sig")


def test_missing_abort_file_policy_isolates_failure(tmp_path: Path) -> None:
    restoration, files, output = _two_csv_batch(tmp_path)

    summary = restoration.restore_files(
        files,
        output,
        BatchRestorationOptions(missing_code_policy=BatchMissingCodePolicy.ABORT_FILE),
    )

    assert files[0].status is BatchRestorationStatus.ERROR
    assert files[0].output_path is None
    assert files[1].status is BatchRestorationStatus.COMPLETED
    assert summary.completed_files == 1


def test_missing_abort_batch_policy_skips_remaining_files(tmp_path: Path) -> None:
    restoration, files, output = _two_csv_batch(tmp_path)

    restoration.restore_files(
        files,
        output,
        BatchRestorationOptions(missing_code_policy=BatchMissingCodePolicy.ABORT_BATCH),
    )

    assert files[0].status is BatchRestorationStatus.ERROR
    assert files[1].status is BatchRestorationStatus.SKIPPED
    assert not list(output.iterdir())


def test_output_naming_uses_numeric_suffix_and_never_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "arquivo_anonimizado.csv"
    source.write_text("x", encoding="utf-8")
    (tmp_path / "arquivo_restaurado.csv").write_text("existing", encoding="utf-8")
    (tmp_path / "arquivo_restaurado_2.csv").write_text("existing", encoding="utf-8")

    assert available_output_path(source, tmp_path).name == "arquivo_restaurado_3.csv"


def test_processing_is_sequential_and_cancel_preserves_completed_file(tmp_path: Path) -> None:
    restoration, _ = service(tmp_path)
    files: list[BatchRestorationFile] = []
    add_files(files, [csv_file(tmp_path / "one.csv", CODE), csv_file(tmp_path / "two.csv", CODE)])
    restoration.analyze_files(files)
    for item in files:
        select_candidates(item)
    output = tmp_path / "output"
    output.mkdir()
    cancel = False
    order: list[str] = []

    def changed(item: BatchRestorationFile) -> None:
        nonlocal cancel
        if item.status is BatchRestorationStatus.COMPLETED:
            order.append(item.path.name)
            cancel = True

    summary = restoration.restore_files(
        files,
        output,
        BatchRestorationOptions(),
        file_callback=changed,
        should_cancel=lambda: cancel,
    )

    assert order == ["one.csv"]
    assert files[0].output_path.is_file()
    assert files[1].status is BatchRestorationStatus.CANCELLED
    assert summary.cancelled
    assert not list(output.glob("*.tmp"))


def test_regular_file_failure_does_not_prevent_next_file(tmp_path: Path) -> None:
    restoration, _ = service(tmp_path)
    files: list[BatchRestorationFile] = []
    first = csv_file(tmp_path / "first.csv", CODE)
    second = csv_file(tmp_path / "second.csv", CODE)
    add_files(files, [first, second])
    restoration.analyze_files(files)
    for item in files:
        select_candidates(item)
    first.unlink()
    output = tmp_path / "output"
    output.mkdir()

    summary = restoration.restore_files(files, output, BatchRestorationOptions())

    assert files[0].status is BatchRestorationStatus.ERROR
    assert files[1].status is BatchRestorationStatus.COMPLETED
    assert summary.completed_files == 1
    assert summary.error_files == 1


def test_structural_failure_preserves_previously_completed_output(tmp_path: Path) -> None:
    analyzer, vault = service(tmp_path)
    files: list[BatchRestorationFile] = []
    add_files(
        files,
        [csv_file(tmp_path / "first.csv", CODE), csv_file(tmp_path / "second.csv", CODE)],
    )
    analyzer.analyze_files(files)
    for item in files:
        select_candidates(item)
    calls = 0

    def repository_factory():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise VaultError("unavailable")
        return vault.as_read_only()

    restoration = BatchRestorationService(repository_factory)
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(BatchRestorationStructuralError):
        restoration.restore_files(files, output, BatchRestorationOptions())

    assert files[0].status is BatchRestorationStatus.COMPLETED
    assert files[0].output_path.is_file()
    assert files[1].status is BatchRestorationStatus.ERROR


def test_vault_remains_byte_for_byte_read_only(tmp_path: Path) -> None:
    restoration, vault = service(tmp_path)
    source = csv_file(tmp_path / "data.csv", CODE)
    files: list[BatchRestorationFile] = []
    add_files(files, [source])
    before = hashlib.sha256(vault.database_path.read_bytes()).digest()
    record_before = vault.get_record(CODE)
    restoration.analyze_files(files)
    select_candidates(files[0])
    output = tmp_path / "output"
    output.mkdir()

    restoration.restore_files(files, output, BatchRestorationOptions())

    assert hashlib.sha256(vault.database_path.read_bytes()).digest() == before
    assert vault.get_record(CODE) == record_before


def test_structural_failure_stops_batch_without_exposing_sensitive_value(tmp_path: Path) -> None:
    source = csv_file(tmp_path / "data.csv", CODE)
    files: list[BatchRestorationFile] = []
    add_files(files, [source])

    def unavailable():
        raise VaultError(f"failure with {ORIGINAL}")

    restoration = BatchRestorationService(unavailable)

    with pytest.raises(BatchRestorationStructuralError) as captured:
        restoration.analyze_files(files)

    assert ORIGINAL not in str(captured.value)
