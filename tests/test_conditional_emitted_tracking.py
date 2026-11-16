"""Opt-out removes whole-file token retention, not vault writes or publication."""

import builtins
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
from pathlib import Path
import sqlite3

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction, ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.batch import BatchFile, BatchFileStatus, BatchService
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools import csv_anonymizer as module
from data_mask_studio.gui import anonymization_worker as worker_module
from data_mask_studio.gui.batch_worker import BatchProcessingWorker
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing import build_processing_plan
from data_mask_studio.transfer_package import read_package
from data_mask_studio.transfer_package.staging import PackageStagingRequest
from test_composite_csv import setup_case, KEY
from test_batch import CountingRepository, FixedKeyProvider, make_profile_service, validate_all, write_csv
import test_composite_batch as composite_batch


ROWS = (("Synthetic Alice", "999.999.999-99", "OTHER-ABCDEFGHIJKL"),
        ("SYNTHETIC ALICE", "99999999999", "OTHER-ABCDEFGHIJKL"),
        ("Synthetic Bob", "88888888888", "OTHER-ABCDEFGHIJKL"),
        (" ", " ", "OTHER-ABCDEFGHIJKL"))


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def case(directory, kind, rows=ROWS):
    directory.mkdir(parents=True, exist_ok=True)
    scalar = kind != "composite"
    configs = [ColumnConfig("NOME", action=ColumnAction.MASK if scalar else ColumnAction.EXCLUDE,
                            prefix="NAME" if scalar else "", normalization_rule=NormalizationRule.PERSON_NAME),
               ColumnConfig("CPF", action=ColumnAction.EXCLUDE), ColumnConfig("IDADE")]
    source, destination, repository, options = setup_case(directory, rows, configs)
    if kind == "scalar":
        options["processing_plan"] = build_processing_plan(inspect_csv(source), configs)
    return source, destination, repository, options, configs


def observe_trackers(monkeypatch):
    trackers, ordered = [], []

    class Tracker(set):
        peak = 0
        updates = 0

        def update(self, values):
            super().update(values)
            self.updates += 1
            self.peak = max(self.peak, len(self))

    def create():
        tracker = Tracker()
        trackers.append(tracker)
        return tracker

    def sort(values, *args, **kwargs):
        if isinstance(values, Tracker):
            ordered.append(len(values))
        return builtins.sorted(values, *args, **kwargs)

    # Only this module's two execution-local sets/sorts. Do not mock token
    # generation, vault operations, package selection or builtins globally.
    monkeypatch.setattr(module, "set", create, raising=False)
    monkeypatch.setattr(module, "sorted", sort, raising=False)
    return trackers, ordered


def cardinality_case(tmp_path, monkeypatch, kind, count, options):
    rows = tuple((f"Synthetic Person {index}", f"{index:011d}", "OTHER-ABCDEFGHIJKL") for index in range(count))
    source, output, _, kwargs, _ = case(tmp_path, kind, rows)
    trackers, ordered = observe_trackers(monkeypatch)
    observed = []
    result = module.anonymize_csv(source, output, **kwargs, mapping_batch_size=8,
        progress_callback=lambda _: observed.append(tuple(len(tracker) for tracker in trackers)), **options)
    assert len(trackers) == 2 and len(observed) == count
    return result, trackers, ordered, observed


@pytest.mark.parametrize("kind", ["scalar", "composite", "mixed"])
@pytest.mark.parametrize("count", [32, 128])
def test_public_default_still_tracks_complete_unique_codes(tmp_path, monkeypatch, kind, count):
    result, trackers, ordered, observed = cardinality_case(tmp_path, monkeypatch, kind, count, {})
    expected = (count if kind != "composite" else 0, count if kind != "scalar" else 0)
    assert tuple(tracker.peak for tracker in trackers) == expected
    assert observed[-1] == expected and tuple(ordered) == expected
    assert (len(result.emitted_scalar_codes), len(result.emitted_composite_codes)) == expected
    print(f"{kind}: {count} rows; peak emitted-set cardinalities={expected}")


