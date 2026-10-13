"""Interrupted fresh validation cannot reuse an earlier batch's results."""

import pytest

from qt_lifecycle import qt_widget_lifecycle
from test_batch_profile_isolation import (
    page, change_profile, finish, pause_after_first, pump_until, validate,
)
from data_mask_studio.batch import BatchError, BatchFileStatus, BatchStructuralError
from data_mask_studio.gui.batch_worker import BatchValidationWorker


@pytest.fixture
def completed_batch(page, monkeypatch):
    app, _, widget, *_ = page
    validate(page)
    with monkeypatch.context() as scoped:
        pause = pause_after_first(page, scoped, "processing")
        widget.start_processing()
        worker = widget._processing_worker
        try:
            pump_until(app, pause.reached.is_set)
            change_profile(page, "update")
        finally:
            pause.release.set()
            assert worker.wait(10000)
            app.processEvents()
        finish(app, widget, "_processing_worker")
    assert all(item.status is BatchFileStatus.COMPLETED for item in widget.files)
    assert "Arquivos concluídos: 2" in widget.summary_output.toPlainText()
    assert widget._requires_revalidation and not widget.start_button.isEnabled()
    old_outputs = {item.output_path: item.output_path.read_bytes() for item in widget.files}
    assert len(old_outputs) == 2
    return page, old_outputs


@pytest.mark.parametrize("outcome", ["cancel", "failure"])
def test_interrupted_revalidation_cannot_publish_using_stale_completed_result(
    completed_batch, monkeypatch, outcome,
):
    page, old_outputs = completed_batch
    app, _, widget, _, _, output = page
    with monkeypatch.context() as scoped:
        pause = pause_after_first(page, scoped, "validation", fail=outcome == "failure")
        widget.validate_files()
        worker = widget._validation_worker
        try:
            pump_until(app, pause.reached.is_set)
            required_while_busy = widget._requires_revalidation
            if outcome == "cancel":
                widget.cancel()
        finally:
            pause.release.set()
            assert worker.wait(10000)
            app.processEvents()
        finish(app, widget, "_validation_worker")

    second_status = widget.files[1].status
    second_path = widget.files[1].output_path
    second_records = widget.files[1].records_processed
    required_after_terminal = widget._requires_revalidation
    terminal_state = widget.status_label.property("feedbackState")
    terminal_summary = widget.summary_output.toPlainText()
    second_output = output.parent / "fresh-output"
    second_output.mkdir()
    widget.output_field.setText(str(second_output))
    widget.start_processing()  # Handler must enforce the same gate as the button.
    incorrectly_started = widget._processing_worker is not None
    if incorrectly_started:
        # Pre-fix diagnostic: real publication of A only, while old B is counted.
        finish(app, widget, "_processing_worker")
    diagnostic = (
        f"started={incorrectly_started}; new_csvs={len(list(second_output.glob('*.csv')))}; "
        f"old_B_status={second_status.value}; old_B_path_retained={second_path is not None}; "
        f"summary={widget.summary_output.toPlainText()}"
    )
    assert not incorrectly_started, diagnostic
    assert required_while_busy and required_after_terminal
    assert widget.files[0].status is BatchFileStatus.COMPATIBLE
    assert widget.files[0].output_path is None and widget.files[0].records_processed == 0
    assert second_status is BatchFileStatus.PENDING
    assert second_path is None and second_records == 0
    assert all(item.processing_result is None for item in widget.files)
    assert not terminal_summary
    assert terminal_state == ("warning" if outcome == "cancel" else "error")
    assert not widget.start_button.isEnabled()
    assert not list(second_output.iterdir())
    assert all(path.read_bytes() == data for path, data in old_outputs.items())
    with pytest.raises(BatchError, match="Valide todos os arquivos"):
        widget._service.process(widget.files, widget._selected_profile(), second_output)

    # Explicit retry visits both files and uses the current (CHANGED) profile.
    validate(page)
    assert not widget._requires_revalidation and widget.start_button.isEnabled()
    assert all(item.output_path is None for item in widget.files)
    widget.start_processing()
    finish(app, widget, "_processing_worker")
    assert len(list(second_output.glob("*.csv"))) == 2
    assert all(item.status is BatchFileStatus.COMPLETED for item in widget.files)
    assert all(item.output_path.parent == second_output for item in widget.files)
    assert all("CHANGED-" in item.output_path.read_text(encoding="utf-8") for item in widget.files)
    assert "Arquivos concluídos: 2" in widget.summary_output.toPlainText()
    assert "Registros processados: 2" in widget.summary_output.toPlainText()
    assert widget.status_label.property("feedbackState") == "success"
    assert widget.overall_progress.value() == widget.overall_progress.maximum() == 2
    assert widget._output_directory == second_output and widget.open_output_button.isEnabled()
    assert all(path.read_bytes() == data for path, data in old_outputs.items())


