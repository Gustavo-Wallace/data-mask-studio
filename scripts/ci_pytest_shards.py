"""Opt-in, two-process/VM CI pilot; never changes tests or their eligibility.

The pytest plugin records collection and phase durations only. Each shard first
collects the entire suite in a separate interpreter, then runs complete modules
with an exact collection guard. Reports contain test identifiers, not app data.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path, PurePosixPath
import statistics
import subprocess
import sys
import time
from urllib.request import Request, urlopen


SHARD_COUNT = 2
RECORD_ENV = "DMS_PILOT_RECORD"
EXPECTED_ENV = "DMS_PILOT_EXPECTED"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def module_name(nodeid):
    name = nodeid.split("::", 1)[0]
    path = PurePosixPath(name)
    require(bool(path.parts) and "\\" not in name and ":" not in name
            and name == path.as_posix() and not path.is_absolute()
            and path.parts[0] == "tests" and ".." not in path.parts
            and path.suffix == ".py", f"Invalid test module: {name!r}")
    return name


def verify_partition(nodeids, modules, shards):
    require(nodeids and len(nodeids) == len(set(nodeids)), "Empty or duplicate baseline node IDs")
    require(len(modules) == len(set(modules)), "Duplicate baseline modules")
    require(len(shards) == SHARD_COUNT, "Exactly two shards are required")
    for name in modules:
        require(module_name(name) == name, "Invalid module manifest")
    combined_nodes, combined_modules = [], []
    for shard in shards:
        require(shard["modules"] and shard["nodeids"], "Empty shard")
        expected = [node for node in nodeids if module_name(node) in shard["modules"]]
        require(shard["nodeids"] == expected, "Module split, omission, duplicate, or changed collection order")
        combined_nodes.extend(shard["nodeids"])
        combined_modules.extend(shard["modules"])
    require(Counter(combined_nodes) == Counter(nodeids), "Node coverage is not exactly once")
    require(Counter(combined_modules) == Counter(modules), "Module coverage is not exactly once")


def partition(nodeids, modules, timings=None):
    """Deterministic longest-estimated-module-first scheduling, stable tie breaks."""
    timings = timings or {}
    counts = Counter(module_name(node) for node in nodeids)
    rates = [seconds / max(counts[name], 1) for name, seconds in timings.items() if name in modules]
    fallback_rate = statistics.median(rates) if rates else 1.0
    weights = {name: timings.get(name, max(counts[name], 1) * fallback_rate) for name in modules}
    require(all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
                for v in weights.values()), "Invalid timing estimate")
    assigned, loads = [set(), set()], [0.0, 0.0]
    for name in sorted(modules, key=lambda name: (-weights[name], name)):
        index = min(range(SHARD_COUNT), key=lambda index: (loads[index], index))
        assigned[index].add(name)
        loads[index] += weights[name]
    shards = [{"modules": [name for name in modules if name in assigned[index]],
               "nodeids": [node for node in nodeids if module_name(node) in assigned[index]],
               "estimated_weight": loads[index]} for index in range(SHARD_COUNT)]
    verify_partition(nodeids, modules, shards)
    return shards


class CollectionRecorder:
    def __init__(self, destination, expected=None):
        self.destination, self.expected = destination, expected
        self.started = time.perf_counter()
        self.modules, self.nodeids, self.started_nodeids = [], [], []
        self.deselected, self.collection_skips = [], []
        self.module_seconds, self.outcomes = Counter(), Counter()

    def pytest_collectstart(self, collector):
        import pytest
        if isinstance(collector, pytest.Module):
            self.modules.append(collector.nodeid)

    def pytest_collectreport(self, report):
        if report.skipped:
            self.collection_skips.append(report.nodeid)

    def pytest_deselected(self, items):
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collection_finish(self, session):
        import pytest
        self.nodeids = [item.nodeid for item in session.items]
        if self.expected is not None:
            expected = read_json(self.expected)
            if (self.deselected or self.nodeids != expected["nodeids"]
                    or self.modules != expected["modules"]):
                pytest.exit("Pilot collection differs from the verified manifest; no tests will run", returncode=4)

    def pytest_runtest_logreport(self, report):
        self.module_seconds[module_name(report.nodeid)] += report.duration
        self.outcomes[f"{report.when}:{report.outcome}"] += 1
        if report.when == "setup":
            self.started_nodeids.append(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        write_json(self.destination, {
            "modules": self.modules, "nodeids": self.nodeids,
            "started_nodeids": self.started_nodeids, "deselected": self.deselected,
            "collection_skips": self.collection_skips, "exit_code": int(exitstatus),
            "duration_seconds": time.perf_counter() - self.started,
            "module_seconds": dict(self.module_seconds), "outcomes": dict(self.outcomes),
        })


def pytest_configure(config):
    """Loaded only by the pilot's explicit -p option, never by normal pytest."""
    if os.environ.get(RECORD_ENV):
        config.pluginmanager.register(CollectionRecorder(
            os.environ[RECORD_ENV], os.environ.get(EXPECTED_ENV)), "dms-pilot-recorder")


