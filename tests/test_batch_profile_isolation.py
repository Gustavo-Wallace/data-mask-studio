"""Profile notifications must not mutate files owned by an active Qt worker."""

import os
from dataclasses import dataclass
from threading import Event
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest

from qt_lifecycle import qt_widget_lifecycle
from test_main_navigation import build_window
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.batch import BatchFileStatus, BatchStructuralError
import data_mask_studio.batch.service as batch_service
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    window = build_window(tmp_path)
    profiles = window._profile_service
    profile = profiles.create("Original", [ColumnConfig("Name", True, "NAME")])
    widget = window.batch_widget
    widget.refresh_profiles()
    for name in ("first.csv", "second.csv"):
        path = tmp_path / name
        path.write_text("Name\nSYNTHETIC\n", encoding="utf-8")
        widget.add_paths([path])
    output = tmp_path / "output"
    output.mkdir()
    widget.output_field.setText(str(output))
    return app, window, widget, profiles, profile, output


def pump_until(app, predicate):
    deadline = monotonic() + 10
    while not predicate():
        assert monotonic() < deadline, "Worker did not reach the synchronization point"
        app.processEvents()
        QTest.qWait(1)
    app.processEvents()


def finish(app, widget, attribute):
    worker = getattr(widget, attribute)
    if worker is not None:
        # Join without tight Qt polling contending with the Python worker.
        # Keep the existing 10-second bound and distinguish a running thread
        # from a finished thread whose queued GUI cleanup did not execute.
        assert worker.wait(10000), f"{attribute} is still running after 10 seconds"
    app.processEvents()
    assert getattr(widget, attribute) is None, (
        f"{attribute} finished, but queued GUI cleanup did not clear its reference"
    )


def validate(page):
    app, _, widget, *_ = page
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)


@pytest.mark.parametrize("phase", ["validation", "processing"])
def test_finish_joins_real_worker_then_delivers_queued_gui_cleanup(page, monkeypatch, phase):
    app, _, widget, *_ = page
    if phase == "processing":
        validate(page)
    attribute = "_validation_worker" if phase == "validation" else "_processing_worker"
    (widget.validate_files if phase == "validation" else widget.start_processing)()
    worker = getattr(widget, attribute)
    joined = []
    original_wait = worker.wait

    def wait_for_thread(timeout):
        stopped = original_wait(timeout)
        joined.append((timeout, stopped, getattr(widget, attribute) is worker))
        return stopped

    monkeypatch.setattr(worker, "wait", wait_for_thread)
    finish(app, widget, attribute)
    # Completion must not depend on a tight GUI polling loop. The real thread
    # stops first; its reference is cleared only by queued production slots.
    assert joined == [(10000, True, True)]
    assert getattr(widget, attribute) is None
    expected = (BatchFileStatus.COMPATIBLE if phase == "validation"
                else BatchFileStatus.COMPLETED)
    assert all(item.status is expected for item in widget.files)


@dataclass
class Pause:
    reached: Event
    release: Event


def pause_after_first(page, monkeypatch, phase, *, fail=False):
    _, _, widget, *_ = page
    method = "validate" if phase == "validation" else "process"
    original = getattr(widget._service, method)
    pause = Pause(Event(), Event())
    terminal = (BatchFileStatus.COMPATIBLE if phase == "validation"
                else BatchFileStatus.COMPLETED)

    def synchronized(*args, file_callback, **kwargs):
        def report(item):
            file_callback(item)  # Real QThread signals, delivered on the GUI thread.
            if item is widget.files[0] and item.status is terminal:
                pause.reached.set()
                assert pause.release.wait(10), "Test did not release the worker"
                if fail:
                    raise BatchStructuralError("Falha estrutural sintética.")
        return original(*args, file_callback=report, **kwargs)

    monkeypatch.setattr(widget._service, method, synchronized)
    return pause


def change_profile(page, change, *, navigation=False):
    _, window, _, profiles, profile, _ = page
    if change == "update":
        profiles.update(profile.identifier, [ColumnConfig("Name", True, "CHANGED")])
    elif change == "delete":
        profiles.delete(profile.identifier)
    if navigation:
        window.set_current_page(0)
        window.set_current_page(1)  # MainWindow's actual navigation refresh.
    else:
        window.anonymization_widget.profiles_changed.emit()


