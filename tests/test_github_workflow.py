from pathlib import Path


WORKFLOW = Path(".github/workflows/tests.yml")


def test_windows_workflow_is_isolated_and_runs_all_tests() -> None:
    content = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: windows-latest" in content
    assert 'python-version: "3.12"' in content
    assert "QT_QPA_PLATFORM: offscreen" in content
    assert "LOCALAPPDATA: ${{ runner.temp }}" in content
    assert 'pip install -e ".[dev]"' in content
    assert "python -m pytest" in content
    assert "push:" in content
    assert "pull_request:" in content
    assert "workflow_dispatch:" in content
    assert "release" not in content.casefold()
