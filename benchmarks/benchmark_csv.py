import argparse
import json
import sys
import time
import tracemalloc
from pathlib import Path

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.csv_tools import anonymize_csv, inspect_csv
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.performance import RestorationMetrics
from data_mask_studio.restoration import (
    RestorationConfiguration,
    RestorationService,
    SelectedColumn,
)
from data_mask_studio.vault import VaultCipher, VaultRepository


def _measure(operation):
    tracemalloc.start()
    started = time.perf_counter()
    result = operation()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed, peak


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark sintético de CSV.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--unique-values", type=int, required=True)
    parser.add_argument("--anonymized-columns", type=int, default=1)
    parser.add_argument("--prepopulate", action="store_true")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=False)
    benchmark_started = time.perf_counter()

    def phase(message: str) -> None:
        elapsed = time.perf_counter() - benchmark_started
        print(f"[{elapsed:8.1f}s] {message}", file=sys.stderr, flush=True)

    def report_progress(label: str):
        last_reported = 0

        def callback(progress) -> None:
            nonlocal last_reported
            rows = progress if isinstance(progress, int) else progress.rows_processed
            if rows - last_reported >= 10_000:
                last_reported = rows
                phase(f"{label}: {rows:,} linhas")

        return callback

    phase("Iniciando anonimização")
    inspection = inspect_csv(args.input)
    selected = min(args.anonymized_columns, len(inspection.headers))
    configs = [
        ColumnConfig(
            header,
            index < selected,
            f"COLUNA_{index + 1}" if index < selected else "",
            NormalizationRule.EXACT,
        )
        for index, header in enumerate(inspection.headers)
    ]
    vault_path = args.work_dir / "vault.db"
    repository = VaultRepository(vault_path, VaultCipher(b"V" * 32))
    anonymized = args.work_dir / "anonimizado.csv"
    restored = args.work_dir / "restaurado.csv"
    if args.prepopulate:
        warmup = args.work_dir / "warmup.csv"
        anonymize_csv(
            args.input,
            warmup,
            encoding=inspection.encoding,
            delimiter=inspection.delimiter,
            configurations=configs,
            secret_key=b"H" * 32,
            vault_repository=repository,
        )
    before_vault = vault_path.stat().st_size
    anonymization, anon_seconds, anon_peak = _measure(
        lambda: anonymize_csv(
            args.input,
            anonymized,
            encoding=inspection.encoding,
            delimiter=inspection.delimiter,
            configurations=configs,
            secret_key=b"H" * 32,
            vault_repository=repository,
            overwrite=True,
            progress_callback=report_progress("Anonimização"),
        )
    )
    phase("Anonimização concluída; iniciando restauração")
    configuration = RestorationConfiguration(
        anonymized,
        "utf-8-sig",
        inspection.delimiter,
        tuple(inspection.headers),
        tuple(SelectedColumn(index, inspection.headers[index]) for index in range(selected)),
    )
    restoration_metrics = RestorationMetrics()
    restoration, restore_seconds, restore_peak = _measure(
        lambda: RestorationService(lambda: repository.as_read_only()).restore(
            configuration,
            restored,
            overwrite=True,
            metrics=restoration_metrics,
            progress_callback=report_progress("Restauração"),
        )
    )
    phase("Restauração concluída")
    report = {
        "synthetic": True,
        "rows": args.rows,
        "unique_values": args.unique_values * selected,
        "repetitions": max(0, args.rows * selected - args.unique_values * selected),
        "anonymized_columns": selected,
        "anonymization": {
            "seconds": anon_seconds,
            "rows_per_second": args.rows / anon_seconds,
            "peak_memory_mib": anon_peak / 1_048_576,
            "vault_operations": anonymization.new_mappings + anonymization.updated_mappings,
        },
        "restoration": {
            "seconds": restore_seconds,
            "rows_per_second": args.rows / restore_seconds,
            "peak_memory_mib": restore_peak / 1_048_576,
            "restored_codes": restoration.restored_codes,
            "metrics": restoration_metrics.to_safe_dict(),
        },
        "output_csv_bytes": anonymized.stat().st_size,
        "vault_growth_bytes": vault_path.stat().st_size - before_vault,
    }
    report_path = args.work_dir / "resultado.json"
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.write_text(report_text + "\n", encoding="utf-8")
    print(report_text)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Benchmark interrompido durante a fase atual.", file=sys.stderr)
        raise SystemExit(130)
