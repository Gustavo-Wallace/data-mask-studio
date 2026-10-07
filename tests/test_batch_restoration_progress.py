"""Current-file lifecycle, separate from overall counters and inline styling."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.batch_restoration import (
    BatchCSVColumn, BatchRestorationError, BatchRestorationProgress,
    BatchRestorationStatus, BatchRestorationSummary,
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
