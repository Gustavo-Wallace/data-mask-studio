"""Current-file lifecycle, separate from overall counters and inline styling."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.batch_restoration import (
    BatchCSVColumn, BatchRestorationError, BatchRestorationProgress,
    BatchRestorationStatus, BatchRestorationStructuralError, BatchRestorationSummary,
)
from data_mask_studio.gui.batch_restoration_widget import BatchRestorationWidget
from data_mask_studio.gui.batch_restoration_worker import (
    BatchRestorationAnalysisWorker, BatchRestorationProcessingWorker,
)


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated"))
    app = create_application([])
    widget = BatchRestorationWidget(lambda: pytest.fail("Signal tests must not access the vault"))
    sources = [tmp_path / "a.csv", tmp_path / "b.csv"]
    for source in sources:
        source.write_text("CPF\nSYNTHETIC\n", encoding="utf-8")
    widget.add_paths(sources)
    output = tmp_path / "output"
    output.mkdir()
    widget.output_field.setText(str(output))
    monkeypatch.setattr(BatchRestorationAnalysisWorker, "start", lambda self: None)
    monkeypatch.setattr(BatchRestorationProcessingWorker, "start", lambda self: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    return app, widget, output


def mark_compatible(widget):
    for item in widget.files:
        item.status = BatchRestorationStatus.COMPATIBLE
        item.columns = [BatchCSVColumn(0, "CPF", 1, 1, 0, selected=True)]


def assert_idle(widget):
    assert (widget.current_progress.minimum(), widget.current_progress.maximum()) == (0, 1)
    assert widget.current_progress.value() == 0
    assert widget.current_progress_label.text() == "Nenhum processamento em andamento."


def overall_state(widget):
    return (widget.overall_progress.minimum(), widget.overall_progress.maximum(),
            widget.overall_progress.value())


def start(widget, phase):
    if phase == "analysis":
        widget.analyze_files()
        worker = widget._analysis_worker
    else:
        mark_compatible(widget)
        widget.start_restoration()
        worker = widget._processing_worker
    assert worker is not None
    return worker


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_analysis_start_and_terminal_signals_finalize_current_bar_without_changing_overall(page, outcome):
    app, widget, _ = page
    busy = []
    widget.busy_changed.connect(busy.append)
    worker = start(widget, "analysis")
    assert overall_state(widget) == (0, 2, 0)
    assert widget.current_progress.maximum() == 0
    assert not widget.analyze_button.isEnabled()
    worker.progress.emit(1, 2)
    if outcome == "success":
        mark_compatible(widget)
        worker.progress.emit(2, 2)
        worker.completed.emit()
        expected = (0, 2, 2)
    elif outcome == "failure":
        worker.failed.emit(BatchRestorationError("Erro seguro no lote."))
        expected = (0, 2, 1)
    else:
        widget.cancel()
        assert worker._cancellation.is_set()
        worker.cancelled.emit()
        expected = (0, 2, 1)
    assert_idle(widget)
    assert overall_state(widget) == expected
    status = widget.status_label.text()
    state = widget.status_label.property("feedbackState")
    worker.finished.emit()
    app.processEvents()
    assert widget._analysis_worker is None
    assert_idle(widget)
    assert overall_state(widget) == expected
    assert widget.status_label.text() == status
    assert widget.status_label.property("feedbackState") == state == {
        "success": "success", "failure": "error", "cancel": "warning",
    }[outcome]
    assert widget.analyze_button.isEnabled() and busy == [True, False]


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
@pytest.mark.parametrize("current_total", [0, 10])
def test_restoration_start_and_terminal_states_reset_unknown_or_partial_file_progress(page, outcome, current_total):
    app, widget, output = page
    widget.current_progress.setRange(0, 10)
    widget.current_progress.setValue(10)
    widget.current_progress_label.setText("OLD_FILE_SENTINEL")
    worker = start(widget, "restoration")
    assert overall_state(widget) == (0, 2, 0)
    assert (widget.current_progress.minimum(), widget.current_progress.maximum()) == (0, 0)
    assert widget.current_progress.value() == 0
    assert widget.current_progress_label.text() == ""
    worker.progress.emit(BatchRestorationProgress(2, 2, 1, 0, "b.csv", 3, current_total))
    if current_total:
        assert (widget.current_progress.maximum(), widget.current_progress.value()) == (10, 3)
    else:
        assert widget.current_progress.maximum() == 0
    assert "b.csv" in widget.current_progress_label.text()
    if outcome == "failure":
        worker.failed.emit(BatchRestorationError("Erro seguro no lote."))
    else:
        if outcome == "cancel":
            widget.cancel()
            assert worker._cancellation.is_set()
        worker.completed.emit(BatchRestorationSummary(
            2, 2 if outcome == "success" else 1, 0, 0,
            int(outcome == "cancel"), 3, 0, output, 0.1, cancelled=outcome == "cancel",
        ))
    assert_idle(widget)
    expected = (0, 2, 2 if outcome == "success" else 1)
    assert overall_state(widget) == expected
    status = widget.status_label.text()
    worker.finished.emit()
    app.processEvents()
    assert widget._processing_worker is None
    assert_idle(widget)
    assert overall_state(widget) == expected
    assert widget.status_label.text() == status
    assert widget.status_label.property("feedbackState") == {
        "success": "success", "failure": "error", "cancel": "warning",
    }[outcome]


def test_consecutive_analysis_and_restoration_do_not_inherit_progress_or_file_labels(page):
    app, widget, output = page
    operations = [("analysis", "success"), ("restoration", "success"),
                  ("analysis", "cancel"), ("restoration", "failure"),
                  ("analysis", "failure"), ("restoration", "cancel"),
                  ("analysis", "success"), ("restoration", "success")]
    for phase, outcome in operations:
        worker = start(widget, phase)
        assert overall_state(widget) == (0, 2, 0)
        assert (widget.current_progress.minimum(), widget.current_progress.maximum()) == (0, 0)
        assert widget.current_progress.value() == 0 and widget.current_progress_label.text() == ""
        if phase == "analysis":
            worker.progress.emit(1, 2)
        else:
            worker.progress.emit(BatchRestorationProgress(2, 2, 1, 0, "b.csv", 3, 10))
        if outcome == "failure":
            worker.failed.emit(BatchRestorationError("Erro seguro no lote."))
        elif phase == "analysis":
            if outcome == "cancel":
                worker.cancelled.emit()
            else:
                mark_compatible(widget)
                worker.progress.emit(2, 2)
                worker.completed.emit()
        else:
            worker.completed.emit(BatchRestorationSummary(
                2, 2 if outcome == "success" else 1, 0, 0,
                int(outcome == "cancel"), 3, 0, output, 0.1, cancelled=outcome == "cancel",
            ))
        worker.finished.emit()
        app.processEvents()
        assert widget._analysis_worker is None and widget._processing_worker is None
        assert_idle(widget)
        assert overall_state(widget) == (0, 2, 2 if outcome == "success" else 1)


@pytest.mark.parametrize("phase", ["analysis", "restoration"])
def test_finished_signal_alone_also_restores_idle_without_faking_completion(page, phase):
    app, widget, _ = page
    worker = start(widget, phase)
    worker.finished.emit()
    app.processEvents()
    assert_idle(widget)
    assert overall_state(widget) == (0, 2, 0)


def seven_file_batch(widget, tmp_path, *, incompatible=1):
    sources = [tmp_path / f"synthetic-{index}.csv" for index in range(5)]
    for source in sources:
        source.write_text("CPF\nSYNTHETIC\n", encoding="utf-8")
    widget.add_paths(sources)
    mark_compatible(widget)
    for item in widget.files[len(widget.files) - incompatible:]:
        item.status = BatchRestorationStatus.INCOMPATIBLE


def test_completed_batch_with_incompatible_file_does_not_retain_66_percent(page, tmp_path):
    app, widget, output = page
    seven_file_batch(widget, tmp_path)
    widget.start_restoration()
    worker = widget._processing_worker
    assert worker is not None
    # The throttled worker's last intermediate update need not be the last file.
    worker.progress.emit(BatchRestorationProgress(5, 6, 4, 0, "synthetic.csv", 10, 0))
    worker.completed.emit(BatchRestorationSummary(7, 6, 0, 1, 0, 6, 0, output, 0.1))
    assert widget.status_label.text() == "Lote concluído."
    assert overall_state(widget) == (0, 7, 7)
    assert_idle(widget)
    worker.finished.emit()
    app.processEvents()
    assert overall_state(widget) == (0, 7, 7)
    assert_idle(widget)


@pytest.mark.parametrize("outcome", ["completed", "incompatible", "errors", "cancelled", "structural_failure"])
def test_restoration_overall_accounting_keeps_terminal_outcomes_distinct(page, tmp_path, outcome):
    app, widget, output = page
    ignored = 0 if outcome == "completed" else 1
    seven_file_batch(widget, tmp_path, incompatible=ignored)
    changes = []
    widget.overall_progress.valueChanged.connect(
        lambda _value: changes.append(overall_state(widget))
    )
    widget.start_restoration()
    worker = widget._processing_worker
    assert worker is not None
    assert overall_state(widget) == (0, 7, ignored)
    worker.progress.emit(BatchRestorationProgress(2, 7 - ignored, 1, 0, "synthetic.csv", 3, 0))
    assert overall_state(widget) == (0, 7, ignored + 1)
    if outcome == "structural_failure":
        worker.failed.emit(BatchRestorationStructuralError("Falha estrutural ao acessar o cofre local."))
        expected = (0, 7, 2)
        state = "error"
        assert widget.status_label.text() == "Falha estrutural ao acessar o cofre local."
        assert not widget.summary_output.toPlainText()
    else:
        cancelled = outcome == "cancelled"
        if cancelled:
            widget.cancel()
            assert worker._cancellation.is_set()
        summary = BatchRestorationSummary(
            7, 1 if cancelled else 5 if outcome == "errors" else 7 - ignored,
            int(outcome == "errors"), 5 if cancelled else ignored,
            int(cancelled), 3, 0, output, 0.1, cancelled=cancelled,
        )
        worker.completed.emit(summary)
        expected = (0, 7, 2 if cancelled else 7)
        state = "error" if outcome == "errors" else "warning" if ignored else "success"
        assert f"Concluídos: {summary.completed_files}" in widget.summary_output.toPlainText()
        assert f"Ignorados: {summary.skipped_files}" in widget.summary_output.toPlainText()
        assert f"Cancelados: {summary.cancelled_files}" in widget.summary_output.toPlainText()
        assert widget.status_label.text() == (
            "Lote cancelado com segurança." if cancelled else "Lote concluído."
        )
    assert overall_state(widget) == expected
    assert widget.status_label.property("feedbackState") == state
    assert_idle(widget)
    worker.finished.emit()
    app.processEvents()
    assert widget._processing_worker is None and not widget.has_running_workers()
    assert overall_state(widget) == expected
    assert all(minimum <= value <= maximum for minimum, maximum, value in changes)
    assert_idle(widget)


def test_new_restoration_does_not_reuse_previous_incompatible_file_offset(page, tmp_path):
    app, widget, output = page
    seven_file_batch(widget, tmp_path)
    widget.start_restoration()
    worker = widget._processing_worker
    assert worker is not None
    worker.completed.emit(BatchRestorationSummary(7, 6, 0, 1, 0, 6, 0, output, 0.1))
    worker.finished.emit()
    app.processEvents()
    mark_compatible(widget)
    widget.start_restoration()
    worker = widget._processing_worker
    assert worker is not None
    assert overall_state(widget) == (0, 7, 0)
    worker.progress.emit(BatchRestorationProgress(1, 7, 0, 0, "synthetic.csv", 0, 0))
    assert overall_state(widget) == (0, 7, 0)
    worker.finished.emit()
    app.processEvents()
    assert_idle(widget)
