"""Batch restoration presentation must not influence eligibility or publication."""

from dataclasses import asdict
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.batch_restoration import (
    STATUS_LABELS, BatchCSVColumn, BatchMissingCodePolicy, BatchRestorationError,
    BatchRestorationFile, BatchRestorationFileType, BatchRestorationProgress,
    BatchRestorationStatus as Status, BatchRestorationStructuralError, BatchRestorationSummary,
)
from data_mask_studio.gui.batch_restoration_widget import BatchRestorationWidget, _render_summary
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.restoration import RepresentationPolicy, RestorationError
from data_mask_studio.vault import VaultError
from test_batch_profile_isolation import pump_until
from test_batch_restoration import repository, CODE, MISSING, ORIGINAL, CANONICAL
from test_dialog_consistency import modal_reply
from test_navigation_localization import build_localized_window


@pytest.fixture
def pages(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    widgets = []

    def create(locale="pt-BR", *, case=None):
        if case is None:
            root = tmp_path / f"case-{len(widgets)}"
            root.mkdir()
            vault = repository(root)
            inputs = root / "inputs"
            inputs.mkdir()
            paths = [inputs / name for name in (
                "clientes & ação_masked.csv", "legacy_anonimizado.csv", "painel_anonimizado.HTM")]
            for path in paths[:2]:
                path.write_text(f"CPF,Kind\n{CODE},SYNTHETIC\n", encoding="utf-8")
            paths[2].write_text(f'<p title="{CODE}">{CODE}</p>', encoding="utf-8")
            missing = inputs / "unknown.csv"
            missing.write_text(f"CPF,Kind\n{MISSING},SYNTHETIC\n", encoding="utf-8")
            bad = inputs / "no-tokens.html"
            bad.write_text("<p>SYNTHETIC text without tokens</p>", encoding="utf-8")
            case = vault, tuple(paths), missing, bad
        vault, paths, _, _ = case
        output = paths[0].parent.parent / f"output-{locale}-{len(widgets)}"
        output.mkdir()
        widget = BatchRestorationWidget(lambda: vault.as_read_only(), translator=Translator(locale))
        widgets.append(widget)
        widget.output_field.setText(str(output))
        monkeypatch.setattr(widget, "_confirm_restoration", lambda: QMessageBox.StandardButton.Yes)
        return app, widget, case, output

    yield create
    for widget in widgets:
        assert widget.stop_workers()


def finish(app, widget, attribute):
    worker = getattr(widget, attribute)
    assert worker is not None and worker.wait(15000), "The real Qt worker did not stop"
    app.processEvents()
    assert getattr(widget, attribute) is None, "Queued GUI cleanup did not retire the worker"
    return worker


def analyze(app, widget):
    widget.analyze_files()
    finish(app, widget, "_analysis_worker")


def confirm_columns(widget):
    for row, item in enumerate(widget.files):
        if item.file_type is BatchRestorationFileType.CSV and item.columns:
            widget.file_table.selectRow(row)
            widget.select_candidate_columns()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_initial_labels_headers_and_explicit_main_window_injection(pages, tmp_path, locale):
    _, widget, _, _ = pages(locale)
    translator = Translator(locale)
    assert widget.analyze_button.text() == ("Analyze files" if locale == "en" else "Analisar arquivos")
    assert widget.start_button.text() == ("Start restoration" if locale == "en" else "Iniciar restauração")
    for control, message_id in (
        (widget.add_files_button, "batch.add_files"), (widget.add_folder_button, "batch.add_folder"),
        (widget.remove_button, "batch.remove"), (widget.clear_button, "batch.clear"),
        (widget.choose_output_button, "batch.choose_folder"), (widget.open_output_button, "batch.open_output"),
        (widget.status_label, "batch_restoration.status.initial"),
        (widget.current_progress_label, "batch.current.idle"),
    ):
        assert control.text() == translator.text(message_id)
    assert widget.file_table.empty_text == translator.text("batch.files_empty")
    assert widget.column_table.empty_text == translator.text("batch_restoration.columns.empty")
    assert widget.summary_output.empty_text == translator.text("batch_restoration.summary.empty")
    assert [widget.file_table.horizontalHeaderItem(i).text() for i in range(8)] == (
        ["File", "Type", "Encoding", "Status", "Tokens", "In vault", "Missing", "Result"] if locale == "en" else
        ["Arquivo", "Tipo", "Codificação", "Status", "Códigos", "No cofre", "Ausentes", "Resultado"])
    assert not widget.analyze_button.isEnabled() and not widget.start_button.isEnabled()
    assert not widget.open_output_button.isEnabled()
    assert not hasattr(widget, "package_controls") and not hasattr(widget, "profile_combo")
    root = tmp_path / "window"
    root.mkdir()
    window = build_localized_window(root, translator)
    assert window.batch_restoration_widget._translator is translator
    assert window.batch_restoration_widget.output_field.accessibleName() == translator.text("batch_restoration.output_accessible")
    assert window.page_stack.count() == 8 and window.page_index(window.batch_restoration_widget) == 3
    window.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert window.batch_restoration_widget._translator is window._translator


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_every_typed_status_and_success_output_reference_translate_without_model_mutation(pages, locale):
    _, widget, case, output = pages(locale)
    labels_before = dict(STATUS_LABELS)
    widget.files = [BatchRestorationFile(case[1][0].with_name(status.value + ".csv"),
                    BatchRestorationFileType.CSV, status=status,
                    result_message="Safe literal service warning.") for status in Status]
    completed = next(item for item in widget.files if item.status is Status.COMPLETED)
    completed.output_path = output / "literal & {filename}_restaurado.csv"
    completed.output_path.write_text("SYNTHETIC output", encoding="utf-8")
    widget._refresh_file_table()
    before = [asdict(item) for item in widget.files]
    for target in (locale, "en" if locale == "pt-BR" else "pt-BR"):
        widget.retranslate(Translator(target))
        for row, item in enumerate(widget.files):
            message_id = ("batch_restoration.file_status." if item.status in (Status.PENDING, Status.REVIEW_REQUIRED)
                          else "batch.file_status.") + item.status.value
            assert widget.file_table.item(row, 3).text() == Translator(target).text(message_id)
            expected = (Translator(target).text("batch_restoration.result.output", filename=item.output_path.name)
                        if item is completed else item.result_message)
            assert widget.file_table.item(row, 7).text() == widget.file_table.item(row, 7).toolTip() == expected
        assert [asdict(item) for item in widget.files] == before and STATUS_LABELS == labels_before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_files_folder_filters_removal_clear_and_picker_cancellation(pages, monkeypatch, locale):
    app, widget, case, _ = pages(locale)
    paths = case[1]
    folder = paths[0].parent
    ignored = folder / "unsupported.dmspackage"
    ignored.write_bytes(b"synthetic unsupported input")
    nested = folder / "nested"
    nested.mkdir()
    (nested / "not-discovered.csv").write_text("Name\nSYNTHETIC\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
        lambda *args: (calls.append(args) or ([str(paths[0]), str(paths[0]), str(paths[2]), str(ignored)], "")))
    widget.add_files_button.click()
    assert [item.path for item in widget.files] == [paths[0], paths[2]]
    assert calls[-1][1] == Translator(locale).text("batch_restoration.files_title")
    assert calls[-1][3].endswith("(*.csv *.html *.htm)")
    assert widget.status_label.text() == Translator(locale).plural("batch.files_added", 2)
    before = tuple(widget.files), widget.status_label.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([], ""))
    widget.add_files_button.click()
    assert (tuple(widget.files), widget.status_label.text()) == before
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(folder))
    widget.add_folder_button.click()
    assert len(widget.files) == 5 and ignored not in [item.path for item in widget.files]
    assert all(item.path.parent == folder for item in widget.files)
    widget.show()
    app.processEvents()
    widget.file_table.selectRow(0)
    widget.file_table.setFocus()
    QTest.keyClick(widget.file_table, Qt.Key.Key_Down)
    removed = widget.files[widget.file_table.currentRow()]
    widget.remove_button.click()
    assert removed not in widget.files and len(widget.files) == 4
    widget.clear_button.click()
    widget.retranslate(Translator(locale))
    assert not widget.files and not widget.summary_output.toPlainText()
    assert widget.overall_progress.value() == 0 and widget.current_progress.maximum() == 1
    assert widget.status_label.text() == Translator(locale).text("batch_restoration.status.initial")
    assert widget.status_label.property("feedbackState") == "neutral"


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("state", ["pending", "incompatible", "unselected", "compatible", "review", "mixed", "invalidated", "no_output", "invalid_output"])
def test_actual_eligibility_and_independent_handler_gates_remain_locale_independent(pages, locale, state):
    app, widget, case, output = pages(locale)
    paths = [case[3]] if state == "incompatible" else [case[2]] if state == "review" else [case[1][0]]
    if state == "mixed":
        paths.append(case[3])
    widget.add_paths(paths)
    if state != "pending":
        analyze(app, widget)
        if state != "unselected":
            confirm_columns(widget)
    if state == "invalidated":
        widget.invalidate_analysis()
        assert all(item.status is Status.PENDING and item.output_path is None for item in widget.files)
    if state == "no_output":
        widget.output_field.clear()
    if state == "invalid_output":
        widget.output_field.setText(str(output / "does-not-exist"))
    allowed = state in ("compatible", "review", "mixed")
    # Existing policy enables a nonempty destination; the handler independently checks the directory.
    assert widget.start_button.isEnabled() == (allowed or state == "invalid_output")
    before = [asdict(item) for item in widget.files]
    widget.start_restoration()
    if not allowed:
        assert widget._processing_worker is None and not list(output.iterdir())
        assert [asdict(item) for item in widget.files] == before
        message_id = "batch_restoration.error.output" if state == "invalid_output" else "batch_restoration.error.not_ready"
        assert widget.status_label.text() == Translator(locale).text(message_id)
        assert widget.status_label.property("feedbackState") == "error"
    else:
        finish(app, widget, "_processing_worker")
        assert widget.files[0].status is Status.COMPLETED and widget.files[0].output_path.is_file()
        assert widget._displayed_summary.completed_files == 1
        if state == "mixed":
            assert widget.files[1].status is Status.INCOMPATIBLE and widget.files[1].output_path is None


