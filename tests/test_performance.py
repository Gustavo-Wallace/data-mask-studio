import errno
import csv
import tracemalloc
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.csv_tools import anonymize_csv
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.performance import (
    BoundedCache,
    PerformanceSettings,
    ProgressLimiter,
    RestorationMetrics,
    calculate_metrics,
)
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from data_mask_studio.restoration import (
    RestorationCancelled,
    RestorationConfiguration,
    SelectedColumn,
    restore_csv,
)


def test_bounded_cache_never_exceeds_limit_and_can_be_cleared() -> None:
    cache = BoundedCache[str, object](3)
    for index in range(20):
        cache[f"CODE-{index}"] = object()

    assert len(cache) == 3
    assert list(cache) == ["CODE-17", "CODE-18", "CODE-19"]
    cache.clear()
    assert len(cache) == 0


def test_progress_limiter_does_not_emit_for_every_row() -> None:
    settings = PerformanceSettings(
        progress_row_interval=10,
        progress_time_interval=0,
    )
    limiter = ProgressLimiter(settings)
    emissions = [row for row in range(1, 101) if limiter.should_emit(row)]

    assert emissions == list(range(10, 101, 10))


def test_metrics_calculate_rate_and_reliable_estimate() -> None:
    metrics = calculate_metrics(500, 2.0, total_rows=2_000)
    unreliable = calculate_metrics(50, 0.5, total_rows=2_000)

    assert metrics.rows_per_second == 250
    assert metrics.estimated_remaining_seconds == 6
    assert unreliable.estimated_remaining_seconds is None


def test_vault_batch_prefetches_existing_codes_in_one_query(
    tmp_path, monkeypatch
) -> None:
    import data_mask_studio.vault.repository as repository_module

    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    initial = [
        MappingCandidate(f"ID-CODE{index:08d}", "ID", str(index), "ID")
        for index in range(3)
    ]
    with repository.transaction() as transaction:
        transaction.upsert_batch(initial)

    selects = 0
    original_connect = repository_module.connect

    def counted_connect(path):
        connection = original_connect(path)

        def trace(statement: str) -> None:
            nonlocal selects
            normalized = " ".join(statement.upper().split())
            if "FROM VAULT_MAPPINGS WHERE CODE IN" in normalized:
                selects += 1

        connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(repository_module, "connect", counted_connect)
    with repository.transaction() as transaction:
        transaction.upsert_batch(initial)

    assert selects == 1
    assert repository.count() == 3


def test_high_repetition_inside_configurable_batch_is_aggregated(tmp_path) -> None:
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    repeated = [MappingCandidate("ID-ABCDEFGHI234", "ID", "valor", "ID") for _ in range(50)]

    with repository.transaction() as transaction:
        transaction.upsert_batch(repeated)

    record = repository.get_record("ID-ABCDEFGHI234")
    assert repository.count() == 1
    assert record is not None
    assert record.occurrence_count == 50


def test_safe_io_messages_do_not_include_sensitive_values() -> None:
    from data_mask_studio.csv_tools.csv_anonymizer import _safe_io_message
    from data_mask_studio.restoration.csv_restorer import _safe_io_message as restore_message

    sensitive = "CPF-123.456.789-00"
    no_space = OSError(errno.ENOSPC, sensitive)
    blocked = PermissionError(errno.EACCES, sensitive)

    assert sensitive not in _safe_io_message(no_space)
    assert "espaço" in _safe_io_message(no_space)
    assert sensitive not in restore_message(blocked)
    assert "bloqueado" in restore_message(blocked)


def test_performance_settings_have_safe_balanced_defaults() -> None:
    settings = PerformanceSettings()

    assert settings.mapping_batch_size == 1_000
    assert settings.restoration_cache_limit == 4_096
    assert settings.progress_row_interval == 250
    assert settings.io_buffer_size == 1_048_576
    assert settings.restoration_window_rows == 5_000
    assert settings.sqlite_lookup_batch_size == 400