@pytest.mark.parametrize("change", ["update", "delete"])
def test_profile_change_between_publications_preserves_both_real_outputs(page, monkeypatch, change):
    app, _, widget, _, profile, output = page
    validate(page)
    pause = pause_after_first(page, monkeypatch, "processing")
    summaries = []
    widget.start_processing()
    worker = widget._processing_worker
    worker.completed.connect(summaries.append)
    try:
        pump_until(app, pause.reached.is_set)
        first_output = widget.files[0].output_path
        change_profile(page, change)
        assert widget.files[0].status is BatchFileStatus.COMPLETED
        assert widget.files[0].output_path == first_output
        assert worker._profile == profile
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_processing_worker")

    assert len(list(output.glob("*.csv"))) == 2
    assert summaries[0].completed_files == 2
    assert summaries[0].records_processed == 2
    assert summaries[0].new_mappings == summaries[0].updated_mappings == 1
    assert all(item.status is BatchFileStatus.COMPLETED for item in widget.files)
    assert all(item.output_path and item.output_path.is_file() for item in widget.files)
    assert all(result.output_path for result in summaries[0].results)
    assert all("NAME-" in item.output_path.read_text(encoding="utf-8")
               for item in widget.files)  # Frozen original profile, not CHANGED.
    assert "Arquivos concluídos: 2" in widget.summary_output.toPlainText()
    assert widget.overall_progress.value() == widget.overall_progress.maximum() == 2
    assert widget.open_output_button.isEnabled()
    assert widget._output_directory == output
    feedback = (widget.summary_output.toPlainText(), widget.status_label.text())
    widget.refresh_profiles()
    assert feedback == (widget.summary_output.toPlainText(), widget.status_label.text())
    assert all(item.output_path and item.output_path.is_file() for item in widget.files)
    assert not widget.start_button.isEnabled()
    current = widget._selected_profile()
    if change == "delete":
        assert current is None
    else:
        assert current.columns[0].prefix == "CHANGED"


@pytest.mark.parametrize("change", ["update", "delete"])
def test_profile_change_during_validation_preserves_completed_validation(page, monkeypatch, change):
    app, _, widget, *_ = page
    pause = pause_after_first(page, monkeypatch, "validation")
    widget.validate_files()
    worker = widget._validation_worker
    try:
        pump_until(app, pause.reached.is_set)
        change_profile(page, change)
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_validation_worker")

    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert "2 compatível(is)" in widget.status_label.text()
    assert not widget.start_button.isEnabled()  # These results used the old profile.
    if change == "update":
        validate(page)
        assert widget.start_button.isEnabled()
    else:
        assert widget._selected_profile() is None
        assert not widget.validate_button.isEnabled()


@pytest.mark.parametrize("change", ["update", "delete"])
def test_idle_profile_refresh_still_invalidates_validation(page, change):
    _, _, widget, *_ = page
    validate(page)
    change_profile(page, change)
    assert all(item.status is BatchFileStatus.PENDING for item in widget.files)
    assert not widget.start_button.isEnabled()


@pytest.mark.parametrize("phase", ["validation", "processing"])
@pytest.mark.parametrize("change", ["unchanged", "update", "delete"])
def test_busy_navigation_refresh_preserves_worker_state(page, monkeypatch, phase, change):
    app, window, widget, _, profile, _ = page
    if phase == "processing":
        validate(page)
    window.set_current_page(1)
    pause = pause_after_first(page, monkeypatch, phase)
    attribute = "_validation_worker" if phase == "validation" else "_processing_worker"
    (widget.validate_files if phase == "validation" else widget.start_processing)()
    worker = getattr(widget, attribute)
    try:
        pump_until(app, pause.reached.is_set)
        snapshot = [(item.status, item.output_path, item.records_processed,
                     item.headers, item.result_message) for item in widget.files]
        change_profile(page, change, navigation=True)
        widget.validate_files()  # Queued/reentrant actions cannot replace the worker.
        widget.start_processing()
        assert getattr(widget, attribute) is worker
        assert widget._selected_profile() == profile
        assert snapshot == [(item.status, item.output_path, item.records_processed,
                             item.headers, item.result_message) for item in widget.files]
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, attribute)
    expected = (BatchFileStatus.COMPATIBLE if phase == "validation"
                else BatchFileStatus.COMPLETED)
    assert all(item.status is expected for item in widget.files)
    if phase == "validation":
        assert widget.start_button.isEnabled() == (change == "unchanged")