def test_new_validation_resets_every_result_before_worker_starts(completed_batch, monkeypatch):
    page, old_outputs = completed_batch
    app, _, widget, *_ = page
    monkeypatch.setattr(BatchValidationWorker, "start", lambda self: None)
    widget.validate_files()
    worker = widget._validation_worker
    assert worker is not None
    try:
        assert widget._requires_revalidation
        assert all(item.status is BatchFileStatus.PENDING for item in widget.files)
        assert all(item.output_path is None and item.processing_result is None for item in widget.files)
        assert all(item.records_processed == item.new_mappings == item.updated_mappings == 0
                   for item in widget.files)
        assert all(not item.headers and not item.result_message and not item.normalization_fallbacks
                   for item in widget.files)
        assert all(item.column_count is None and item.encoding is None and item.error_type is None
                   for item in widget.files)
        assert all(widget.file_table.item(row, 2).text() == "Aguardando validação" for row in range(2))
        assert not widget.summary_output.toPlainText()
        assert (widget.overall_progress.maximum(), widget.overall_progress.value()) == (1, 0)
        assert widget.status_label.text() == "Validando arquivos..."
        assert not widget.start_button.isEnabled()
        assert all(path.read_bytes() == data for path, data in old_outputs.items())
    finally:
        worker.finished.emit()  # No completed signal: no validation was performed.
        app.processEvents()
    assert widget._validation_worker is None
    assert widget._requires_revalidation and not widget.start_button.isEnabled()


def test_failure_after_visiting_all_files_does_not_release_required_validation(page, monkeypatch):
    app, _, widget, *_ = page
    original = widget._service.validate

    def fail_after_visits(*args, **kwargs):
        original(*args, **kwargs)
        raise BatchStructuralError("Falha sintética ao concluir a validação.")

    with monkeypatch.context() as scoped:
        scoped.setattr(widget._service, "validate", fail_after_visits)
        widget.validate_files()
        worker = widget._validation_worker
        assert worker.wait(10000)
        app.processEvents()
        finish(app, widget, "_validation_worker")
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert widget.status_label.property("feedbackState") == "error"
    assert widget._requires_revalidation and not widget.start_button.isEnabled()
    widget.start_processing()
    assert widget._processing_worker is None
    validate(page)
    assert not widget._requires_revalidation and widget.start_button.isEnabled()


@pytest.mark.parametrize("profile_changed", [False, True])
def test_validation_gate_waits_for_queued_terminal_signals_and_deferred_refresh(page, profile_changed):
    app, _, widget, *_ = page
    widget.validate_files()
    worker = widget._validation_worker
    assert worker.wait(10000)
    assert not worker.isRunning()
    assert widget._validation_worker is worker  # Terminal signals are still queued.
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert widget._requires_revalidation and not widget.start_button.isEnabled()
    widget.start_processing()
    assert widget._processing_worker is None
    if profile_changed:
        change_profile(page, "update")
    app.processEvents()
    finish(app, widget, "_validation_worker")
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert widget._requires_revalidation == profile_changed
    assert widget.start_button.isEnabled() == (not profile_changed)
    if profile_changed:
        assert widget._selected_profile().columns[0].prefix == "CHANGED"
        validate(page)
        assert not widget._requires_revalidation and widget.start_button.isEnabled()
