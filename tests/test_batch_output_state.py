"""Batch output actions require publication, not a configured destination."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.batch import BatchFileStatus, BatchStructuralError, BatchSummary
from data_mask_studio.batch_restoration import (
    BatchCSVColumn, BatchRestorationStatus, BatchRestorationStructuralError,
    BatchRestorationSummary,
)
from data_mask_studio.gui.batch_widget import BatchWidget
from data_mask_studio.gui.batch_worker import BatchProcessingWorker, BatchValidationWorker
from data_mask_studio.gui.batch_restoration_widget import BatchRestorationWidget
from data_mask_studio.gui.batch_restoration_worker import (
    BatchRestorationAnalysisWorker, BatchRestorationProcessingWorker,
)
from data_mask_studio.profiles import ProfileRepository, ProfileService


class NoKeyAccess:
    def get_key(self):
        pytest.fail("GUI signal tests must not access keys")


@pytest.fixture(params=["anonymization", "restoration"])
def page(request, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    if request.param == "anonymization":
        profiles = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
        profiles.create("Synthetic batch", [ColumnConfig("Name", True, "NAME")])
        widget = BatchWidget(profiles, NoKeyAccess(), lambda: pytest.fail("No vault access"))
    else:
        widget = BatchRestorationWidget(lambda: pytest.fail("No vault access"))
    for worker_type in (BatchProcessingWorker, BatchValidationWorker,
                        BatchRestorationAnalysisWorker, BatchRestorationProcessingWorker):
        monkeypatch.setattr(worker_type, "start", lambda self: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Yes)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())) or True)
    for name in ("a.csv", "b.csv"):
        source = tmp_path / name
        source.write_text("Name\nSYNTHETIC\n", encoding="utf-8")
        widget.add_paths([source])
    return app, widget, request.param, tmp_path, opened


def start(page, directory):
    _, widget, kind, _, _ = page
    for item in widget.files:
        if kind == "anonymization":
            item.status = BatchFileStatus.COMPATIBLE
        else:
            item.status = BatchRestorationStatus.COMPATIBLE
            item.columns = [BatchCSVColumn(0, "Name", 1, 1, 0, selected=True)]
        item.output_path = None  # The existing revalidation/analysis contract.
    directory.mkdir(exist_ok=True)
    widget.output_field.setText(str(directory))
    if kind == "anonymization":
        widget.start_processing()
    else:
        widget.start_restoration()
    assert widget._processing_worker is not None
    return widget._processing_worker


def publish_first(page, worker, directory):
    app, widget, kind, _, _ = page
    output = directory / "published.csv"
    output.write_text("Name\nSYNTHETIC OUTPUT\n", encoding="utf-8")
    item = widget.files[0]
    item.output_path = output
    item.status = (BatchFileStatus.COMPLETED if kind == "anonymization"
                   else BatchRestorationStatus.COMPLETED)
    worker.file_changed.emit(item)
    app.processEvents()
    return output


def finish(page, worker, directory, *, completed, outcome="success"):
    app, widget, kind, _, _ = page
    if outcome == "failure":
        error_type = BatchStructuralError if kind == "anonymization" else BatchRestorationStructuralError
        worker.failed.emit(error_type("Synthetic structural failure"))
    else:
        cancelled = outcome == "cancel"
        if cancelled:
            widget.cancel()
            cancellation = worker._cancellation
            assert cancellation.is_requested() if kind == "anonymization" else cancellation.is_set()
        errors = int(outcome == "item_error")
        skipped = len(widget.files) - completed - errors
        if kind == "anonymization":
            summary = BatchSummary(len(widget.files), len(widget.files), completed, 0,
                                   errors, skipped, completed, 0, 0, 0.1, directory)
        else:
            summary = BatchRestorationSummary(len(widget.files), completed, errors,
                skipped - int(cancelled), int(cancelled), completed, 0, directory, 0.1,
                cancelled=cancelled)
        worker.completed.emit(summary)
    worker.finished.emit()
    app.processEvents()
    assert widget._processing_worker is None


def assert_open_directory(page, directory):
    _, widget, _, _, opened = page
    assert widget.open_output_button.isEnabled()
    widget.open_output_button.click()
    assert opened == [directory]
    opened.clear()


def assert_no_output_action(page):
    _, widget, _, _, opened = page
    assert not widget.open_output_button.isEnabled()
    widget.open_output_directory()  # The handler must also have no stale target.
    assert opened == []


def test_success_then_new_directory_failure_invalidates_previous_output_action(page):
    _, widget, _, root, _ = page
    first, second = root / "batch-A", root / "batch-B"
    worker = start(page, first)
    publish_first(page, worker, first)
    finish(page, worker, first, completed=1)
    assert_open_directory(page, first)
    worker = start(page, second)
    action_enabled_at_start = widget.open_output_button.isEnabled()
    finish(page, worker, second, completed=0, outcome="failure")
    assert not action_enabled_at_start
    assert_no_output_action(page)
    assert widget.status_label.property("feedbackState") == "error"


def test_current_publication_enables_action_and_keeps_actual_directory(page):
    _, widget, _, root, _ = page
    output = root / "current-batch"
    worker = start(page, output)
    assert_no_output_action(page)
    published = publish_first(page, worker, output)
    assert published.is_file()
    assert_open_directory(page, output)
    finish(page, worker, output, completed=1)
    widget.output_field.setText(str(root / "configured-but-unpublished"))
    assert_open_directory(page, output)


@pytest.mark.parametrize("outcome", ["failure", "cancel", "item_error"])
def test_partial_publication_survives_later_failure_or_cancellation(page, outcome):
    _, widget, _, root, _ = page
    first, current = root / "previous", root / "current"
    worker = start(page, first)
    publish_first(page, worker, first)
    finish(page, worker, first, completed=1)
    worker = start(page, current)
    published = publish_first(page, worker, current)
    finish(page, worker, current, completed=1, outcome=outcome)
    assert published.is_file()
    assert_open_directory(page, current)
    assert widget.status_label.property("feedbackState") == ("warning" if outcome == "cancel" else "error")
    if page[2] == "restoration":
        assert (widget.current_progress.maximum(), widget.current_progress.value()) == (1, 0)
        assert widget.current_progress_label.text() == "Nenhum processamento em andamento."


@pytest.mark.parametrize("outcome", ["failure", "cancel", "item_error", "success"])
def test_no_publication_in_new_batch_keeps_action_disabled(page, outcome):
    _, widget, _, root, _ = page
    first, second = root / "published-A", root / "empty-B"
    worker = start(page, first)
    publish_first(page, worker, first)
    finish(page, worker, first, completed=1)
    worker = start(page, second)
    finish(page, worker, second, completed=0, outcome=outcome)
    assert second.is_dir() and not list(second.iterdir())
    assert_no_output_action(page)


def test_analysis_and_existing_directory_are_not_publication(page):
    app, widget, kind, root, _ = page
    destination = root / "existing-directory"
    destination.mkdir()
    widget.output_field.setText(str(destination))
    if kind == "anonymization":
        widget.validate_files()
        worker = widget._validation_worker
    else:
        widget.analyze_files()
        worker = widget._analysis_worker
    assert worker is not None
    worker.completed.emit()
    worker.finished.emit()
    app.processEvents()
    assert_no_output_action(page)


@pytest.mark.parametrize("page", ["restoration"], indirect=True)
def test_clear_completed_restoration_resets_progress_status_and_output_action(page):
    _, widget, _, root, _ = page
    output = root / "completed"
    worker = start(page, output)
    publish_first(page, worker, output)
    finish(page, worker, output, completed=1)
    assert widget.overall_progress.value() == widget.overall_progress.maximum()
    assert widget.status_label.text() == "Lote concluído."
    widget.clear_button.click()
    assert not widget.files and not widget.summary_output.toPlainText()
    assert (widget.overall_progress.minimum(), widget.overall_progress.maximum(),
            widget.overall_progress.value()) == (0, 1, 0)
    assert widget.status_label.text() == "Adicione arquivos CSV ou HTML para começar."
    assert widget.status_label.property("feedbackState") == "neutral"
    assert (widget.current_progress.maximum(), widget.current_progress.value()) == (1, 0)
    assert widget.current_progress_label.text() == "Nenhum processamento em andamento."
    assert widget.cancel_button.isHidden()
    assert_no_output_action(page)


@pytest.mark.parametrize("page", ["restoration"], indirect=True)
@pytest.mark.parametrize("phase", ["analysis", "restoration"])
def test_clear_does_not_discard_running_restoration_or_analysis(page, phase):
    app, widget, _, root, _ = page
    if phase == "analysis":
        widget.analyze_files()
        worker = widget._analysis_worker
    else:
        worker = start(page, root / "active")
    assert worker is not None and not widget.clear_button.isEnabled()
    files = list(widget.files)
    status = widget.status_label.text()
    progress = (widget.overall_progress.maximum(), widget.overall_progress.value())
    widget.clear_files()
    assert widget.files == files
    assert widget.status_label.text() == status
    assert (widget.overall_progress.maximum(), widget.overall_progress.value()) == progress
    assert widget.current_progress.maximum() == 0
    worker.finished.emit()
    app.processEvents()


@pytest.mark.parametrize("page", ["restoration"], indirect=True)
def test_clear_and_repeat_restore_initial_feedback_after_each_terminal_state(page):
    _, widget, _, root, _ = page
    for index, outcome in enumerate(("success", "cancel", "failure")):
        if not widget.files:
            widget.add_paths([root / "a.csv", root / "b.csv"])
        output = root / f"run-{index}"
        worker = start(page, output)
        if outcome == "success":
            publish_first(page, worker, output)
        finish(page, worker, output, completed=int(outcome == "success"), outcome=outcome)
        widget.clear_files()
        assert widget.overall_progress.value() == 0
        assert widget.status_label.text() == "Adicione arquivos CSV ou HTML para começar."
        assert widget.status_label.property("feedbackState") == "neutral"
        assert widget.current_progress.maximum() == 1 and widget.current_progress.value() == 0
        assert_no_output_action(page)