@pytest.mark.parametrize("phase", ["validation", "processing"])
@pytest.mark.parametrize("change", ["update", "delete"])
def test_refresh_after_thread_returns_but_before_queued_finish_is_safe(page, phase, change):
    app, _, widget, *_ = page
    if phase == "processing":
        validate(page)
    attribute = "_validation_worker" if phase == "validation" else "_processing_worker"
    (widget.validate_files if phase == "validation" else widget.start_processing)()
    worker = getattr(widget, attribute)
    assert worker.wait(10000)
    assert not worker.isRunning()
    assert getattr(widget, attribute) is worker  # finished() is still queued.
    change_profile(page, change)
    app.processEvents()
    finish(app, widget, attribute)
    expected = (BatchFileStatus.COMPATIBLE if phase == "validation"
                else BatchFileStatus.COMPLETED)
    assert all(item.status is expected for item in widget.files)
    assert not widget.start_button.isEnabled()
    if phase == "processing":
        assert all(item.output_path and item.output_path.is_file() for item in widget.files)
        assert "Arquivos concluídos: 2" in widget.summary_output.toPlainText()


@pytest.mark.parametrize("change", ["update", "delete"])
@pytest.mark.parametrize("outcome", ["cancel", "item_failure", "worker_failure"])
def test_deferred_refresh_keeps_partial_publication_on_failure_or_cancel(
    page, monkeypatch, change, outcome,
):
    app, _, widget, _, _, output = page
    validate(page)
    pause = pause_after_first(page, monkeypatch, "processing", fail=outcome == "worker_failure")
    if outcome == "item_failure":
        original = batch_service.anonymize_csv

        def fail_second(source, *args, **kwargs):
            if source == widget.files[1].path:
                raise BatchStructuralError("Falha estrutural sintética.")
            return original(source, *args, **kwargs)

        monkeypatch.setattr(batch_service, "anonymize_csv", fail_second)
    widget.start_processing()
    worker = widget._processing_worker
    try:
        pump_until(app, pause.reached.is_set)
        change_profile(page, change)
        if outcome == "cancel":
            widget.cancel()
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_processing_worker")
    first = widget.files[0]
    assert first.status is BatchFileStatus.COMPLETED
    assert first.records_processed == 1
    assert first.output_path and first.output_path.is_file()
    assert widget.open_output_button.isEnabled()
    assert widget._output_directory == output
    assert len(list(output.glob("*.csv"))) == 1
    assert widget.status_label.property("feedbackState") == (
        "warning" if outcome == "cancel" else "error"
    )
    assert not widget.start_button.isEnabled()
    if outcome != "worker_failure":
        assert "Arquivos concluídos: 1" in widget.summary_output.toPlainText()
        assert widget.files[1].status is (
            BatchFileStatus.CANCELLED if outcome == "cancel" else BatchFileStatus.ERROR
        )


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
@pytest.mark.parametrize("change", ["update", "delete"])
def test_validation_failure_or_cancel_still_applies_deferred_profiles(page, monkeypatch, outcome, change):
    app, _, widget, *_ = page
    pause = pause_after_first(page, monkeypatch, "validation", fail=outcome == "failure")
    widget.validate_files()
    worker = widget._validation_worker
    try:
        pump_until(app, pause.reached.is_set)
        change_profile(page, change)
        if outcome == "cancel":
            widget.cancel()
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_validation_worker")
    assert widget.files[0].status is BatchFileStatus.COMPATIBLE
    assert widget.files[1].status is BatchFileStatus.PENDING
    assert not widget.start_button.isEnabled()
    assert not widget.open_output_button.isEnabled()
    current = widget._selected_profile()
    if change == "delete":
        assert current is None
    else:
        assert current.columns[0].prefix == "CHANGED"
    if outcome == "failure":
        assert widget.status_label.property("feedbackState") == "error"


