"""Feedback belongs to the selected CSV/source and to the active worker only."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.restoration_widget import RestorationWidget
from data_mask_studio.gui.restoration_worker import CSVRestorationWorker, RestorationAnalysisWorker
from data_mask_studio.restoration import (
    MissingCodePolicy, RepresentationPolicy, RestorationError, RestorationProgress,
    RestorationResult, RestorationStage,
)
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from test_package_csv_restoration import make_case, PASSWORD
from test_restoration_options_consistency import CODE


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    vault = VaultRepository(tmp_path / "vault.db", VaultCipher(b"S" * 32))
    with vault.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(CODE, "PERSON", "Synthetic original", "Name")])
    widget = RestorationWidget(lambda: vault.as_read_only())
    sources = [tmp_path / "first.csv", tmp_path / "second.csv"]
    sources[0].write_text(f"Name,Kind\n{CODE},SYNTHETIC\n", encoding="utf-8")
    sources[1].write_text("Person,Category\nSYNTHETIC,TEST\n", encoding="utf-8")
    widget.load_csv(str(sources[0]))
    widget._checkboxes[0].setChecked(True)
    widget.show()
    app.processEvents()
    return app, widget, sources


def complete(page, tmp_path, kind="restoration"):
    app, widget, _ = page
    if kind == "analysis":
        widget.start_analysis()
    else:
        widget.start_restoration(tmp_path / "restored.csv", overwrite=False)
    worker = widget._worker
    assert worker is not None and worker.wait(15000)
    app.processEvents()
    assert widget._worker is None and widget._last_error is None
    assert widget.summary.toPlainText() and widget.progress_label.text()
    assert widget.progress_bar.value() == widget.progress_bar.maximum() == 1
    return worker


def snapshot(widget):
    return (
        widget._inspection, tuple(checkbox.isChecked() for checkbox in widget._checkboxes),
        widget.source_combo.currentData(), widget.missing_policy_combo.currentData(),
        widget.representation_combo.currentData(), widget._last_output_path, widget._last_error,
        widget.summary.toPlainText(), widget.status_label.text(), widget.status_label.property("feedbackState"),
        widget.progress_bar.minimum(), widget.progress_bar.maximum(), widget.progress_bar.value(),
        widget.progress_bar.isVisible(), widget.progress_label.text(), widget.progress_label.isVisible(),
        widget.open_folder_button.isVisible(), widget.package_controls.path.text(),
        widget.package_controls.password.text(), widget.package_controls.show_password.isChecked(),
    )


def assert_invalidated(widget):
    assert not widget.summary.toPlainText()
    assert widget._last_output_path is None and widget._last_error is None
    assert not widget._operation_succeeded
    assert widget.progress_bar.maximum() == 1 and widget.progress_bar.value() == 0
    assert widget.progress_bar.isHidden() and widget.progress_label.isHidden()
    assert not widget.progress_label.text() and widget.open_folder_button.isHidden()


@pytest.mark.parametrize("kind", ["restoration", "analysis"])
def test_successful_csv_replacement_invalidates_previous_operation(page, tmp_path, monkeypatch, kind):
    _, widget, sources = page
    complete(page, tmp_path, kind)
    widget.missing_policy_combo.setCurrentIndex(1)
    widget.representation_combo.setCurrentIndex(1)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(sources[1]), "CSV"))
    widget.select_button.click()
    assert widget._inspection.path == sources[1]
    assert [widget.table.item(row, 1).text() for row in range(2)] == ["Person", "Category"]
    assert not any(checkbox.isChecked() for checkbox in widget._checkboxes)
    assert widget.missing_policy_combo.currentData() == MissingCodePolicy.EMPTY.value
    assert widget.representation_combo.currentData() == RepresentationPolicy.CANONICAL.value
    assert widget.status_label.text() == "Cabecalhos lidos com sucesso."
    assert_invalidated(widget)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *_: pytest.fail("Old output must not open"))
    widget.open_output_folder()


def test_source_round_trip_clears_feedback_not_file_columns_or_existing_policy_inputs(page, tmp_path):
    _, widget, _ = page
    complete(page, tmp_path)
    inspection = widget._inspection
    selection = tuple(checkbox.isChecked() for checkbox in widget._checkboxes)
    widget.missing_policy_combo.setCurrentIndex(1)
    widget.representation_combo.setCurrentIndex(1)
    package = tmp_path / "synthetic.dmspackage"
    widget.package_controls.path.setText(str(package))
    widget.package_controls.password.setText("transient synthetic secret")
    widget.package_controls.show_password.setChecked(True)
    for index in (1, 0):
        widget.source_combo.setCurrentIndex(index)
        assert_invalidated(widget)
        assert widget._inspection is inspection
        assert tuple(checkbox.isChecked() for checkbox in widget._checkboxes) == selection
        assert widget.package_controls.path.text() == str(package)
        assert not widget.package_controls.password.text()
        assert not widget.package_controls.show_password.isChecked()
        assert widget.package_controls.password.echoMode() is QLineEdit.EchoMode.Password
        assert widget.representation_combo.currentData() == RepresentationPolicy.CANONICAL.value
        assert widget.missing_policy_combo.currentData() == (
            MissingCodePolicy.ABORT.value if index else MissingCodePolicy.EMPTY.value
        )
        assert widget.analyze_button.isEnabled() == (not index)
        assert widget.package_controls.isVisible() == bool(index)
        assert widget.status_label.property("feedbackState") == "neutral"
        widget.package_controls.password.setText("another transient synthetic secret")
        widget.package_controls.show_password.setChecked(True)


@pytest.mark.parametrize("package_mode", [False, True])
def test_cancelled_csv_picker_preserves_valid_feedback_and_transient_inputs(page, tmp_path, monkeypatch, package_mode):
    _, widget, _ = page
    if package_mode:
        fixture = tmp_path / "package-fixture"
        fixture.mkdir()
        _, config, package, _, _ = make_case(fixture)
        widget.source_combo.setCurrentIndex(1)
        widget.load_csv(str(config.source_path))
        widget._checkboxes[0].setChecked(True)
        widget._checkboxes[1].setChecked(True)
        widget.package_controls.path.setText(str(package))
        widget.package_controls.password.setText(PASSWORD)
    complete(page, tmp_path)
    widget.package_controls.password.setText("fresh transient synthetic input")
    before = snapshot(widget)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", "CSV"))
    widget.select_button.click()
    assert snapshot(widget) == before


@pytest.mark.parametrize("kind", ["restoration", "analysis"])
def test_clear_selection_removes_all_obsolete_feedback(page, tmp_path, monkeypatch, kind):
    _, widget, _ = page
    complete(page, tmp_path, kind)
    widget.clear_selection()
    assert_invalidated(widget)
    assert widget._inspection is None and not widget.path_field.text()
    assert widget.table.rowCount() == 0 and not widget._checkboxes
    assert not widget.generate_button.isEnabled() and not widget.analyze_button.isEnabled()
    assert widget.status_label.text() == "Selecione um CSV anonimizado para começar."
    assert widget.status_label.property("feedbackState") == "neutral"
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *_: pytest.fail("No output is selected"))
    widget.open_output_folder()


def test_failed_csv_inspection_keeps_existing_selection_and_result(page, tmp_path):
    _, widget, _ = page
    complete(page, tmp_path)
    before = snapshot(widget)
    widget.load_csv(str(tmp_path / "missing.csv"))
    after = snapshot(widget)
    assert after[:8] == before[:8]
    assert after[10:] == before[10:]
    assert widget.status_label.property("feedbackState") == "error"


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
@pytest.mark.parametrize("change", ["load", "clear", "source"])
def test_failed_or_cancelled_feedback_is_invalidated_with_its_context(page, tmp_path, monkeypatch, outcome, change):
    _, widget, sources = page
    monkeypatch.setattr(CSVRestorationWorker, "start", lambda self: None)
    widget.start_restoration(tmp_path / "incomplete.csv", overwrite=False)
    worker = widget._worker
    assert worker is not None
    worker.progress.emit(RestorationProgress(RestorationStage.RESTORING, 3, 2))
    if outcome == "failure":
        worker.failed.emit(RestorationError("Synthetic operation failed."))
        assert widget._last_error is not None
    else:
        worker.cancelled.emit()
    worker.finished.emit()
    assert widget.progress_bar.value() == 0 and widget.progress_label.text()
    assert widget.status_label.property("feedbackState") == ("error" if outcome == "failure" else "warning")
    old_status = widget.status_label.text()
    if change == "load":
        widget.load_csv(str(sources[1]))
    elif change == "clear":
        widget.clear_selection()
    else:
        widget.source_combo.setCurrentIndex(1)
    assert_invalidated(widget)
    assert widget.status_label.text() != old_status
    assert widget.status_label.property("feedbackState") == ("success" if change == "load" else "neutral")


def test_new_analysis_removes_previous_output_feedback_before_worker_start(page, tmp_path, monkeypatch):
    _, widget, _ = page
    complete(page, tmp_path)
    monkeypatch.setattr(RestorationAnalysisWorker, "start", lambda self: None)
    widget.start_analysis()
    worker = widget._worker
    assert worker is not None
    assert not widget.summary.toPlainText() and widget._last_output_path is None
    assert widget.open_folder_button.isHidden() and not widget._operation_succeeded
    assert widget.progress_bar.maximum() == 0 and widget.progress_bar.isVisible()
    assert widget.progress_label.text() == "0 linhas processadas"
    worker.finished.emit()


@pytest.mark.parametrize("change", ["load", "clear", "source", "new_worker"])
def test_queued_old_worker_signals_cannot_repopulate_invalidated_feedback(page, tmp_path, monkeypatch, change):
    app, widget, sources = page
    monkeypatch.setattr(CSVRestorationWorker, "start", lambda self: None)
    monkeypatch.setattr(RestorationAnalysisWorker, "start", lambda self: None)
    widget.start_restoration(tmp_path / "old.csv", overwrite=False)
    old = widget._worker
    assert old is not None
    result = RestorationResult(tmp_path / "old.csv", 3, 2, 0, 1, 0, 0.1,
                               MissingCodePolicy.KEEP, RepresentationPolicy.FIRST_ORIGINAL)

    class LateEmitter(QThread):
        def run(self):
            old.progress.emit(RestorationProgress(RestorationStage.RESTORING, 3, 2))
            old.completed.emit(result)
            old.failed.emit(RestorationError("OLD_FAILURE_SENTINEL"))
            old.cancelled.emit()
            old.finished.emit()

    emitter = LateEmitter(widget)
    emitter.start()
    assert emitter.wait(5000)
    # Retire the old worker before delivering its queued callbacks.
    old.finished.emit()
    assert widget._worker is None
    if change == "clear":
        widget.clear_selection()
    elif change == "source":
        widget.source_combo.setCurrentIndex(1)
    else:
        widget.load_csv(str(sources[1]))
        if change == "new_worker":
            widget.select_all_columns()
            widget.start_analysis()
    current = widget._worker
    before = snapshot(widget)
    app.processEvents()
    assert snapshot(widget) == before and widget._worker is current
    if current is not None:
        current.finished.emit()
    else:
        assert_invalidated(widget)


def test_busy_worker_blocks_file_clear_and_programmatic_source_switch(page, tmp_path, monkeypatch):
    _, widget, sources = page
    monkeypatch.setattr(CSVRestorationWorker, "start", lambda self: None)
    widget.start_restoration(tmp_path / "active.csv", overwrite=False)
    worker = widget._worker
    assert worker is not None and not widget.source_combo.isEnabled()
    before = snapshot(widget)
    widget.load_csv(str(sources[1]))
    widget.clear_selection()
    widget.source_combo.setCurrentIndex(1)
    assert snapshot(widget) == before and widget._worker is worker
    worker.finished.emit()


def test_real_worker_feedback_stays_on_gui_thread(page, tmp_path, monkeypatch):
    app, widget, _ = page
    calls = []
    original = widget._restoration_completed

    def completed(result):
        calls.append(QThread.currentThread() == app.thread())
        original(result)

    monkeypatch.setattr(widget, "_restoration_completed", completed)
    complete(page, tmp_path)
    assert calls == [True]