def invoke_pytest(arguments, record, basetemp, *, expected=None, collect=False):
    environment = os.environ.copy()
    environment[RECORD_ENV] = str(record.resolve())
    environment.pop(EXPECTED_ENV, None)
    if expected is not None:
        environment[EXPECTED_ENV] = str(expected.resolve())
    plugin_directory = str(Path(__file__).resolve().parent)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [plugin_directory, environment.get("PYTHONPATH")]))
    command = [sys.executable, "-m", "pytest", "-p", "ci_pytest_shards",
               "--basetemp", str(basetemp.resolve()), *arguments]
    print(subprocess.list2cmdline(command), flush=True)
    started = time.perf_counter()
    if collect:
        with record.with_suffix(".log").open("w", encoding="utf-8") as log:
            code = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT).returncode
    else:
        code = subprocess.run(command, env=environment).returncode
    if record.is_file():
        result = read_json(record)
        result["process_seconds"] = time.perf_counter() - started
        write_json(record, result)
    require(code == 0, f"pytest exited {code}; see {record.name} and collection logs")
    result = read_json(record)
    require(result["exit_code"] == 0 and not result["deselected"], "Failed or deselected collection")
    return result


def verified_reports(directory):
    manifests = sorted(Path(directory).rglob("manifest.json"))
    require(len(manifests) == SHARD_COUNT, "Missing or duplicate shard reports")
    reports = [read_json(path) for path in manifests]
    require({report["shard_index"] for report in reports} == {0, 1}, "Missing or duplicate shard index")
    plan = reports[0]["plan"]
    require(all(report["plan"] == plan for report in reports), "Shard baseline/partition disagreement")
    verify_partition(plan["nodeids"], plan["modules"], plan["shards"])
    executions, seen_skips = {}, []
    for path, report in zip(manifests, reports):
        index = report["shard_index"]
        execution = read_json(path.with_name("execution.json"))
        expected = plan["shards"][index]
        require(execution["exit_code"] == 0, f"Shard {index} failed")
        require(not execution["deselected"], f"Shard {index} deselected tests")
        require(execution["nodeids"] == expected["nodeids"]
                and execution["modules"] == expected["modules"], f"Shard {index} collection mismatch")
        require(Counter(execution["started_nodeids"]) == Counter(expected["nodeids"]),
                f"Shard {index} did not execute each collected test exactly once")
        seen_skips.extend(execution["collection_skips"])
        executions[index] = execution
    require(Counter(seen_skips) == Counter(plan["collection_skips"]), "Collection skip disagreement")
    return plan, executions


def historical_timings(directory):
    if directory is None:
        return {}
    _, executions = verified_reports(directory)
    timings = {}
    for execution in executions.values():
        for name, seconds in execution["module_seconds"].items():
            require(name not in timings and isinstance(seconds, (int, float))
                    and math.isfinite(seconds) and seconds > 0, "Invalid historical module timing")
            timings[name] = seconds
    return timings


def prepare(args):
    args.output_dir.mkdir(parents=True, exist_ok=False)
    # We own this container; pytest owns (and initializes) each leaf basetemp.
    # Collection does not request tmp_path, so it will not create this parent.
    args.basetemp.mkdir(parents=True, exist_ok=True)
    baseline = invoke_pytest(["--collect-only"], args.output_dir / "collection.json",
                             args.basetemp / "collection", collect=True)
    require(not baseline["started_nodeids"], "Collection unexpectedly executed tests")
    timings = historical_timings(args.timing_dir)
    matched = len(set(timings).intersection(baseline["modules"]))
    plan = {"commit": os.environ.get("GITHUB_SHA", "local"),
            "modules": baseline["modules"], "nodeids": baseline["nodeids"],
            "collection_skips": baseline["collection_skips"],
            "balancing": "historical-phase-seconds" if matched else "collected-case-count-estimate",
            "historical_modules": matched,
            "shards": partition(baseline["nodeids"], baseline["modules"], timings)}
    print(f"Coverage: {len(plan['nodeids'])} nodes, {len(plan['modules'])} modules; "
          f"balancing={plan['balancing']}, historical modules={matched}", flush=True)
    return plan