@pytest.mark.parametrize("policy", list(BatchMissingCodePolicy))
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_real_mixed_batch_outputs_names_counts_and_outcomes_match_between_locales(pages, policy, representation):
    app, _, case, _ = pages()
    paths = [case[1][0], case[2], case[1][1], case[1][2], case[3]]
    originals = {path: path.read_bytes() for path in paths}
    vault_bytes = case[0].database_path.read_bytes()
    outputs, outcomes = [], []
    for locale in ("pt-BR", "en"):
        _, widget, _, output = pages(locale, case=case)
        widget.add_paths(paths)
        analyze(app, widget)
        assert [item.status for item in widget.files] == [Status.COMPATIBLE, Status.REVIEW_REQUIRED,
            Status.COMPATIBLE, Status.COMPATIBLE, Status.INCOMPATIBLE]
        confirm_columns(widget)
        widget.representation_combo.setCurrentIndex(widget.representation_combo.findData(representation.value))
        widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(policy.value))
        # Existing collision policy must preserve this file and choose the numeric suffix.
        protected = output / "clientes & ação_restaurado.csv"
        protected.write_bytes(b"preexisting synthetic output")
        widget.start_restoration()
        worker = widget._processing_worker
        assert worker._options.representation_policy is representation and worker._options.missing_code_policy is policy
        finish(app, widget, "_processing_worker")
        summary = widget._displayed_summary
        outputs.append({item.output_path.name: item.output_path.read_bytes() for item in widget.files if item.output_path})
        outcomes.append((tuple(item.status for item in widget.files), summary.selected_files, summary.completed_files,
                         summary.error_files, summary.skipped_files, summary.restored_occurrences, summary.missing_occurrences))
        assert widget.files[0].output_path.name == "clientes & ação_restaurado_2.csv"
        assert protected.read_bytes() == b"preexisting synthetic output"
        assert summary.completed_files == (4 if policy is BatchMissingCodePolicy.KEEP else 3 if policy is BatchMissingCodePolicy.ABORT_FILE else 1)
        assert summary.error_files == (0 if policy is BatchMissingCodePolicy.KEEP else 1)
        assert summary.skipped_files == (3 if policy is BatchMissingCodePolicy.ABORT_BATCH else 1)
        assert summary.missing_occurrences == int(policy is BatchMissingCodePolicy.KEEP)
        assert summary.restored_occurrences == (1 if policy is BatchMissingCodePolicy.ABORT_BATCH else 4)
        assert widget.summary_output.toPlainText() == _render_summary(summary, Translator(locale))
        assert widget.status_label.property("feedbackState") == ("warning" if policy is BatchMissingCodePolicy.KEEP else "error")
        assert widget.overall_progress.value() == widget.overall_progress.maximum() == 5
        assert widget.current_progress.maximum() == 1 and widget.current_progress.value() == 0
        assert widget.open_output_button.isEnabled() and widget._output_directory == output
        assert all(secret not in widget.summary_output.toPlainText() for secret in (ORIGINAL, CANONICAL, CODE, MISSING))
        if policy is not BatchMissingCodePolicy.ABORT_BATCH:
            assert "legacy_restaurado.csv" in outputs[-1] and "painel_restaurado.html" in outputs[-1]
    assert outputs[0] == outputs[1] and outcomes[0] == outcomes[1]
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert case[0].database_path.read_bytes() == vault_bytes


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_retranslation_preserves_model_cells_columns_selection_focus_summary_and_output_action(pages, locale):
    app, widget, case, output = pages(locale)
    widget.add_paths(case[1])
    analyze(app, widget)
    confirm_columns(widget)
    widget.start_restoration()
    finish(app, widget, "_processing_worker")
    widget.resize(1180, 820)
    widget.show()
    app.processEvents()
    widget.file_table.selectRow(0)
    widget.column_table.cellWidget(0, 0).setFocus()
    cursor = widget.summary_output.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    widget.summary_output.setTextCursor(cursor)
    files = widget.files, tuple(widget.files)
    cells = tuple(widget.file_table.item(r, c) for r in range(3) for c in range(8))
    checkboxes = tuple(widget.column_table.cellWidget(r, 0) for r in range(widget.column_table.rowCount()))
    before = [asdict(item) for item in widget.files]
    summary = widget._displayed_summary
    sizes = widget.splitter.sizes()
    selection = [(index.row(), index.column()) for index in widget.file_table.selectedIndexes()]
    spies = [QSignalSpy(signal) for signal in (widget.representation_combo.currentIndexChanged,
        widget.missing_policy_combo.currentIndexChanged, widget.busy_changed, checkboxes[0].toggled)]
    for target in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(target))
        app.processEvents()
        assert (widget.files, tuple(widget.files)) == files and [asdict(item) for item in widget.files] == before
        assert tuple(widget.file_table.item(r, c) for r in range(3) for c in range(8)) == cells
        assert tuple(widget.column_table.cellWidget(r, 0) for r in range(widget.column_table.rowCount())) == checkboxes
        assert [(index.row(), index.column()) for index in widget.file_table.selectedIndexes()] == selection
        assert widget.file_table.currentRow() == 0 and checkboxes[0].hasFocus()
        assert all(spy.count() == 0 for spy in spies)
        assert (widget.summary_output.textCursor().anchor(), widget.summary_output.textCursor().position()) == (3, 9)
        assert widget._displayed_summary is summary and widget._output_directory == output
        assert widget.open_output_button.isEnabled() and widget.output_field.text() == str(output)
        assert widget.splitter.sizes() == sizes