@pytest.mark.parametrize("kind", ["scalar", "composite", "mixed"])
@pytest.mark.parametrize("tracking", [False, True])
def test_explicit_opt_out_never_populates_or_sorts_collectors(tmp_path, monkeypatch, kind, tracking):
    result, trackers, ordered, observed = cardinality_case(tmp_path, monkeypatch, kind, 128,
                                                         {"track_emitted_codes": tracking})
    expected = (128 if kind != "composite" else 0, 128 if kind != "scalar" else 0) if tracking else (0, 0)
    assert tuple(tracker.peak for tracker in trackers) == expected
    assert observed[-1] == expected
    assert (len(result.emitted_scalar_codes), len(result.emitted_composite_codes)) == expected
    if not tracking:
        assert all(value == (0, 0) for value in observed)
        assert not any(tracker.updates for tracker in trackers)
        assert ordered == []


def semantic_vault(repository):
    def clean(value):
        if isinstance(value, dict):
            # Fresh encryption uses random nonces/variation UUIDs and observation
            # times. Compare authenticated identities, values, rules and counts.
            return {key: clean(item) for key, item in value.items()
                    if key not in {"first_seen", "last_seen", "identifier"}}
        if isinstance(value, (tuple, list)):
            return tuple(clean(item) for item in value)
        return value

    with closing(sqlite3.connect(repository.database_path)) as connection:
        codes = tuple(row[0] for row in connection.execute(
            "SELECT code FROM vault_mappings UNION ALL SELECT code FROM composite_mappings ORDER BY code"))
    return tuple(clean(asdict(repository.get_mapping_for_inspection(code))) for code in codes)


@pytest.mark.parametrize("kind", ["scalar", "composite", "mixed"])
def test_opt_out_preserves_csv_binding_counters_and_authenticated_mappings(tmp_path, monkeypatch, kind):
    results, outputs, mappings = [], [], []
    for tracking in (True, False):
        source, output, repository, kwargs, _ = case(tmp_path / str(tracking), kind)
        outcomes = []
        transaction = repository.transaction

        @contextmanager
        def observe_transaction():
            with transaction() as current:
                yield current
            outcomes.append(current.publication_outcome)

        monkeypatch.setattr(repository, "transaction", observe_transaction)
        result = module.anonymize_csv(source, output, **kwargs, track_emitted_codes=tracking, mapping_batch_size=1)
        assert outcomes == ["committed"]
        outputs.append(output.read_bytes())
        mappings.append(semantic_vault(repository))
        results.append(replace(result, output_path=Path("output.csv"), duration_seconds=0,
                               emitted_scalar_codes=(), emitted_composite_codes=()))
    assert outputs[0] == outputs[1] and mappings[0] == mappings[1] and results[0] == results[1]


