"""Lightweight pilot regressions; subprocesses execute only tiny synthetic suites."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_pytest_shards.py"
spec = importlib.util.spec_from_file_location("pilot_under_test", SCRIPT)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def suite():
    modules = [f"tests/test_{name}.py" for name in "abcd"]
    nodes = [f"{module}::test_value[case-{index}]"
             for module, count in zip(modules, (6, 3, 2, 1)) for index in range(count)]
    return nodes, modules


def reports(tmp_path):
    nodes, modules = suite()
    plan = {"commit": "synthetic", "modules": modules, "nodeids": nodes,
            "collection_skips": [], "balancing": "collected-case-count-estimate",
            "historical_modules": 0, "shards": pilot.partition(nodes, modules)}
    for index, shard in enumerate(plan["shards"]):
        directory = tmp_path / f"p03-shard-{index}"
        directory.mkdir()
        pilot.write_json(directory / "manifest.json", {"shard_index": index, "plan": plan})
        pilot.write_json(directory / "execution.json", {
            "modules": shard["modules"], "nodeids": shard["nodeids"],
            "started_nodeids": shard["nodeids"], "exit_code": 0, "deselected": [],
            "collection_skips": [], "duration_seconds": 2.5, "process_seconds": 2.6,
            "outcomes": {"call:passed": len(shard["nodeids"])},
            "module_seconds": {name: 1.0 for name in shard["modules"]},
        })
    return plan


def test_partition_is_deterministic_case_weighted_and_keeps_whole_modules():
    nodes, modules = suite()
    result = pilot.partition(nodes, modules)
    assert result == pilot.partition(nodes, modules)
    assert [len(shard["nodeids"]) for shard in result] == [6, 6]
    assert [len(shard["modules"]) for shard in result] == [1, 3]
    assert result[0]["nodeids"] == nodes[:6]
    pilot.verify_partition(nodes, modules, result)


def test_measured_timings_take_precedence_over_equal_case_or_file_counts():
    nodes, modules = suite()
    durations = dict(zip(modules, (9.0, 8.0, 2.0, 1.0)))
    result = pilot.partition(nodes, modules, durations)
    assert [shard["estimated_weight"] for shard in result] == [10.0, 10.0]
    assert result[0]["modules"] == [modules[0], modules[3]]
    assert result == pilot.partition(nodes, modules, dict(reversed(list(durations.items()))))


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan"), "slow"])
def test_invalid_timings_fail_closed(seconds):
    nodes, modules = suite()
    with pytest.raises((ValueError, TypeError)):
        pilot.partition(nodes, modules, {modules[0]: seconds})


@pytest.mark.parametrize("name", ["", "../tests/test_x.py", "/tests/test_x.py", "tests/../x.py",
                                  "C:/tests/test_x.py", "tests/test_x.txt", "tests/..\\test_x.py"])
def test_module_paths_cannot_escape_the_test_tree(name):
    with pytest.raises(ValueError):
        pilot.module_name(name)


@pytest.mark.parametrize("defect", ["omission", "duplicate", "split-module", "order", "duplicate-module"])
def test_partition_verifier_rejects_inexact_manifests(defect):
    nodes, modules = suite()
    shards = pilot.partition(nodes, modules)
    if defect == "omission":
        shards[0]["nodeids"].pop()
    elif defect == "duplicate":
        shards[0]["nodeids"].append(shards[0]["nodeids"][0])
    elif defect == "split-module":
        shards[1]["nodeids"].append(shards[0]["nodeids"].pop())
    elif defect == "order":
        shards[0]["nodeids"].reverse()
    else:
        shards[1]["modules"].append(shards[0]["modules"][0])
    with pytest.raises(ValueError):
        pilot.verify_partition(nodes, modules, shards)


def test_empty_modules_are_assigned_once_and_parametrized_ids_remain_literal():
    nodes, modules = suite()
    nodes[0] = modules[0] + "::test_value[unicode-á/path :: user label]"
    modules.append("tests/test_empty.py")
    shards = pilot.partition(nodes, modules)
    pilot.verify_partition(nodes, modules, shards)
    assert sum("tests/test_empty.py" in shard["modules"] for shard in shards) == 1
    assert sum(nodes[0] in shard["nodeids"] for shard in shards) == 1


def test_complete_successful_reports_provide_verified_historical_weights(tmp_path):
    plan = reports(tmp_path)
    actual, executions = pilot.verified_reports(tmp_path)
    assert actual == plan and len(executions) == 2
    assert pilot.historical_timings(tmp_path) == {name: 1.0 for name in plan["modules"]}


@pytest.mark.parametrize("defect", ["missing-shard", "duplicate-shard", "baseline", "collection",
                                   "not-executed", "executed-twice", "failed", "deselected", "skip"])
def test_aggregate_rejects_missing_failed_or_inexact_execution_evidence(tmp_path, defect):
    reports(tmp_path)
    directory = tmp_path / "p03-shard-0"
    path = directory / "execution.json"
    value = pilot.read_json(path)
    if defect == "missing-shard":
        (directory / "manifest.json").unlink()
    elif defect == "duplicate-shard":
        manifest = pilot.read_json(directory / "manifest.json")
        manifest["shard_index"] = 1
        pilot.write_json(directory / "manifest.json", manifest)
    elif defect == "baseline":
        manifest = pilot.read_json(directory / "manifest.json")
        manifest["plan"]["commit"] = "different"
        pilot.write_json(directory / "manifest.json", manifest)
    elif defect == "collection":
        value["nodeids"].pop()
    elif defect == "not-executed":
        value["started_nodeids"].pop()
    elif defect == "executed-twice":
        value["started_nodeids"].append(value["started_nodeids"][0])
    elif defect == "failed":
        value["exit_code"] = 1
    elif defect == "deselected":
        value["deselected"] = ["tests/test_synthetic.py::test_value"]
    else:
        value["collection_skips"] = ["tests/test_unexpected.py"]
    pilot.write_json(path, value)
    with pytest.raises(ValueError):
        pilot.verified_reports(tmp_path)
    with pytest.raises(ValueError):
        pilot.historical_timings(tmp_path)


@pytest.mark.parametrize("state", ["success", "failure", "cancelled", "skipped"])
def test_final_gate_requires_successful_job_state_even_with_complete_reports(tmp_path, monkeypatch, state):
    reports(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert pilot.main(["aggregate", "--reports-dir", str(tmp_path), "--matrix-result", state]) == (state != "success")


def synthetic_suite(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (tests / "test_a.py").write_text(
        'import pytest\nfrom pathlib import Path\n'
        '@pytest.mark.parametrize("value", [1, 2])\n'
        'def test_value(value, tmp_path):\n'
        '    Path("executed.txt").write_text("synthetic")\n'
        '    assert tmp_path.is_dir()\n'
        '    assert value > 0\n', encoding="utf-8")
    (tests / "test_b.py").write_text(
        'def test_other(tmp_path):\n    assert tmp_path.is_dir()\n    assert 2 + 2 == 4\n', encoding="utf-8")
    return tests


def command(tmp_path, *args):
    environment = os.environ.copy()
    for name in (pilot.RECORD_ENV, pilot.EXPECTED_ENV, "PYTEST_ADDOPTS", "GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY"):
        environment.pop(name, None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"  # Only the tiny synthetic suite.
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["LOCALAPPDATA"] = str(tmp_path / "synthetic-localappdata")
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], cwd=tmp_path,
                          env=environment, capture_output=True, text=True, timeout=60)


def test_real_collection_only_covers_all_parameters_without_executing_tests(tmp_path):
    synthetic_suite(tmp_path)
    result = command(tmp_path, "collect", "--output-dir", "reports", "--basetemp", "pytest-temp")
    assert result.returncode == 0, result.stdout + result.stderr
    plan = pilot.read_json(tmp_path / "reports/coverage.json")
    assert len(plan["nodeids"]) == 3
    assert any(node.endswith("[1]") for node in plan["nodeids"])
    assert any(node.endswith("[2]") for node in plan["nodeids"])
    assert not (tmp_path / "executed.txt").exists()
    assert (tmp_path / "pytest-temp").is_dir()
    assert not (tmp_path / "pytest-temp/collection").exists()
    assert not (tmp_path / "pytest-temp/execution").exists()


def test_two_real_synthetic_shards_execute_once_and_generate_reusable_timings(tmp_path):
    synthetic_suite(tmp_path)
    for index in range(2):
        assert not (tmp_path / f"pytest-{index}").exists()
        result = command(tmp_path, "run", "--shard-index", str(index), "--output-dir",
                         f"reports/p03-shard-{index}", "--basetemp", f"pytest-{index}")
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / f"pytest-{index}/execution").is_dir()
    plan, executions = pilot.verified_reports(tmp_path / "reports")
    assert sum(len(record["started_nodeids"]) for record in executions.values()) == 3
    assert set(pilot.historical_timings(tmp_path / "reports")) == set(plan["modules"])
    assert all(record["duration_seconds"] > 0 for record in executions.values())
    result = command(tmp_path, "collect", "--output-dir", "timed", "--basetemp", "pytest-timed",
                     "--timing-dir", "reports")
    assert result.returncode == 0, result.stdout + result.stderr
    assert pilot.read_json(tmp_path / "timed/coverage.json")["historical_modules"] == 2


def test_missing_pytest_leaf_after_start_is_not_recreated_or_retried(tmp_path):
    tests = synthetic_suite(tmp_path)
    (tests / "test_a.py").write_text(
        'from pathlib import Path\n'
        'def test_remove_empty_owned_base(request):\n'
        '    base = request.config._tmp_path_factory.getbasetemp()\n'
        '    assert base == Path("pytest-temp/execution").resolve()\n'
        '    assert not list(base.iterdir())\n'
        '    base.rmdir()\n'
        'def test_next_fixture(tmp_path):\n'
        '    assert tmp_path.is_dir()\n', encoding="utf-8")
    result = command(tmp_path, "run", "--shard-index", "0", "--output-dir", "reports/p03-shard-0",
                     "--basetemp", "pytest-temp")
    assert result.returncode == 1
    assert "FileNotFoundError" in result.stdout
    assert (tmp_path / "pytest-temp").is_dir()
    assert not (tmp_path / "pytest-temp/execution").exists()
    record = pilot.read_json(tmp_path / "reports/p03-shard-0/execution.json")
    assert record["exit_code"] == 1 and record["outcomes"]["setup:failed"] == 1
    assert len(record["started_nodeids"]) == 2  # No retry.


def test_invalid_basetemp_container_is_preserved_and_fails_before_collection(tmp_path):
    synthetic_suite(tmp_path)
    blocked = tmp_path / "pytest-temp"
    blocked.write_bytes(b"synthetic non-directory sentinel")
    result = command(tmp_path, "run", "--shard-index", "0", "--output-dir", "reports/p03-shard-0",
                     "--basetemp", "pytest-temp")
    assert result.returncode == 1
    assert blocked.read_bytes() == b"synthetic non-directory sentinel"
    assert not (tmp_path / "reports/p03-shard-0/collection.json").exists()
    assert not (tmp_path / "executed.txt").exists()


def test_collection_guard_fails_before_running_any_synthetic_test(tmp_path, monkeypatch):
    synthetic_suite(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "synthetic-localappdata"))
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    expected = tmp_path / "expected.json"
    pilot.write_json(expected, {"modules": ["tests/test_a.py"], "nodeids": ["tests/test_a.py::absent"]})
    with pytest.raises(ValueError, match="pytest exited 4"):
        pilot.invoke_pytest(["tests/test_a.py"], tmp_path / "guard.json", tmp_path / "base", expected=expected)
    record = pilot.read_json(tmp_path / "guard.json")
    assert record["exit_code"] == 4 and record["started_nodeids"] == []
    assert not (tmp_path / "executed.txt").exists()


def test_a_real_synthetic_failure_is_retained_and_cannot_pass_the_pilot(tmp_path):
    tests = synthetic_suite(tmp_path)
    (tests / "test_b.py").write_text('def test_failure():\n    assert False, "synthetic rejection"\n', encoding="utf-8")
    result = command(tmp_path, "run", "--shard-index", "1", "--output-dir", "reports/p03-shard-1",
                     "--basetemp", "pytest-temp")
    assert result.returncode == 1
    record = pilot.read_json(tmp_path / "reports/p03-shard-1/execution.json")
    assert record["exit_code"] == 1 and record["outcomes"]["call:failed"] == 1
    assert (tmp_path / "reports/p03-shard-1/pytest.xml").is_file()


def test_pilot_workflow_is_valid_json_yaml_and_manual_only_with_independent_jobs():
    # JSON flow syntax is a YAML subset, permitting syntax checks without a new
    # YAML dependency. GitHub expressions remain ordinary quoted scalar values.
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            assert key not in result, f"Duplicate workflow key: {key}"
            result[key] = value
        return result

    workflow = json.loads((ROOT / ".github/workflows/tests-pilot.yml").read_text("utf-8"), object_pairs_hook=unique_keys)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    shards = workflow["jobs"]["shards"]
    assert shards["runs-on"] == "windows-latest" and shards["timeout-minutes"] == 45
    assert shards["strategy"] == {"max-parallel": 2, "fail-fast": False, "matrix": {"shard": [0, 1]}}
    assert shards["env"] == {"QT_QPA_PLATFORM": "offscreen"}
    setup = next(step for step in shards["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"] == {"python-version": "3.12", "cache": "pip", "cache-dependency-path": "pyproject.toml"}
    assert any(step.get("run") == 'python -m pip install -e ".[dev]"' for step in shards["steps"])
    isolation = shards["steps"][1]["run"]
    assert all(name in isolation for name in ("LOCALAPPDATA=", "TEMP=", "TMP=", "GITHUB_ENV", "matrix.shard"))
    assert "--basetemp" in shards["steps"][-2]["run"]
    aggregate = workflow["jobs"]["aggregate"]
    assert aggregate["needs"] == ["shards"] and aggregate["if"] == "${{ always() }}"
    assert aggregate["steps"][-1]["env"]["MATRIX_RESULT"] == "${{ needs.shards.result }}"
    assert not any(job.get("continue-on-error") for job in workflow["jobs"].values())