def collect_only(args):
    plan = prepare(args)
    actual = []
    for index, shard in enumerate(plan["shards"]):
        expected = args.output_dir / f"expected-{index}.json"
        write_json(expected, shard)
        result = invoke_pytest(["--collect-only", *shard["modules"]],
                               args.output_dir / f"shard-{index}-collection.json",
                               args.basetemp / f"shard-{index}", expected=expected, collect=True)
        actual.append(result)
    verify_partition(plan["nodeids"], plan["modules"], actual)
    require(Counter(skip for result in actual for skip in result["collection_skips"])
            == Counter(plan["collection_skips"]), "Collection skip disagreement")
    write_json(args.output_dir / "coverage.json", plan)
    print("Both real shard collections match the full collection exactly once.")
    return 0


def run_shard(args):
    started = time.perf_counter()
    plan = prepare(args)
    write_json(args.output_dir / "manifest.json", {"shard_index": args.shard_index, "plan": plan})
    expected = args.output_dir / "expected.json"
    shard = plan["shards"][args.shard_index]
    write_json(expected, shard)
    print(f"Shard {args.shard_index}: {len(shard['nodeids'])} tests in {len(shard['modules'])} modules", flush=True)
    try:
        invoke_pytest([*shard["modules"], "--junitxml", str((args.output_dir / "pytest.xml").resolve())],
                      args.output_dir / "execution.json", args.basetemp / "execution", expected=expected)
    finally:
        write_json(args.output_dir / "elapsed.json", {"shard_seconds": time.perf_counter() - started})
    return 0


def workflow_elapsed():
    """Elapsed wall time through the final gate; GitHub records the final total."""
    url = (f"{os.environ['GITHUB_API_URL']}/repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
           f"{os.environ['GITHUB_RUN_ID']}/attempts/{os.environ['GITHUB_RUN_ATTEMPT']}")
    request = Request(url, headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                                    "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=30) as response:
        started = json.load(response)["run_started_at"]
    return (datetime.now(timezone.utc) - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()


def aggregate(args):
    lines = ["## Two-shard Windows pilot", f"Matrix result: {args.matrix_result}"]
    errors = []
    if args.matrix_result != "success":
        errors.append("Both Windows jobs must finish successfully (failure/cancellation/skip is not success).")
    try:
        plan, executions = verified_reports(args.reports_dir)
        lines.append(f"Exact-once coverage verified: {len(plan['nodeids'])} tests, {len(plan['modules'])} modules.")
        lines.append(f"Balancing: {plan['balancing']}; timed modules: {plan['historical_modules']}.")
        for index, result in sorted(executions.items()):
            lines.append(f"Shard {index}: {len(result['nodeids'])} collected/executed; "
                         f"pytest process {result['process_seconds']:.2f}s; phases {result['outcomes']}.")
        for path in sorted(args.reports_dir.rglob("elapsed.json")):
            lines.append(f"{path.parent.name}: collection + execution {read_json(path)['shard_seconds']:.2f}s.")
    except (ValueError, KeyError, OSError) as error:
        errors.append(str(error))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        try:
            lines.append(f"Workflow wall-clock elapsed through final gate: {workflow_elapsed():.2f}s.")
            lines.append("GitHub's run Summary records the final total, including the remaining job cleanup.")
        except (ValueError, KeyError, OSError) as error:
            errors.append(f"Could not record workflow elapsed time: {type(error).__name__}")
    lines.extend(f"ERROR: {error}" for error in errors)
    text = "\n\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write(text)
    return int(bool(errors))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("collect", collect_only), ("run", run_shard)):
        command = commands.add_parser(name)
        command.add_argument("--output-dir", type=Path, required=True)
        command.add_argument("--basetemp", type=Path, required=True,
                             help="Pilot-owned parent of the pytest-owned phase directories")
        command.add_argument("--timing-dir", type=Path)
        if name == "run":
            command.add_argument("--shard-index", type=int, choices=range(SHARD_COUNT), required=True)
        command.set_defaults(handler=handler)
    command = commands.add_parser("aggregate")
    command.add_argument("--reports-dir", type=Path, required=True)
    command.add_argument("--matrix-result", required=True)
    command.set_defaults(handler=aggregate)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ValueError, KeyError, OSError) as error:
        print(f"Pilot failed closed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