@pytest.mark.parametrize("phase", ["analysis", "processing"])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_worker_cancellation_and_busy_retranslation_keep_ownership(pages, monkeypatch, phase, locale):
    app, widget, case, output = pages(locale)
    widget.add_paths(case[1])
    if phase == "processing":
        analyze(app, widget)
        confirm_columns(widget)
    entered, proceed = Event(), Event()
    method = "analyze_files" if phase == "analysis" else "restore_files"
    attribute = "_analysis_worker" if phase == "analysis" else "_processing_worker"
    original = getattr(widget._service, method)

    def synchronized(*args, **kwargs):
        entered.set()
        assert proceed.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(widget._service, method, synchronized)
    widget.analyze_files() if phase == "analysis" else widget.start_restoration()
    worker = getattr(widget, attribute)
    try:
        assert entered.wait(10)
        before = widget.files, tuple(widget.files), [asdict(item) for item in widget.files]
        widget.retranslate(Translator("en"))
        assert getattr(widget, attribute) is worker and worker.isRunning()
        assert not widget.start_button.isEnabled() and not widget.analyze_button.isEnabled()
        widget.clear_files()
        assert (widget.files, tuple(widget.files), [asdict(item) for item in widget.files]) == before
        widget.cancel()
        assert worker._cancellation.is_set() and not widget.cancel_button.isEnabled()
        widget.retranslate(Translator("pt-BR"))
        assert getattr(widget, attribute) is worker and not widget.cancel_button.isEnabled()
        assert widget.status_label.text() == "Cancelamento solicitado..."
    finally:
        proceed.set()
    finish(app, widget, attribute)
    assert not list(output.iterdir()) and widget.overall_progress.value() < widget.overall_progress.maximum()
    assert widget.current_progress.maximum() == 1 and widget.current_progress.value() == 0
    assert widget.status_label.property("feedbackState") == "warning"


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("outcome", ["cancel", "file_error", "structural"])
def test_partial_publication_remains_accessible_after_cancel_or_failure(pages, monkeypatch, locale, outcome):
    app, widget, case, output = pages(locale)
    widget.add_paths(case[1])
    analyze(app, widget)
    confirm_columns(widget)
    reached, proceed = Event(), Event()
    original_batch, original_file = widget._service.restore_files, widget._service._restore_file

    def synchronized(*args, file_callback, **kwargs):
        def changed(item):
            file_callback(item)
            if item is widget.files[0] and item.status is Status.COMPLETED:
                reached.set()
                assert proceed.wait(10)
        return original_batch(*args, file_callback=changed, **kwargs)

    def per_file(item, *args, **kwargs):
        if item is widget.files[1] and outcome != "cancel":
            if outcome == "structural":
                raise VaultError("PRIVATE-SYNTHETIC-VAULT-DETAIL")
            raise RestorationError("Safe synthetic file failure.")
        return original_file(item, *args, **kwargs)

    monkeypatch.setattr(widget._service, "restore_files", synchronized)
    monkeypatch.setattr(widget._service, "_restore_file", per_file)
    widget.start_restoration()
    try:
        pump_until(app, reached.is_set)
        first_output = widget.files[0].output_path
        contents = first_output.read_bytes()
        assert widget.open_output_button.isEnabled()
        widget.retranslate(Translator("en"))
        assert widget.files[0].output_path == first_output and widget._output_directory == output
        if outcome == "cancel":
            widget.cancel()
    finally:
        proceed.set()
    finish(app, widget, "_processing_worker")
    assert first_output.read_bytes() == contents and widget.files[0].status is Status.COMPLETED
    assert widget._output_directory == output and widget.open_output_button.isEnabled()
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())) or True)
    widget.open_output_button.click()
    assert opened == [output]
    assert not list(output.glob("*.tmp")) and "PRIVATE" not in widget.status_label.text()
    if outcome == "cancel":
        assert [item.status for item in widget.files] == [Status.COMPLETED, Status.CANCELLED, Status.SKIPPED]
        assert widget._displayed_summary.completed_files == 1 and widget._displayed_summary.cancelled
        assert widget.status_label.property("feedbackState") == "warning"
        assert widget.overall_progress.value() < widget.overall_progress.maximum()
    elif outcome == "file_error":
        assert [item.status for item in widget.files] == [Status.COMPLETED, Status.ERROR, Status.COMPLETED]
        assert widget._displayed_summary.completed_files == 2 and widget._displayed_summary.error_files == 1
        assert widget.status_label.property("feedbackState") == "error"
    else:
        assert [item.status for item in widget.files] == [Status.COMPLETED, Status.ERROR, Status.SKIPPED]
        assert widget._displayed_summary is None and not widget.summary_output.toPlainText()
        assert widget.status_label.property("feedbackState") == "error"
    assert widget.current_progress.maximum() == 1 and widget.current_progress.value() == 0


