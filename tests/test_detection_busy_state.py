"""Non-modal detection cannot edit a configuration owned by a masking worker."""

from dataclasses import asdict
from threading import Event

import pytest
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import AnonymizationResult, ColumnAction
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError, ProcessingCancelled
from data_mask_studio.detection import DetectionError, analyze_csv_columns
from data_mask_studio.gui import anonymization_worker as worker_module
from test_detection_interface import make_widget, sample_csv


def presentation(widget):
    return (
        widget._configuration_snapshot(), widget._configuration_validated,
        tuple((action.currentData(), prefix.text(), output.text(), normalization.currentData(),
               action.property("columnAction"), prefix.property("validationError"))
              for action, prefix, output, normalization in zip(
                  widget._action_fields, widget._prefix_fields, widget._output_name_fields,
                  widget._normalization_fields)),
        widget.generate_button.isEnabled(), widget.status_label.text(),
        widget.status_label.property("feedbackState"), widget._status_message,
        widget.progress_bar.minimum(), widget.progress_bar.maximum(), widget.progress_bar.value(),
        widget.progress_bar.isHidden(), widget.processed_count_label.text(),
        widget.processed_count_label.isHidden(), widget._last_output_path,
        tuple(widget._configuration_history._undo), tuple(widget._configuration_history._redo),
    )


class Processing:
    def __init__(self, widget, destination):
        self.widget, self.destination = widget, destination
        self.entered, self.release = Event(), Event()
        self.worker = None
        self.outcome = "success"
        self.loop = QEventLoop(widget)

    def backend(self, *args, **kwargs):
        self.entered.set()
        assert self.release.wait(5), "Synthetic worker was not released"
        if kwargs["should_cancel"]():
            raise ProcessingCancelled("Synthetic cancellation")
        if self.outcome == "failure":
            raise CSVAnonymizationError("Synthetic processing failure")
        return AnonymizationResult(self.destination, 2, 0.01)

    def start(self, outcome="success"):
        self.outcome = outcome
        self.widget.validate_current_configuration()
        self.widget._start_processing(self.destination, overwrite=False)
        self.worker = self.widget._worker
        assert self.worker is not None and self.entered.wait(5)
        assert self.worker.isRunning()
        self.worker.finished.connect(self.loop.quit, Qt.ConnectionType.QueuedConnection)

    def join(self):
        self.release.set()
        assert self.worker.wait(5000)

    def finish(self):
        self.join()
        # Deliver this worker's queued terminal callbacks through a real event
        # loop, exiting on its finished signal, not by flushing global events.
        expired = []
        timer = QTimer(self.loop)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: (expired.append(True), self.loop.quit()))
        timer.start(5000)
        try:
            self.loop.exec()
        finally:
            timer.stop()
        assert not expired and self.widget._worker is None
        self.worker = None


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    widget = make_widget(tmp_path)
    widget.load_csv(str(sample_csv(tmp_path)))
    widget.show()
    result = analyze_csv_columns(widget._inspection_result)
    widget._detection_completed(result)
    dialog = widget._detection_dialog
    assert dialog is not None and dialog.isVisible() and not dialog.isModal()
    dialog._acceptance_fields[1].setChecked(True)
    processing = Processing(widget, tmp_path / "synthetic_masked.csv")
    monkeypatch.setattr(worker_module, "anonymize_csv", processing.backend)
    try:
        yield app, widget, dialog, result, processing
    finally:
        if processing.worker is not None:
            processing.finish()