def test_deleted_profile_cannot_silently_reuse_alternative_for_new_run(page, monkeypatch):
    app, _, widget, profiles, _, _ = page
    profiles.create("Replacement", [ColumnConfig("Name", True, "OTHER")])
    pause = pause_after_first(page, monkeypatch, "validation")
    widget.validate_files()
    worker = widget._validation_worker
    try:
        pump_until(app, pause.reached.is_set)
        change_profile(page, "delete")
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_validation_worker")
    assert widget._selected_profile().name == "Replacement"
    assert not widget.start_button.isEnabled()
    widget.start_processing()  # Backend entry cannot bypass revalidation either.
    assert widget._processing_worker is None
    assert "Valide todos os arquivos" in widget.status_label.text()
    validate(page)
    assert widget.start_button.isEnabled()


def test_multiple_notifications_reload_latest_profile_and_require_fresh_validation(page, monkeypatch):
    app, _, widget, profiles, profile, output = page
    pause = pause_after_first(page, monkeypatch, "validation")
    widget.validate_files()
    worker = widget._validation_worker
    try:
        pump_until(app, pause.reached.is_set)
        change_profile(page, "update")
        profiles.update(profile.identifier, [ColumnConfig("Name", True, "LATEST")])
        change_profile(page, "unchanged", navigation=True)
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_validation_worker")
    assert widget._selected_profile().columns[0].prefix == "LATEST"
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert not widget.start_button.isEnabled()
    widget.start_processing()
    assert widget._processing_worker is None
    assert not list(output.glob("*.csv"))
    validate(page)
    widget.start_processing()
    finish(app, widget, "_processing_worker")
    assert "Arquivos concluídos: 2" in widget.summary_output.toPlainText()
    assert all("LATEST-" in item.output_path.read_text(encoding="utf-8") for item in widget.files)
    widget.refresh_profiles()  # The deferred notification is fully consumed.
    assert all(item.status is BatchFileStatus.COMPLETED for item in widget.files)
    assert all(item.output_path.is_file() for item in widget.files)


def test_profile_selector_signal_while_busy_cannot_invalidate_worker_files(page, monkeypatch):
    app, _, widget, profiles, profile, _ = page
    replacement = profiles.create("Replacement", [ColumnConfig("Name", True, "OTHER")])
    widget.refresh_profiles()
    pause = pause_after_first(page, monkeypatch, "validation")
    widget.validate_files()
    worker = widget._validation_worker
    try:
        pump_until(app, pause.reached.is_set)
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData(replacement.identifier))
        assert widget.files[0].status is BatchFileStatus.COMPATIBLE
        assert worker._profile == profile
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_validation_worker")
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert not widget.start_button.isEnabled()
    validate(page)
    assert widget.start_button.isEnabled()


def test_composite_only_profile_update_also_requires_fresh_validation(page, monkeypatch):
    app, window, widget, profiles, profile, _ = page
    configurations = [ColumnConfig("Name", True, "NAME"), ColumnConfig("City")]
    for item in widget.files:
        item.path.write_text("Name,City\nSYNTHETIC,TEST CITY\n", encoding="utf-8")
    profiles.update(profile.identifier, configurations)
    widget.refresh_profiles()
    pause = pause_after_first(page, monkeypatch, "validation")
    widget.validate_files()
    worker = widget._validation_worker
    try:
        pump_until(app, pause.reached.is_set)
        composite = CompositeColumnConfig("Combined", components=(
            CompositeSource(SourceColumnRef("Name")),
            CompositeSource(SourceColumnRef("City")),
        ))
        profiles.update(profile.identifier, configurations, composites=[composite])
        window.anonymization_widget.profiles_changed.emit()
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_validation_worker")
    assert widget._selected_profile().composites == (composite,)
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert not widget.start_button.isEnabled()
    validate(page)
    assert widget.start_button.isEnabled()
