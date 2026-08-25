import argparse
import gc
import json
import sys
import time
import tracemalloc
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from data_mask_studio.html_restoration import (
    analyze_html,
    inspect_html,
    restore_html,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.performance import HTMLProcessingMetrics
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository


BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
SYNTHETIC_KEY = b"H" * 32


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    records: int
    unique_codes: int
    include_common_content: bool = True


SCENARIOS = {
    scenario.name: scenario
    for scenario in (
        Scenario("small", 2_000, 100),
        Scenario("medium", 20_000, 1_000),
        Scenario("large", 80_000, 4_000),
        Scenario("repeated", 50_000, 10),
        Scenario("unique", 5_000, 5_000),
    )
}


class InstrumentedRepository:
    """Conta acessos unitários sem registrar códigos ou valores."""

    def __init__(self, repository: VaultRepository) -> None:
        self._repository = repository.as_read_only()
        self.lookup_calls = 0
        self.lookup_seconds = 0.0

    def as_read_only(self) -> "InstrumentedRepository":
        return self

    def get_decrypted_mapping(self, code: str):
        started = time.perf_counter()
        try:
            return self._repository.get_decrypted_mapping(code)
        finally:
            self.lookup_calls += 1
            self.lookup_seconds += time.perf_counter() - started

    @contextmanager
    def read_session(self, metrics=None):
        with self._repository.read_session(metrics) as session:
            yield session


def synthetic_code(index: int) -> str:
    number = index
    encoded = ""
    while number:
        number, remainder = divmod(number, len(BASE32_ALPHABET))
        encoded = BASE32_ALPHABET[remainder] + encoded
    return "SYN-" + (encoded or "A").rjust(12, "A")


def create_fixture(directory: Path, scenario: Scenario) -> tuple[Path, VaultRepository]:
    directory.mkdir(parents=True, exist_ok=False)
    codes = [synthetic_code(index) for index in range(scenario.unique_codes)]
    repository = VaultRepository(directory / "vault.db", VaultCipher(SYNTHETIC_KEY))
    for offset in range(0, len(codes), 1_000):
        candidates = [
            MappingCandidate(
                code,
                "SYN",
                f"synthetic-value-{offset + index:08d}",
                "Synthetic",
                normalization_rule=NormalizationRule.EXACT,
            )
            for index, code in enumerate(codes[offset : offset + 1_000])
        ]
        with repository.transaction() as transaction:
            transaction.upsert_batch(candidates)

    source = directory / "fixture.html"
    common = (
        "Synthetic dashboard content: no personal information is present. "
        if scenario.include_common_content
        else ""
    )
    with source.open("w", encoding="utf-8", newline="", buffering=1_048_576) as file:
        file.write("<!doctype html><html><body><script>const rows = [];\n</script>\n")
        for record in range(scenario.records):
            code = codes[record % len(codes)]
            file.write(
                f'<article data-code="{code}"><p>{common}{code}</p>'
                f'<script>rows.push({{"code":"{code}","safe":true}});</script>'
                "</article>\n"
            )
        file.write("</body></html>\n")
    return source, repository


def measure(operation):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    result = operation()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed, peak


def benchmark_scenario(run_directory: Path, scenario: Scenario) -> dict[str, object]:
    scenario_directory = run_directory / scenario.name
    print(f"[{scenario.name}] generating synthetic fixture", file=sys.stderr, flush=True)
    source, repository = create_fixture(scenario_directory, scenario)
    inspection = inspect_html(source)
    expected_occurrences = scenario.records * 3

    analysis_repository = InstrumentedRepository(repository)
    print(f"[{scenario.name}] analyzing", file=sys.stderr, flush=True)
    analysis, analysis_seconds, analysis_peak = measure(
        lambda: analyze_html(inspection, analysis_repository)
    )

    restoration_repository = InstrumentedRepository(repository)
    destination = scenario_directory / "restored.html"
    print(f"[{scenario.name}] restoring", file=sys.stderr, flush=True)
    restoration, restoration_seconds, restoration_peak = measure(
        lambda: restore_html(inspection, destination, restoration_repository)
    )
    if analysis.total_occurrences != expected_occurrences:
        raise RuntimeError("The synthetic analysis produced an unexpected result.")
    if restoration.restored_occurrences != expected_occurrences:
        raise RuntimeError("The synthetic restoration produced an unexpected result.")

    analysis_metrics = HTMLProcessingMetrics()
    restoration_metrics = HTMLProcessingMetrics()
    print(f"[{scenario.name}] profiling", file=sys.stderr, flush=True)
    analyze_html(inspection, repository, metrics=analysis_metrics)
    restore_html(
        inspection,
        scenario_directory / "profile-restored.html",
        repository,
        metrics=restoration_metrics,
    )

    return {
        "scenario": asdict(scenario),
        "input_bytes": source.stat().st_size,
        "token_occurrences": expected_occurrences,
        "unique_tokens": scenario.unique_codes,
        "analysis": {
            "seconds": analysis_seconds,
            "peak_memory_mib": analysis_peak / 1_048_576,
            "vault_lookup_calls": analysis_repository.lookup_calls,
            "vault_lookup_seconds": analysis_repository.lookup_seconds,
            "metrics": analysis_metrics.to_safe_dict(),
            "result": {
                "unique_codes": analysis.unique_codes,
                "total_occurrences": analysis.total_occurrences,
                "found_codes": analysis.found_codes,
                "missing_codes": analysis.missing_codes,
            },
        },
        "restoration": {
            "seconds": restoration_seconds,
            "peak_memory_mib": restoration_peak / 1_048_576,
            "vault_lookup_calls": restoration_repository.lookup_calls,
            "vault_lookup_seconds": restoration_repository.lookup_seconds,
            "metrics": restoration_metrics.to_safe_dict(),
            "output_bytes": destination.stat().st_size,
            "result": {
                "total_occurrences": restoration.total_occurrences,
                "restored_occurrences": restoration.restored_occurrences,
                "missing_occurrences": restoration.missing_occurrences,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic HTML restoration benchmark.")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(SCENARIOS),
        help="Scenario to run; may be repeated. Defaults to all scenarios.",
    )
    parser.add_argument("--label", default="benchmark")
    parser.add_argument("--output-root", type=Path, default=Path("benchmarks/.data/html"))
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_directory = args.output_root / f"{args.label}-{timestamp}-{uuid.uuid4().hex[:8]}"
    run_directory.mkdir(parents=True, exist_ok=False)
    selected = args.scenario or list(SCENARIOS)
    started = time.perf_counter()
    results = [benchmark_scenario(run_directory, SCENARIOS[name]) for name in selected]
    report = {
        "synthetic": True,
        "label": args.label,
        "run_directory": str(run_directory),
        "total_seconds": time.perf_counter() - started,
        "scenarios": results,
    }
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    (run_directory / "result.json").write_text(report_text + "\n", encoding="utf-8")
    print(report_text)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Benchmark interrupted during the active phase.", file=sys.stderr)
        raise SystemExit(130)