@pytest.mark.parametrize("package", [False, True])
def test_individual_gui_worker_selects_tracking_policy_and_retains_real_outputs(tmp_path, monkeypatch, package):
    app = create_application([])
    source, output, repository, kwargs, configs = case(tmp_path, "mixed")
    request = PackageStagingRequest("synthetic tracking password") if package else None
    observed = []
    original = worker_module.anonymize_csv

    def run(*args, **options):
        observed.append(options.get("track_emitted_codes", True))
        return original(*args, **options)

    monkeypatch.setattr(worker_module, "anonymize_csv", run)

    class Key:
        def get_key(self):
            return KEY

    worker = worker_module.AnonymizationWorker(inspect_csv(source), str(output), configs, Key(), lambda: repository,
        overwrite=False, processing_plan=kwargs["processing_plan"], transfer_package_request=request,
        transfer_package_destination=output.with_suffix(".dmspackage") if package else None)
    completed, failed = QSignalSpy(worker.completed), QSignalSpy(worker.failed)
    worker.start()
    assert worker.wait(10000)
    app.processEvents()
    assert failed.count() == 0 and completed.count() == 1 and observed == [package]
    result = completed.at(0)[0]
    assert output.is_file() and result.records_processed == len(ROWS)
    if package:
        payload = read_package(result.transfer_package_path, request.password)
        assert tuple(item.code for item in payload.scalar_mappings) == result.emitted_scalar_codes
        assert tuple(item.code for item in payload.composite_mappings) == result.emitted_composite_codes
        assert payload.masked_file == result.masked_file_binding
    else:
        assert result.emitted_scalar_codes == result.emitted_composite_codes == ()
    assert worker._transfer_package_request is None
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(worker, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("gui_worker", [False, True], ids=["public-default", "gui-opt-out"])
def test_batch_retains_statistics_and_results_without_unneeded_code_collections(tmp_path, monkeypatch, gui_worker):
    app = create_application([])
    profiles, profile = make_profile_service(tmp_path)
    files = [BatchFile(tmp_path / name) for name in ("first.csv", "second.csv")]
    for item in files:
        write_csv(item.path)
    validate_all(files, profiles, profile)
    output = tmp_path / "output"
    output.mkdir()
    repository = CountingRepository(tmp_path / "vault.db")
    service = BatchService(profiles)
    if gui_worker:
        observed = []
        process = service.process

        def run(*args, **options):
            observed.append(options.get("track_emitted_codes", True))
            return process(*args, **options)

        monkeypatch.setattr(service, "process", run)
        worker = BatchProcessingWorker(service, files, profile, str(output), FixedKeyProvider(), lambda: repository)
        completed, failed = QSignalSpy(worker.completed), QSignalSpy(worker.failed)
        worker.start()
        assert worker.wait(10000)
        app.processEvents()
        assert failed.count() == 0 and completed.count() == 1 and observed == [False]
        summary = completed.at(0)[0]
        worker.deleteLater()
        QCoreApplication.sendPostedEvents(worker, QEvent.Type.DeferredDelete)
    else:
        summary = service.process(files, profile, output, FixedKeyProvider(), lambda: repository)
    assert repository.transaction_count == summary.completed_files == 2
    assert summary.records_processed == 2 and len(summary.results) == 2
    assert summary.new_mappings == summary.updated_mappings == 2
    for item, result in zip(files, summary.results):
        assert item.status is BatchFileStatus.COMPLETED and item.output_path.is_file()
        assert result.processing_result is item.processing_result
        assert bool(item.processing_result.emitted_scalar_codes) is not gui_worker
        assert item.processing_result.emitted_composite_codes == ()


@pytest.mark.parametrize("scalar", [False, True], ids=["composite-only", "mixed"])
@pytest.mark.parametrize("tracking", [True, False])
def test_batch_composite_results_respect_tracking_without_changing_outputs(tmp_path, scalar, tracking):
    configs = [ColumnConfig("NOME", action=ColumnAction.EXCLUDE),
               ColumnConfig("CPF", action=ColumnAction.MASK if scalar else ColumnAction.EXCLUDE, prefix="DOC"),
               ColumnConfig("IDADE")]
    profiles, profile = composite_batch.setup_profile(tmp_path, configs=configs, multiple=True)
    source = composite_batch.write_csv(tmp_path / "input.csv", ["NOME", "CPF", "IDADE"],
                                       [["Synthetic Alice", "99999999999", "24"]])
    files, summary, resources = composite_batch.process(tmp_path, profiles, profile, [source],
                                                       track_emitted_codes=tracking)
    item = files[0]
    result = summary.results[0].processing_result
    assert item.status is BatchFileStatus.COMPLETED and summary.completed_files == 1
    assert result is item.processing_result and item.output_path.is_file()
    headers, row = composite_batch.read_output(item)
    assert headers == (["CPF", "IDADE", "PESSOA", "OUTRA"] if scalar else ["IDADE", "PESSOA", "OUTRA"])
    assert result.composite_tokens_generated == result.composite_mapping_occurrences == 2
    assert result.scalar_mapping_occurrences == resources.repository.count() == int(scalar)
    assert all(resources.repository.composite_repository().get_composite_mapping(code) for code in row[-2:])
    assert result.emitted_composite_codes == (tuple(sorted(row[-2:])) if tracking else ())
    assert result.emitted_scalar_codes == ((row[0],) if tracking and scalar else ())
