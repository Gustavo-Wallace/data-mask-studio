"""Replay original shard 1 with dialog provenance and one-test language observations."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re

if __package__:
    from . import ci_pytest_shards as pilot
else:
    import ci_pytest_shards as pilot


TARGET = ("tests/test_theme_startup.py::"
          "test_theme_save_during_owned_work_is_deferred_and_language_draft_remains_independent")


def original_shard(manifest):
    pilot.require(manifest["shard_index"] == 1, "Only original shard 1 may be replayed")
    plan = manifest["plan"]
    pilot.verify_partition(plan["nodeids"], plan["modules"], plan["shards"])
    pilot.require(re.fullmatch(r"[0-9a-f]{40}", plan["commit"]), "Invalid context commit")
    shard = plan["shards"][1]
    pilot.require(TARGET in shard["nodeids"], "The original shard does not contain the diagnostic target")
    return shard


def summarize(events):
    """Interpret observed control flow, never service text or guessed causes."""
    before = next((i for i, item in enumerate(events) if item["event"] == "before_language_save"), None)
    after = next((i for i, item in enumerate(events) if item["event"] == "after_language_save"), None)
    if before is None or after is None:
        return {"observation": "language_save_attempt_not_reached_or_not_observed"}
    attempt = events[before:after + 1]
    names = [item["event"] for item in attempt]
    gates = [item["allowed"] for item in attempt if item["event"] == "gate.result"]
    writes = [item["persisted"] for item in attempt if item["event"] == "persistence.result"]
    rendered = [item["translator_locale"] for item in attempt if item["event"] == "retranslate.exit"]
    state = events[after]["state"]
    final_locale = next((item["state"]["translator_locale"] for item in reversed(events)
                         if item["event"] == "test.teardown"), state["translator_locale"])
    result = {"save_handler_entered": "save_handler.enter" in names,
              "language_handler_entered": "language_handler.enter" in names,
              "actual_gate_results": gates,
              "retranslation_attempted": "retranslate.enter" in names,
              "button_enabled_before": events[before]["state"]["button_enabled"],
              "translator_after": state["translator_locale"],
              "translator_at_teardown": final_locale,
              "persisted_after": events[after]["persisted"]}
    if not result["save_handler_entered"]:
        observation = "save_handler_not_called"
    elif "persistence.exception" in names or any(write["error"] is not None for write in writes):
        observation = "persistence_rejected_or_raised"
    elif any(gate is False for gate in gates):
        observation = "saved_but_live_application_deferred_by_guard"
    elif "en" in rendered and (state["translator_locale"] != "en" or final_locale != "en"):
        observation = "applied_then_overwritten"
    elif "language_handler.exception" in names or "retranslate.exception" in names:
        observation = "live_application_raised"
    elif state["translator_locale"] == "en":
        observation = "applied_no_failure_reproduced"
    else:
        observation = "unclassified_observe_raw_events"
    result["observation"] = observation
    return result


def run(args):
    manifest = pilot.read_json(args.context_manifest)
    shard = original_shard(manifest)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    args.basetemp.mkdir(parents=True, exist_ok=True)
    expected = args.output_dir / "expected.json"
    pilot.write_json(expected, shard)
    pilot.write_json(args.output_dir / "context.json", {
        "mode": "original-shard-1-only", "context_commit": manifest["plan"]["commit"],
        "instrumented_commit": os.environ.get("GITHUB_SHA", "local"),
        "modules_in_original_order": shard["modules"], "expected_count": len(shard["nodeids"]),
    })
    os.environ["DMS_QT_LANGUAGE_DIAGNOSTICS"] = "1"
    trace = args.output_dir / "language-save.jsonl"
    os.environ["DMS_QT_LANGUAGE_TRACE"] = str(trace.resolve())
    ownership_trace = args.output_dir / "dialog-ownership.jsonl"
    os.environ["DMS_QT_DIALOG_TRACE"] = str(ownership_trace.resolve())
    code = 0
    try:
        # No repartition, full-suite collection, deselection, retries or xdist.
        pilot.invoke_pytest(["-p", "ci_qt_dialog_ownership", *shard["modules"],
                             "--junitxml", str((args.output_dir / "pytest.xml").resolve())],
                            args.output_dir / "execution.json", args.basetemp / "execution", expected=expected)
    except (ValueError, KeyError, OSError) as error:
        print(f"Original shard execution failed: {error}")
        code = 1
    try:
        record = pilot.read_json(args.output_dir / "execution.json")
        pilot.require(record["nodeids"] == shard["nodeids"] and record["modules"] == shard["modules"]
                      and not record["deselected"], "Original shard collection/order mismatch")
        pilot.require(Counter(record["started_nodeids"]) == Counter(shard["nodeids"]),
                      "Original shard did not execute every context node exactly once")
        pilot.require(record["exit_code"] == 0 or code != 0, "A failing pytest result cannot pass")
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        pilot.require(any(item["event"] == "attached" for item in events), "Target diagnostics never attached")
        if record["exit_code"] == 0:
            pilot.require({"before_language_save", "after_language_save"}.issubset(
                item["event"] for item in events), "Passing tests without save observations are not a diagnostic success")
        summary = summarize(events)
        ownership_events = [json.loads(line) for line in ownership_trace.read_text(encoding="utf-8").splitlines()]
        pilot.require(ownership_events and ownership_events[0]["event"] == "tracker.started"
                      and ownership_events[-1]["event"] == "tracker.stopped", "Ownership trace is incomplete")
        summary["guard_message_boxes"] = [obj["ownership"] for event in events if event["event"] == "gate.result"
                                          for obj in event["state"]["other_windows"] if "ownership" in obj]
        summary["ownership_log"] = "dialog-ownership.jsonl"
        summary["original_shard_count"] = len(record["nodeids"])
        summary["pytest_exit_code"] = record["exit_code"]
        pilot.write_json(args.output_dir / "diagnostic-summary.json", summary)
        print(json.dumps(summary, indent=2))
    except (ValueError, KeyError, OSError) as error:
        print(f"Diagnostic evidence incomplete: {type(error).__name__}")
        code = 1
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--basetemp", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, KeyError, OSError) as error:
        print(f"Diagnostic replay refused: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
