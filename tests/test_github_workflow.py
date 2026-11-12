import json
from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def workflow(name):
    # JSON flow syntax is valid YAML (as in the validated pilot). Reject duplicate
    # keys too, without adding a YAML dependency solely for these contracts.
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            assert key not in result, f"Duplicate workflow key: {key}"
            result[key] = value
        return result

    return json.loads((WORKFLOWS / name).read_text("utf-8"), object_pairs_hook=unique_keys)


def assert_windows_environment(job):
    assert job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] == 45
    assert job["env"] == {"QT_QPA_PLATFORM": "offscreen"}
    assert job["steps"][0] == {"uses": "actions/checkout@v4"}
    setup = next(step for step in job["steps"] if step.get("uses") == "actions/setup-python@v5")
    assert setup["with"] == {"python-version": "3.12", "cache": "pip", "cache-dependency-path": "pyproject.toml"}
    assert any(step.get("run") == 'python -m pip install -e ".[dev]"' for step in job["steps"])
    isolation = job["steps"][1]["run"]
    assert all(value in isolation for value in (
        "$env:RUNNER_TEMP", "$env:GITHUB_RUN_ID", "$env:GITHUB_RUN_ATTEMPT",
        "LOCALAPPDATA=$local", "TEMP=$temp", "TMP=$temp", "$env:GITHUB_ENV",
        "New-Item -ItemType Directory -Force",
    ))
    assert not job.get("continue-on-error")
    assert not any(step.get("continue-on-error") for step in job["steps"])


def test_windows_workflow_is_isolated_and_runs_all_tests() -> None:
    production = workflow("tests.yml")
    assert production["name"] == "Testes"
    assert production["on"] == {
        "push": {"branches": ["main", "ui/**"]},
        "pull_request": {"branches": ["main"]},
        "workflow_dispatch": {},
    }
    assert production["permissions"] == {"contents": "read", "actions": "read"}
    assert set(production["jobs"]) == {"windows_shards", "tests"}
    shards = production["jobs"]["windows_shards"]
    assert_windows_environment(shards)
    assert shards["name"] == "Windows test shard ${{ matrix.shard }}"
    assert shards["strategy"] == {"max-parallel": 2, "fail-fast": False, "matrix": {"shard": [0, 1]}}
    assert "matrix.shard" in shards["steps"][1]["run"]
    execution = shards["steps"][-2]
    assert execution["run"] == (
        "python scripts/ci_pytest_shards.py run --shard-index ${{ matrix.shard }} "
        "--output-dir (Join-Path $env:DMS_CI_ROOT 'reports') "
        "--basetemp (Join-Path $env:DMS_CI_ROOT 'pytest')\nexit $LASTEXITCODE"
    )
    upload = shards["steps"][-1]
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert "always()" in upload["if"]
    assert upload["with"]["name"] == "ci-tests-shard-${{ matrix.shard }}"
    assert upload["with"]["path"] == "${{ env.DMS_CI_ROOT }}/reports/"
    assert upload["with"]["if-no-files-found"] == "error"
    text = json.dumps(production).casefold()
    assert not any(value in text for value in ("--timing-dir", "timing_run_id", "xdist", "release"))


def test_required_tests_gate_always_requires_both_shards_and_strict_artifacts():
    gate = workflow("tests.yml")["jobs"]["tests"]
    assert gate["name"] == "tests"
    assert gate["needs"] == ["windows_shards"]
    assert gate["if"] == "${{ always() }}"
    assert gate["timeout-minutes"] == 5
    assert not gate.get("continue-on-error")
    download = next(step for step in gate["steps"] if step.get("uses") == "actions/download-artifact@v4")
    assert download["with"] == {
        "pattern": "ci-tests-shard-*", "path": "${{ runner.temp }}/ci-test-results", "merge-multiple": False,
    }
    validation = gate["steps"][-1]
    assert validation["if"] == "${{ always() }}"
    assert validation["env"]["MATRIX_RESULT"] == "${{ needs.windows_shards.result }}"
    assert validation["run"] == (
        'python scripts/ci_pytest_shards.py aggregate --reports-dir "${{ runner.temp }}/ci-test-results" '
        '--matrix-result "$MATRIX_RESULT" --strict-execution'
    )
    assert not any(step.get("continue-on-error") for step in gate["steps"])


def test_full_sequential_fallback_is_manual_and_has_a_separate_check():
    fallback = workflow("tests-sequential.yml")
    assert fallback["on"] == {"workflow_dispatch": {}}
    assert fallback["permissions"] == {"contents": "read"}
    assert set(fallback["jobs"]) == {"serial_tests"}
    serial = fallback["jobs"]["serial_tests"]
    assert serial["name"] == "Windows serial fallback"
    assert_windows_environment(serial)
    assert serial["steps"][-2]["run"] == (
        "python -m pytest --basetemp (Join-Path $env:DMS_SERIAL_ROOT 'pytest') "
        "--junitxml (Join-Path $env:DMS_SERIAL_ROOT 'reports/pytest.xml')\nexit $LASTEXITCODE"
    )
    upload = serial["steps"][-1]
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert "always()" in upload["if"]
    assert upload["with"]["if-no-files-found"] == "error"


def test_production_check_names_do_not_overlap_pilot_or_fallback():
    names = []
    for filename in ("tests.yml", "tests-pilot.yml", "tests-sequential.yml"):
        for identifier, job in workflow(filename)["jobs"].items():
            template = job.get("name", identifier)
            indices = job.get("strategy", {}).get("matrix", {}).get("shard", [None])
            names.extend(template.replace("${{ matrix.shard }}", str(index)) for index in indices)
    assert len(names) == len(set(names))
    assert names.count("tests") == 1
