"""Lightweight checks for temporary diagnostic infrastructure, not another shard."""

from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from language_save_diagnostics import DisabledTrace, LanguageSaveTrace, TARGET, choice
from scripts import ci_qt_language_diagnostic as diagnostic
from data_mask_studio.preferences import PreferencesService
from test_bootstrap_localization import app
from test_live_language_settings import WaitingWorker
from test_navigation_localization import build_localized_window


def context():
    # Deliberately not alphabetic: replay must retain the recorded order.
    modules = ["tests/test_b.py", "tests/test_a.py", "tests/test_theme_startup.py"]
    nodes = [modules[0] + "::test_b", modules[1] + "::test_a", TARGET]
    return {"shard_index": 1, "plan": {"commit": "a" * 40, "modules": modules, "nodeids": nodes,
            "shards": [{"modules": [modules[1]], "nodeids": [nodes[1]]},
                       {"modules": [modules[0], modules[2]], "nodeids": [nodes[0], nodes[2]]}]}}


def test_context_replay_uses_only_original_shard_one_without_rebalancing(monkeypatch):
    manifest = context()
    before = deepcopy(manifest)
    monkeypatch.setattr(diagnostic.pilot, "partition", lambda *args: pytest.fail("Do not repartition"))
    result = diagnostic.original_shard(manifest)
    assert result == manifest["plan"]["shards"][1]
    assert result["modules"] == ["tests/test_b.py", "tests/test_theme_startup.py"]
    assert "tests/test_a.py" not in result["modules"]
    assert manifest == before


@pytest.mark.parametrize("defect", ["wrong-shard", "missing-target", "corrupt-coverage", "bad-commit"])
def test_invalid_original_context_is_refused(defect):
    manifest = context()
    if defect == "wrong-shard":
        manifest["shard_index"] = 0
    elif defect == "missing-target":
        replacement = "tests/test_theme_startup.py::another_test"
        manifest["plan"]["nodeids"][-1] = replacement
        manifest["plan"]["shards"][1]["nodeids"][-1] = replacement
    elif defect == "corrupt-coverage":
        manifest["plan"]["shards"][1]["nodeids"].pop()
    else:
        manifest["plan"]["commit"] = "not a commit"
    with pytest.raises(ValueError):
        diagnostic.original_shard(manifest)


def test_disabled_trace_does_not_touch_its_arguments_or_files():
    class Forbidden:
        def __getattribute__(self, name):
            pytest.fail("Disabled diagnostics must not inspect anything")
    trace = DisabledTrace()
    trace.watch(Forbidden(), Forbidden(), Forbidden())
    trace.capture("before_language_save")
    assert choice("private arbitrary input", ("en", "pt-BR")) == "unsupported"


@pytest.mark.parametrize("mode,expected", [
    ("applied", "applied_no_failure_reproduced"),
    ("dialog", "saved_but_live_application_deferred_by_guard"),
    ("unreadable", "save_handler_not_called"),
])
def test_read_only_probe_records_real_decisions_without_events_or_sensitive_text(
    app, tmp_path, monkeypatch, mode, expected,
):
    service = PreferencesService(tmp_path / "preferences.json")
    if mode == "unreadable":
        service.path.write_text("not-json", encoding="utf-8")
    trace = LanguageSaveTrace(tmp_path / "trace.jsonl")
    trace.install()
    try:
        window = build_localized_window(tmp_path, preferences_service=service, live_language_switching=True)
        window.show()
        window.set_current_page(window.page_index(window.settings_widget))
        app.processEvents()  # Ordinary setup, never part of instrumentation.
        worker = WaitingWorker()
        trace.watch(window, service, worker)
        window.setWindowTitle("PRIVATE TITLE SENTINEL")
        window.anonymization_widget.transfer_controls.password.setText("PRIVATE PASSWORD SENTINEL")
        if mode == "dialog":
            dialog = QMessageBox(window)
            dialog.setText("PRIVATE DIALOG SENTINEL")
        settings = window.settings_widget
        settings.language_combo.setCurrentIndex(settings.language_combo.findData("en"))
        with monkeypatch.context() as guard:
            guard.setattr(app, "processEvents", lambda *args: pytest.fail("No diagnostic event pumping"))
            guard.setattr(QThread, "start", lambda *args: pytest.fail("No diagnostic worker starts"))
            trace.capture("before_language_save")
            settings.save_button.click()
            trace.capture("after_language_save")
            if service.path.exists():
                stored = service.path.read_bytes(), service.path.stat().st_mtime_ns
                trace.capture("read_only_check")
                assert (service.path.read_bytes(), service.path.stat().st_mtime_ns) == stored
        summary = diagnostic.summarize(trace.events)
        assert summary["observation"] == expected
        assert summary["save_handler_entered"] is (mode != "unreadable")
        assert window._translator.locale == ("en" if mode == "applied" else "pt-BR")
        assert len([e for e in trace.events if e["event"] == "persistence.enter"]) == (mode != "unreadable")
        assert len([e for e in trace.events if e["event"] == "gate.result"]) == (mode != "unreadable")
    finally:
        trace.close()
    raw = trace.destination.read_text(encoding="utf-8")
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    decoded = tuple(strings([json.loads(line) for line in raw.splitlines()]))
    assert not any(path in value for value in decoded for path in (str(tmp_path), tmp_path.as_posix()))
    assert all(secret not in raw for secret in (
        "PRIVATE TITLE SENTINEL", "PRIVATE PASSWORD SENTINEL", "PRIVATE DIALOG SENTINEL"))