@pytest.mark.parametrize("phase", ["analysis", "processing"])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_finished_thread_with_queued_signals_is_not_retired_or_reenabled_by_retranslation(pages, phase, locale):
    app, widget, case, _ = pages(locale)
    widget.add_paths(case[1])
    if phase == "processing":
        analyze(app, widget)
        confirm_columns(widget)
    attribute = "_analysis_worker" if phase == "analysis" else "_processing_worker"
    widget.analyze_files() if phase == "analysis" else widget.start_restoration()
    worker = getattr(widget, attribute)
    assert worker.wait(15000) and not worker.isRunning()
    # Do not deliver queued progress/completion/finished callbacks yet.
    for target in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(target))
        assert getattr(widget, attribute) is worker
        assert not widget.analyze_button.isEnabled() and not widget.start_button.isEnabled()
        assert widget.status_label.property("feedbackState") == "neutral"
    app.processEvents()
    assert getattr(widget, attribute) is None
    assert widget.current_progress.maximum() == 1 and widget.current_progress.value() == 0
    assert widget.status_label.property("feedbackState") == "success"
    if phase == "processing":
        assert widget._displayed_summary.completed_files == 3 and widget.open_output_button.isEnabled()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_clear_invalidate_and_subsequent_failure_cannot_retranslate_old_success(pages, monkeypatch, locale):
    app, widget, case, output = pages(locale)
    widget.add_paths(case[1])
    analyze(app, widget)
    confirm_columns(widget)
    widget.start_restoration()
    finish(app, widget, "_processing_worker")
    published = {item.output_path: item.output_path.read_bytes() for item in widget.files}
    widget.clear_files()
    for target in ("en", "pt-BR"):
        widget.retranslate(Translator(target))
        assert widget._displayed_summary is None and not widget.summary_output.toPlainText()
        assert widget.status_label.text() == Translator(target).text("batch_restoration.status.initial")
        assert widget.status_label.property("feedbackState") == "neutral"
        assert widget.overall_progress.value() == 0 and not widget.open_output_button.isEnabled()
    widget.add_paths(case[1])
    analyze(app, widget)
    confirm_columns(widget)
    widget.invalidate_analysis()
    widget.retranslate(Translator(locale))
    assert all(item.status is Status.PENDING and item.output_path is None for item in widget.files)
    assert not widget.start_button.isEnabled()
    widget.start_restoration()
    assert widget._processing_worker is None
    analyze(app, widget)
    confirm_columns(widget)
    new_output = output.parent / "new-empty-output"
    new_output.mkdir()
    widget.output_field.setText(str(new_output))

    def failed(*args, **kwargs):
        raise BatchRestorationStructuralError("Safe synthetic structural failure.")

    monkeypatch.setattr(widget._service, "restore_files", failed)
    widget.start_restoration()
    finish(app, widget, "_processing_worker")
    widget.retranslate(Translator("en"))
    assert not list(new_output.iterdir()) and not widget.open_output_button.isEnabled()
    assert widget._output_directory is None and widget._displayed_summary is None
    assert not widget.summary_output.toPlainText() and widget.status_label.property("feedbackState") == "error"
    assert all(path.read_bytes() == data for path, data in published.items())


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("outcome", ["cancel", "failure"])
def test_new_analysis_invalidates_old_summary_but_preserves_actual_published_files(pages, monkeypatch, locale, outcome):
    app, widget, case, output = pages(locale)
    widget.add_paths(case[1])
    analyze(app, widget)
    confirm_columns(widget)
    widget.start_restoration()
    finish(app, widget, "_processing_worker")
    published = {item.output_path: item.output_path.read_bytes() for item in widget.files}
    assert widget._displayed_summary is not None and widget.summary_output.toPlainText()
    entered, proceed = Event(), Event()
    original = widget._service.analyze_files

    def interrupted(*args, **kwargs):
        entered.set()
        assert proceed.wait(10)
        if outcome == "failure":
            raise BatchRestorationError("Safe synthetic analysis failure.")
        return original(*args, **kwargs)

    monkeypatch.setattr(widget._service, "analyze_files", interrupted)
    widget.analyze_files()
    try:
        assert entered.wait(10)
        stale_at_start = widget._displayed_summary is not None or bool(widget.summary_output.toPlainText())
        if outcome == "cancel":
            widget.cancel()
    finally:
        proceed.set()
    finish(app, widget, "_analysis_worker")
    assert not stale_at_start, "A new analysis retained the previous operation's completed summary"
    for target in ("en", "pt-BR"):
        widget.retranslate(Translator(target))
        assert widget._displayed_summary is None and not widget.summary_output.toPlainText()
        assert widget.status_label.property("feedbackState") == ("warning" if outcome == "cancel" else "error")
        assert not widget.start_button.isEnabled()
    assert widget._output_directory == output and widget.open_output_button.isEnabled()
    assert all(path.read_bytes() == data for path, data in published.items())


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("interaction", ["enter", "escape", "no", "yes"])
def test_native_confirmation_preserves_roles_no_default_escape_and_results(pages, locale, interaction):
    app, widget, _, _ = pages(locale)

    def reply():
        dialog = app.activeModalWidget()
        assert dialog.windowTitle() == Translator(locale).text("batch_restoration.confirm.title")
        assert dialog.text() == Translator(locale).text("batch_restoration.confirm.text")
        assert dialog.standardButtons() == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        assert dialog.standardButton(dialog.defaultButton()) == dialog.standardButton(dialog.escapeButton()) == QMessageBox.StandardButton.No
        for button, key, role in ((QMessageBox.StandardButton.Yes, "yes", QMessageBox.ButtonRole.YesRole),
                                  (QMessageBox.StandardButton.No, "no", QMessageBox.ButtonRole.NoRole)):
            assert dialog.button(button).text() == Translator(locale).text("dialog.button." + key)
            assert dialog.buttonRole(dialog.button(button)) == role
        if interaction in ("yes", "no"):
            dialog.button(QMessageBox.StandardButton.Yes if interaction == "yes" else QMessageBox.StandardButton.No).click()
        else:
            QTest.keyClick(dialog, Qt.Key.Key_Return if interaction == "enter" else Qt.Key.Key_Escape)

    with modal_reply(widget, reply):
        result = BatchRestorationWidget._confirm_restoration(widget)
    assert result == (QMessageBox.StandardButton.Yes if interaction == "yes" else QMessageBox.StandardButton.No)
    assert widget._confirmation_dialog is None and widget._processing_worker is None


def test_saved_english_preference_does_not_activate_batch_restoration_locale(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    preferences = PreferencesService(tmp_path / "preferences.json")
    assert preferences.save_language("en").error is None
    window = build_localized_window(tmp_path, preferences_service=preferences)
    assert window.batch_restoration_widget._translator.locale == window._translator.locale == "pt-BR"
    assert window.batch_restoration_widget.start_button.text() == "Iniciar restauração"
    assert window.navigation.buttons[3].text() == "Restauração em lote"