@pytest.mark.parametrize("action", [
    "individual", "high", "accepted", "direct-individual", "direct-bulk", "internal",
    "signal-individual", "signal-bulk", "clear", "method-high", "method-clear",
    "reenabled-individual", "validate", "detection-completed", "detection-cancelled", "detection-failed",
])
def test_active_masking_rejects_detection_callbacks_without_mutation(page, monkeypatch, action):
    _, widget, dialog, result, processing = page
    monkeypatch.setattr(widget, "_confirm", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    processing.start()
    before = presentation(widget)
    plan, columns = processing.worker._processing_plan, [asdict(item) for item in processing.worker._configurations]
    if action == "reenabled-individual":
        dialog.setEnabled(True)  # The receiver must not rely on disabled UI.
    {
        "individual": lambda: dialog.table.cellWidget(0, 8).click(),
        "high": dialog.apply_high_button.click,
        "accepted": dialog.apply_accepted_button.click,
        "direct-individual": lambda: widget.apply_detection_suggestion(0),
        "direct-bulk": lambda: widget.apply_detection_suggestions((0, 1)),
        "internal": lambda: widget._apply_detection_rows((0,)),
        "signal-individual": lambda: dialog.individual_requested.emit(0),
        "signal-bulk": lambda: dialog.bulk_requested.emit((0, 1)),
        "clear": dialog.suggestions_cleared.emit,
        "method-high": dialog._request_high_confidence,
        "method-clear": dialog._clear_suggestions,
        "reenabled-individual": lambda: dialog.table.cellWidget(0, 8).click(),
        "validate": widget.validate_current_configuration,
        "detection-completed": lambda: widget._detection_completed(result),
        "detection-cancelled": widget._detection_cancelled,
        "detection-failed": lambda: widget._detection_failed(DetectionError("Synthetic failure")),
    }[action]()
    assert processing.worker.isRunning()
    assert presentation(widget) == before
    assert processing.worker._processing_plan is plan
    assert [asdict(item) for item in processing.worker._configurations] == columns


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_dialog_remains_suspended_until_finished_cleanup_then_idle_application_works(page, monkeypatch, outcome):
    _, widget, dialog, _, processing = page
    processing.start("failure" if outcome == "failure" else "success")
    assert dialog.isVisible() and not dialog.isEnabled()
    if outcome == "cancelled":
        widget.cancel_processing()
        assert processing.worker._cancel_requested.is_set()
    processing.join()
    assert not processing.worker.isRunning() and widget._worker is processing.worker
    before = presentation(widget)
    dialog.individual_requested.emit(0)
    assert presentation(widget) == before  # isRunning() alone is insufficient.
    processing.finish()
    assert widget._detection_dialog is dialog and dialog.isEnabled()
    assert all(column.action is ColumnAction.PRESERVE for column in widget._column_configs)
    assert not widget._configuration_history.can_undo and not widget._configuration_history.can_redo
    dialog.table.cellWidget(0, 8).click()
    assert widget._column_configs[0].action is ColumnAction.MASK
    assert widget._column_configs[0].prefix == "CPF" and widget.generate_button.isEnabled()
    assert not widget._configuration_history.can_undo  # Suggestions remain a history boundary.


@pytest.mark.parametrize("bulk", [False, True], ids=["individual", "bulk"])
@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])
def test_confirmation_rechecks_busy_state_after_its_modal_loop(page, bulk, answer):
    app, widget, _, _, processing = page
    widget._action_fields[0].setCurrentIndex(widget._action_fields[0].findData(ColumnAction.EXCLUDE.value))
    observations, errors = [], []

    def respond():
        box = app.activeModalWidget()
        try:
            assert isinstance(box, QMessageBox)
            assert box.standardButton(box.defaultButton()) == box.standardButton(box.escapeButton()) == QMessageBox.StandardButton.No
            processing.start()
            observations.append(presentation(widget))
            box.button(answer).click()
        except BaseException as error:
            errors.append(error)
            if isinstance(box, QMessageBox):
                box.reject()

    reply = QTimer(widget)
    reply.setSingleShot(True)
    reply.timeout.connect(respond)
    reply.start(0)
    try:
        if bulk:
            widget.apply_detection_suggestions((0, 1))
        else:
            widget.apply_detection_suggestion(0)
    finally:
        reply.stop()
    if errors:
        raise errors[0]
    assert observations and processing.worker.isRunning()
    assert presentation(widget) == observations[0]


def test_queued_dialog_signal_during_cancellation_cannot_overwrite_feedback(page):
    _, widget, dialog, _, processing = page
    processing.start()
    widget.cancel_processing()
    before = presentation(widget)
    loop = QEventLoop(widget)
    observed = []

    def deliver():
        try:
            dialog.individual_requested.emit(0)
            dialog.suggestions_cleared.emit()
            observed.append(presentation(widget))
        finally:
            loop.quit()

    QTimer.singleShot(0, deliver)
    loop.exec()
    assert observed == [before] and processing.worker.isRunning()
    assert processing.worker._cancel_requested.is_set()


def test_busy_callbacks_do_not_validate_plan_or_touch_history(page, monkeypatch):
    _, widget, dialog, _, processing = page
    processing.start()

    def forbidden(*args, **kwargs):
        pytest.fail("Busy detection must not validate, rebuild a plan or reset history")

    with monkeypatch.context() as guard:
        guard.setattr(widget, "validate_current_configuration", forbidden)
        guard.setattr(widget, "_build_current_plan", forbidden)
        guard.setattr(widget, "_reset_configuration_history", forbidden)
        widget.apply_detection_suggestion(0)
        widget.apply_detection_suggestions((0, 1))
        widget._apply_detection_rows((0,))
        dialog.individual_requested.emit(0)
        dialog.bulk_requested.emit((0, 1))


def test_suspended_dialog_methods_do_not_emit_mutation_requests(page):
    _, _, dialog, _, processing = page
    processing.start()
    spies = [QSignalSpy(signal) for signal in (
        dialog.individual_requested, dialog.bulk_requested, dialog.suggestions_cleared,
    )]
    dialog._request_individual(0)
    dialog._request_high_confidence()
    dialog._request_accepted()
    dialog._clear_suggestions()
    assert all(spy.count() == 0 for spy in spies)


@pytest.mark.parametrize("signal", ["individual", "bulk", "clear"])
def test_retired_dialog_signals_cannot_apply_to_new_suggestions(page, monkeypatch, signal):
    _, widget, old, result, processing = page
    processing.start()
    processing.finish()
    widget._clear_detection_suggestions()
    widget._detection_completed(result)
    current = widget._detection_dialog
    assert current is not old
    before = presentation(widget)

    def forbidden(*args, **kwargs):
        pytest.fail("Retired dialog must not request confirmation")

    monkeypatch.setattr(widget, "_confirm", forbidden)
    {"individual": lambda: old.individual_requested.emit(0),
     "bulk": lambda: old.bulk_requested.emit((0, 1)),
     "clear": old.suggestions_cleared.emit}[signal]()
    assert presentation(widget) == before and widget._detection_dialog is current