@pytest.mark.parametrize("kind,expected", [
    ("not-called", "save_handler_not_called"),
    ("rejected", "persistence_rejected_or_raised"),
    ("deferred", "saved_but_live_application_deferred_by_guard"),
    ("applied", "applied_no_failure_reproduced"),
    ("overwritten", "applied_then_overwritten"),
    ("unknown", "unclassified_observe_raw_events"),
])
def test_summary_distinguishes_observed_outcomes_without_parsing_messages(kind, expected):
    events = [{"event": "before_language_save", "state": {"button_enabled": kind != "not-called"}}]
    if kind != "not-called":
        events.append({"event": "save_handler.enter"})
    if kind == "rejected":
        events.append({"event": "persistence.result", "persisted": {"error": "WRITE_FAILED"}})
    if kind == "deferred":
        events.append({"event": "gate.result", "allowed": False})
    if kind in ("applied", "overwritten"):
        events.extend([{"event": "retranslate.enter"}, {"event": "retranslate.exit", "translator_locale": "en"}])
    events.append({"event": "after_language_save", "state": {"translator_locale": "en" if kind == "applied" else "pt-BR"},
                   "persisted": {"language": "en", "theme": "light", "error": None}})
    assert diagnostic.summarize(events)["observation"] == expected


def test_missing_observation_is_reported_as_unknown_not_as_a_fix():
    assert diagnostic.summarize([]) == {"observation": "language_save_attempt_not_reached_or_not_observed"}


@pytest.mark.parametrize("outcome", ["passed", "failed", "missing-trace"])
def test_replay_dispatches_exactly_once_and_cannot_turn_a_failure_into_success(tmp_path, monkeypatch, outcome):
    manifest = context()
    source = tmp_path / "manifest.json"
    diagnostic.pilot.write_json(source, manifest)
    args = SimpleNamespace(context_manifest=source, output_dir=tmp_path / "reports", basetemp=tmp_path / "pytest")
    expected = manifest["plan"]["shards"][1]
    calls = []
    # Routing-only unit check. Real Qt observation is exercised separately.
    def record_invocation(arguments, record, basetemp, *, expected):
        calls.append(arguments)
        assert basetemp.parent.is_dir()
        assert diagnostic.pilot.read_json(expected) == manifest["plan"]["shards"][1]
        assert os.environ["DMS_QT_LANGUAGE_DIAGNOSTICS"] == "1"
        shard = manifest["plan"]["shards"][1]
        diagnostic.pilot.write_json(record, {**shard, "started_nodeids": shard["nodeids"],
                                           "deselected": [], "exit_code": int(outcome == "failed")})
        if outcome != "missing-trace":
            events = [{"event": "attached"},
                      {"event": "before_language_save", "state": {"button_enabled": True}},
                      {"event": "save_handler.enter"},
                      {"event": "after_language_save", "state": {"translator_locale": "en"},
                       "persisted": {"language": "en", "theme": "light", "error": None}}]
            Path(os.environ["DMS_QT_LANGUAGE_TRACE"]).write_text(
                "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
        if outcome == "failed":
            raise ValueError("Synthetic pytest failure remains a failure")
    monkeypatch.setattr(diagnostic.pilot, "invoke_pytest", record_invocation)
    monkeypatch.setenv("DMS_QT_LANGUAGE_DIAGNOSTICS", "0")
    monkeypatch.setenv("DMS_QT_LANGUAGE_TRACE", "unused")
    assert diagnostic.run(args) == (outcome != "passed")
    assert len(calls) == 1
    assert calls[0][:-2] == expected["modules"]
    assert calls[0][-2] == "--junitxml"


def test_diagnostic_workflow_is_manual_one_windows_job_with_only_shard_one_artifact():
    root = Path(__file__).resolve().parents[1]
    workflow = json.loads((root / ".github/workflows/qt-language-diagnostic.yml").read_text(encoding="utf-8"))
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["context_run_id"]["required"] is True
    assert len(workflow["jobs"]) == 1
    job = workflow["jobs"]["diagnostic"]
    assert job["runs-on"] == "windows-latest" and "strategy" not in job
    assert job["env"] == {"QT_QPA_PLATFORM": "offscreen"}
    setup = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "3.12"
    download = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["name"] == "p03-shard-1"
    assert download["with"]["run-id"] == "${{ inputs.context_run_id }}"
    execute = next(step for step in job["steps"] if "ci_qt_language_diagnostic.py" in step.get("run", ""))
    assert "--context-manifest" in execute["run"]
    assert "--shard-index" not in execute["run"]