def test_batch_sizes_preserve_exact_csv_tokens_rows_and_columns(tmp_path) -> None:
    source = tmp_path / "input.csv"
    with source.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Texto"])
        for row in range(300):
            writer.writerow([f"{row % 7:04d}", f"linha-{row}"])
    configuration = [
        ColumnConfig("ID", True, "ID", NormalizationRule.EXACT),
        ColumnConfig("Texto", False, "", NormalizationRule.EXACT),
    ]
    first = tmp_path / "batch-1.csv"
    balanced = tmp_path / "balanced.csv"

    anonymize_csv(
        source,
        first,
        encoding="utf-8",
        delimiter=",",
        configurations=configuration,
        secret_key=b"H" * 32,
        mapping_batch_size=1,
    )
    anonymize_csv(
        source,
        balanced,
        encoding="utf-8",
        delimiter=",",
        configurations=configuration,
        secret_key=b"H" * 32,
        mapping_batch_size=1_000,
    )

    assert first.read_bytes() == balanced.read_bytes()
    with balanced.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert len(rows) == 301
    assert all(len(row) == 2 for row in rows)


def _high_cardinality_restoration(tmp_path: Path, unique: int = 4_200):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

    def code_for(index: int) -> str:
        encoded = ""
        number = index
        while number:
            number, remainder = divmod(number, len(alphabet))
            encoded = alphabet[remainder] + encoded
        return "ID-" + (encoded or "A").rjust(12, "A")

    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    candidates = [
        MappingCandidate(
            code_for(index),
            "ID",
            f"valor-{index}",
            "ID",
        )
        for index in range(unique)
    ]
    with repository.transaction() as transaction:
        transaction.upsert_batch(candidates)
    source = tmp_path / "anonimizado.csv"
    with source.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID"])
        for index in range(unique * 2):
            writer.writerow([candidates[index % unique].code])
    configuration = RestorationConfiguration(
        source,
        "utf-8",
        ",",
        ("ID",),
        (SelectedColumn(0, "ID"),),
    )
    return repository, candidates, configuration


def test_high_cardinality_above_cache_uses_windows_and_grouped_queries(
    tmp_path: Path,
) -> None:
    repository, candidates, configuration = _high_cardinality_restoration(tmp_path)
    metrics = RestorationMetrics()
    destination = tmp_path / "restaurado.csv"

    tracemalloc.start()
    result = restore_csv(
        configuration, destination, repository, metrics=metrics
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert result.rows_processed == len(candidates) * 2
    assert metrics.connections_opened == 1
    assert metrics.sqlite_queries < 100
    assert metrics.sqlite_queries < result.restored_codes // 100
    assert metrics.cache_hit_rate < 0.75
    assert metrics.decryptions >= len(candidates)
    assert max(metrics.codes_returned_per_query) <= 400
    assert peak < 64 * 1_048_576
    expected = tmp_path / "esperado.csv"
    with expected.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID"])
        for index in range(len(candidates) * 2):
            writer.writerow([f"valor-{index % len(candidates)}"])
    assert destination.read_bytes() == expected.read_bytes()


def test_cancellation_during_window_removes_temporary(tmp_path: Path) -> None:
    repository, _, configuration = _high_cardinality_restoration(tmp_path, unique=20)
    destination = tmp_path / "cancelado.csv"
    checks = 0

    def cancel_during_processing() -> bool:
        nonlocal checks
        checks += 1
        return checks > 25

    with pytest.raises(RestorationCancelled):
        restore_csv(
            configuration,
            destination,
            repository,
            should_cancel=cancel_during_processing,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".cancelado.csv.*.tmp"))


def test_restoration_metrics_are_aggregate_and_do_not_expose_data() -> None:
    metrics = RestorationMetrics(
        cache_hits=2,
        cache_misses=3,
        sqlite_queries=1,
        codes_returned=2,
        codes_returned_per_query=[2],
    )
    serialized = str(metrics.to_safe_dict())

    assert metrics.cache_hit_rate == pytest.approx(0.4)
    assert metrics.to_safe_dict()["codes_returned_per_query"] == {
        "minimum": 2,
        "maximum": 2,
        "average": 2.0,
        "distribution": {2: 1},
    }
    assert "CPF-ABCDEFGHI234" not in serialized
    assert "123.456.789-00" not in serialized


def test_benchmark_runs_use_exclusive_parameterized_directories() -> None:
    script = Path("scripts/run_benchmarks.ps1").read_text(encoding="utf-8")

    assert "rows-$Rows-unique-$UniqueValues-cols-$Columns" in script
    assert "Get-Date -Format 'yyyyMMdd-HHmmss-fff'" in script
    assert "[Guid]::NewGuid()" in script
    assert "benchmarks\\.data\\run" not in script
    assert "'fixture.csv'" in script
    assert "'work'" in script
    benchmark = Path("benchmarks/benchmark_csv.py").read_text(encoding="utf-8")
    assert "exist_ok=False" in benchmark
