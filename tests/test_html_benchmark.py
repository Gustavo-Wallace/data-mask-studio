"""Execute tiny synthetic workloads through the benchmark's real adapters and CLI."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks import benchmark_html as benchmark
from data_mask_studio.environment import EnvironmentError as EnvironmentLeaseError, RESTORE_JOURNAL
from data_mask_studio.html_restoration import (
    HTMLRestorationSecurityError, HTMLUnsupportedContextError, analyze_html, inspect_html, restore_html,
)
from data_mask_studio.performance import RestorationMetrics
from data_mask_studio.vault import VaultCipher, VaultRepository


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def test_html_benchmark_cli_runs_real_analysis_restoration_and_records_metrics(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")
    # Keep the production CLI, measurement phases and services; only shrink the
    # synthetic scenario so this is an executable smoke test, not a benchmark run.
    script = (
        "from benchmarks import benchmark_html as benchmark; "
        "benchmark.SCENARIOS = {'small': benchmark.Scenario('small', 6, 2)}; "
        "benchmark.main()"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script, "--scenario", "small", "--label", "smoke",
         "--output-root", str(tmp_path / "runs")],
        cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["synthetic"] is True and report["label"] == "smoke"
    assert report["total_seconds"] > 0
    run = Path(report["run_directory"])
    assert run.parent == tmp_path / "runs" and run.name.startswith("smoke-")
    assert json.loads((run / "result.json").read_text(encoding="utf-8")) == report
    scenario, = report["scenarios"]
    assert scenario["token_occurrences"] == 18 and scenario["unique_tokens"] == 2
    assert scenario["analysis"]["result"] == {
        "unique_codes": 2, "total_occurrences": 18, "found_codes": 2, "missing_codes": 0,
    }
    assert scenario["restoration"]["result"] == {
        "total_occurrences": 18, "restored_occurrences": 18, "missing_occurrences": 0,
    }
    for phase in ("analysis", "restoration"):
        measured = scenario[phase]
        assert measured["seconds"] > 0 and measured["peak_memory_mib"] > 0
        assert measured["vault_lookup_calls"] > 0 and measured["vault_lookup_seconds"] > 0
        assert measured["vault_lookup_seconds"] <= measured["seconds"]
        assert measured["metrics"]["bytes_processed"] == scenario["input_bytes"]
        assert measured["metrics"]["valid_occurrences"] == 18
        assert measured["metrics"]["vault"]["sqlite_queries"] > 0
        assert measured["metrics"]["vault"]["connections_opened"] == 1
    output = run / "small" / "restored.html"
    assert scenario["restoration"]["output_bytes"] == output.stat().st_size
    assert output.read_bytes() == (run / "small" / "profile-restored.html").read_bytes()
    assert "SYN-" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("common", [True, False])
def test_success_fixture_uses_only_supported_contexts_with_three_occurrences_per_record(tmp_path, common):
    scenario = benchmark.Scenario("tiny", 4, 2, include_common_content=common)
    source, repository = benchmark.create_fixture(tmp_path / "case", scenario)
    original = source.read_bytes()
    vault_before = repository.database_path.read_bytes()
    inspection = inspect_html(source)
    analysis = analyze_html(inspection, repository)
    destination = source.with_name("restored.html")
    result = restore_html(inspection, destination, repository)
    assert analysis.total_occurrences == result.restored_occurrences == 12
    assert analysis.unique_codes == analysis.found_codes == 2 and analysis.missing_codes == 0
    text = destination.read_text(encoding="utf-8")
    for index in range(2):
        assert text.count(f"synthetic-value-{index:08d}") == 6
    assert ("Synthetic dashboard content:" in text) is common
    assert source.read_bytes() == original
    assert repository.database_path.read_bytes() == vault_before


def test_adapter_delegates_real_repository_identity_and_counts_scalar_and_bulk_lookups(tmp_path):
    _, repository = benchmark.create_fixture(tmp_path / "case", benchmark.Scenario("tiny", 2, 2))
    before = repository.database_path.read_bytes()
    adapter = benchmark.InstrumentedRepository(repository)
    assert adapter.database_path == repository.database_path and adapter.as_read_only() is adapter
    assert adapter.get_decrypted_mapping(benchmark.synthetic_code(0)).original_value == "synthetic-value-00000000"
    assert adapter.lookup_calls == 1
    metrics = RestorationMetrics()
    with adapter.read_session(metrics) as session:
        found = session.get_many([benchmark.synthetic_code(0), benchmark.synthetic_code(1)])
    assert len(found) == 2 and adapter.lookup_calls == 2 and adapter.lookup_seconds > 0
    assert metrics.connections_opened == 1 and metrics.sqlite_queries > 0 and metrics.decryptions > 0
    assert repository.database_path.read_bytes() == before


@pytest.mark.parametrize("known", [True, False])
@pytest.mark.parametrize("template", [
    '<script>rows.push({"code":"TOKEN","safe":true});</script>',
    '<script type="application/json">{"code":"TOKEN"}</script>',
])
def test_script_is_a_rejected_security_scenario_not_a_success_workload(tmp_path, template, known):
    source, repository = benchmark.create_fixture(tmp_path / "case", benchmark.Scenario("negative", 1, 1))
    code = benchmark.synthetic_code(0 if known else 7)
    source.write_text(template.replace("TOKEN", code), encoding="utf-8")
    source_before, vault_before = source.read_bytes(), repository.database_path.read_bytes()
    adapter = benchmark.InstrumentedRepository(repository)
    destination = source.with_name("rejected.html")
    inspection = inspect_html(source)
    for operation in (
        lambda: analyze_html(inspection, adapter),
        lambda: restore_html(inspection, destination, adapter),
    ):
        with pytest.raises(HTMLUnsupportedContextError) as error:
            operation()
        assert code not in str(error.value)
        assert not destination.exists() and not list(source.parent.glob("*.tmp"))
        assert source.read_bytes() == source_before and repository.database_path.read_bytes() == vault_before


@pytest.mark.parametrize("phase", ["analysis", "restoration"])
def test_adapter_does_not_bypass_environment_recovery_guard(tmp_path, phase):
    source, repository = benchmark.create_fixture(tmp_path / "case", benchmark.Scenario("guard", 1, 1))
    adapter = benchmark.InstrumentedRepository(repository)
    (source.parent / RESTORE_JOURNAL).write_text("{}", encoding="utf-8")
    inspection = inspect_html(source)
    destination = source.with_name("restored.html")
    with pytest.raises(EnvironmentLeaseError):
        if phase == "analysis":
            analyze_html(inspection, adapter)
        else:
            restore_html(inspection, destination, adapter)
    assert adapter.lookup_calls == 0 and not destination.exists()


@pytest.mark.parametrize("phase", ["analysis", "restoration"])
def test_adapter_preserves_authentication_failure_and_counts_failed_lookups(tmp_path, phase):
    source, repository = benchmark.create_fixture(tmp_path / "case", benchmark.Scenario("wrong-key", 1, 1))
    before = repository.database_path.read_bytes()
    wrong_key = VaultRepository(repository.database_path, VaultCipher(b"W" * 32), read_only=True)
    adapter = benchmark.InstrumentedRepository(wrong_key)
    inspection = inspect_html(source)
    destination = source.with_name("rejected.html")
    with pytest.raises(HTMLRestorationSecurityError):
        if phase == "analysis":
            analyze_html(inspection, adapter)
        else:
            restore_html(inspection, destination, adapter)
    assert adapter.lookup_calls == 1 and adapter.lookup_seconds > 0
    assert repository.database_path.read_bytes() == before
    assert not destination.exists() and not list(source.parent.glob("*.tmp"))
