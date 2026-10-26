"""Batch display labels cannot authorize processing or mutate execution state."""

from dataclasses import asdict, replace
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import AnonymizationResult, ColumnAction, ColumnConfig, NormalizationFallback
from data_mask_studio.app import create_application
from data_mask_studio.batch import BatchError, BatchFile, BatchFileStatus, BatchProgress, BatchSummary
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.gui.batch_widget import BatchWidget, _render_summary
from data_mask_studio.gui.batch_worker import BatchProcessingWorker, BatchValidationWorker
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository
from test_batch_profile_isolation import page, change_profile, finish, pause_after_first, pump_until, validate
from test_dialog_consistency import modal_reply
from test_main_navigation import build_window


class SyntheticKey:
    def get_key(self):
        return b"H" * 32


@pytest.fixture
def batch_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    pages = []

    def create(locale="pt-BR"):
        root = tmp_path / f"{locale}-{len(pages)}"
        root.mkdir()
        first = root / "first.csv"
        second = root / "second.csv"
        first.write_text("Nome,Documento,IP,Nota\nAlice Example,123.456,192.0.2.1,Test\n", encoding="utf-8")
        second.write_text("Documento,Nome,IP,Nota\n123.456,Alice Example,PRIVATE-INVALID-IP,Test\n", encoding="utf-8")
        profiles = ProfileService(ProfileRepository(root / "profiles.json"))
        composite = CompositeColumnConfig("PERSON_DOCUMENT", "PAIR", (
            CompositeSource(SourceColumnRef("Nome"), Rule.PERSON_NAME),
            CompositeSource(SourceColumnRef("Documento"), Rule.DIGITS_ONLY),
        ), UUID("12345678-1234-5678-1234-567812345678"), ColumnAction.MASK)
        profile = profiles.create("Perfil Ω & literal", [
            ColumnConfig("Nome", normalization_rule=Rule.PERSON_NAME, output_name="Name"),
            ColumnConfig("Documento", True, "DOC", Rule.DIGITS_ONLY, output_name="Document"),
            ColumnConfig("IP", True, "IP", Rule.IP_ADDRESS),
            ColumnConfig("Nota", action=ColumnAction.EXCLUDE),
        ], inspection=inspect_csv(first), composites=(composite,))
        repository = VaultRepository(root / "vault.db", VaultCipher(b"V" * 32))
        translator = Translator(locale)
        widget = BatchWidget(profiles, SyntheticKey(), lambda: repository, translator=translator)
        pages.append(widget)
        output = root / "output"
        output.mkdir()
        widget.output_field.setText(str(output))
        return app, widget, (first, second), profile, repository, translator, output

    yield create
    for widget in pages:
        assert widget.stop_workers()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_batch_labels_headers_empty_states_and_profile_identity(batch_pages, locale):
    _, widget, _, profile, _, translator, _ = batch_pages(locale)
    assert widget.start_button.text() == translator.text("batch.start")
    assert widget.add_files_button.text() == translator.text("batch.add_files")
    assert widget.validate_button.text() == translator.text("batch.validate")
    assert widget.profile_combo.currentData() == profile.identifier
    assert widget.profile_combo.currentText() == profile.name
    assert widget.output_field.accessibleName() == translator.text("batch.output_accessible")
    assert widget.file_table.empty_text == translator.text("batch.files_empty")
    assert widget.summary_output.empty_text == translator.text("batch.summary_empty")
    assert widget.current_progress_label.text() == translator.text("batch.current.idle")
    assert widget.status_label.text() == translator.text("batch.status.initial")
    assert [widget.file_table.horizontalHeaderItem(i).text() for i in range(5)] == [
        translator.text(f"batch.table.{name}") for name in ("file", "path", "status", "columns", "result")
    ]
    assert not widget.validate_button.isEnabled() and not widget.start_button.isEnabled()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("case", ["pending", "zero_compatible", "mixed", "compatible", "revalidation"])
def test_start_eligibility_and_real_publication_are_independent_of_language(batch_pages, locale, case):
    app, widget, paths, profile, repository, translator, output = batch_pages(locale)
    incompatible_paths = paths if case == "zero_compatible" else paths[1:] if case == "mixed" else ()
    for path in incompatible_paths:
        path.write_text("Unknown\nSynthetic\n", encoding="utf-8")
    widget.add_paths(paths)
    if case != "pending":
        widget.validate_files()
        finish(app, widget, "_validation_worker")
        expected = [BatchFileStatus.INCOMPATIBLE if path in incompatible_paths
                    else BatchFileStatus.COMPATIBLE for path in paths]
        assert [item.status for item in widget.files] == expected
        assert not widget._requires_revalidation
    if case == "revalidation":
        configurations = [ColumnConfig(
            column.header, prefix=column.prefix, normalization_rule=column.normalization_rule,
            action=column.action,
            output_name="Renamed Name" if column.header == "Nome" else column.output_name,
        ) for column in profile.columns]
        widget._profile_service.update(profile.identifier, configurations, inspection=inspect_csv(paths[0]))
        widget.refresh_profiles()
        assert widget._requires_revalidation
        assert all(item.status is BatchFileStatus.PENDING for item in widget.files)

    eligible = case in ("mixed", "compatible")
    widget.resize(1180, 780)
    widget.show()
    app.processEvents()
    assert widget.start_button.text() == translator.text("batch.start")
    assert widget.start_button.isEnabled() is eligible
    clicked = QSignalSpy(widget.start_button.clicked)
    QTest.mouseClick(widget.start_button, Qt.MouseButton.LeftButton)
    if not eligible:
        widget.start_button.click()  # Neither mouse nor programmatic clicks bypass disabled state.
        assert clicked.count() == 0 and widget._processing_worker is None
        before = [asdict(item) for item in widget.files]
        widget.start_processing()  # Independent handler gates must also reject the operation.
        message_id = "batch.error.no_compatible" if case == "zero_compatible" else "batch.error.validate_first"
        assert widget.status_label.text() == translator.text(message_id)
        assert widget.status_label.property("feedbackState") == "error"
        assert widget._processing_worker is None and [asdict(item) for item in widget.files] == before
        assert not list(output.iterdir()) and repository.count() == 0
        assert not widget.open_output_button.isEnabled()
        if case != "revalidation":
            return
        widget.validate_files()
        finish(app, widget, "_validation_worker")
        assert not widget._requires_revalidation and widget.start_button.isEnabled()
        QTest.mouseClick(widget.start_button, Qt.MouseButton.LeftButton)

    assert clicked.count() == 1 and widget._processing_worker is not None
    finish(app, widget, "_processing_worker")
    expected_outputs = {path.stem + "_masked.csv" for path in paths if path not in incompatible_paths}
    assert {path.name for path in output.iterdir()} == expected_outputs
    assert widget._displayed_summary.completed_files == len(expected_outputs)
    assert widget._displayed_summary.incompatible_files == len(incompatible_paths)
    for item in widget.files:
        if item.path in incompatible_paths:
            assert item.status is BatchFileStatus.INCOMPATIBLE and item.output_path is None
        else:
            assert item.status is BatchFileStatus.COMPLETED and item.output_path.is_file()
    assert widget._output_directory == output and widget.open_output_button.isEnabled()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_all_typed_statuses_translate_without_rewriting_service_messages(batch_pages, locale):
    _, widget, paths, _, _, translator, output = batch_pages(locale)
    widget.files = [BatchFile(paths[0].with_name(f"{status.value}.csv"), status=status,
                              result_message="Safe free-form service warning.") for status in BatchFileStatus]
    completed = next(item for item in widget.files if item.status is BatchFileStatus.COMPLETED)
    completed.processing_result = AnonymizationResult(output / "completed_masked.csv", 3, 0.1,
                                                     normalization_fallbacks=(NormalizationFallback("IP", 2),))
    widget._refresh_table()
    before = [asdict(item) for item in widget.files]
    for row, item in enumerate(widget.files):
        assert widget.file_table.item(row, 2).text() == translator.text(f"batch.file_status.{item.status.value}")
        if item.status is BatchFileStatus.COMPLETED:
            assert widget.file_table.item(row, 4).text().startswith(translator.text("batch.result.completed"))
            assert "IP: 2" in widget.file_table.item(row, 4).text()
        else:
            assert widget.file_table.item(row, 4).text() == item.result_message
    widget.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert [asdict(item) for item in widget.files] == before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_added_file_and_record_counts_use_singular_and_plural(batch_pages, locale):
    _, widget, paths, _, _, translator, _ = batch_pages(locale)
    assert widget.add_paths(paths[:1]) == 1
    assert widget.status_label.text() == translator.plural("batch.files_added", 1)
    widget.clear_files()
    assert widget.add_paths(paths) == 2
    assert widget.status_label.text() == translator.plural("batch.files_added", 2)
    for count in (0, 1, 2):
        widget._progress_changed(BatchProgress(1, 2, "literal & name.csv", count, 0, 0))
        assert translator.plural("batch.records", count) in widget.current_progress_label.text()
    assert widget.current_progress_label.textFormat() == Qt.TextFormat.PlainText


def test_retranslate_preserves_file_objects_cells_selection_report_focus_and_gate(batch_pages):
    app, widget, paths, profile, _, _, output = batch_pages()
    widget.add_paths(paths)
    widget.files[0].status = BatchFileStatus.COMPLETED
    widget.files[0].output_path = output / "first_masked.csv"
    widget.files[0].processing_result = AnonymizationResult(widget.files[0].output_path, 2, 0.1)
    widget.files[1].status = BatchFileStatus.ERROR
    widget.files[1].result_message = "Synthetic safe file error."
    widget._refresh_table()
    summary = BatchSummary(2, 2, 1, 0, 1, 0, 2, 1, 0, 0.25, output)
    widget.overall_progress.setRange(0, 2)
    widget._processing_completed(summary)
    widget._requires_revalidation = True
    widget._update_actions()
    widget.show()
    app.processEvents()
    widget.file_table.selectRow(1)
    widget.file_table.setFocus()
    cursor = widget.summary_output.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    widget.summary_output.setTextCursor(cursor)
    objects = widget.files, tuple(widget.files), tuple(widget.file_table.item(r, c) for r in range(2) for c in range(5))
    state = [asdict(item) for item in widget.files]
    selection = [(index.row(), index.column()) for index in widget.file_table.selectedIndexes()]
    paths_before = widget.output_field.text(), widget._output_directory
    document = widget._profile_service.repository.path.read_bytes()
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        app.processEvents()
        assert (widget.files, tuple(widget.files), tuple(widget.file_table.item(r, c) for r in range(2) for c in range(5))) == objects
        assert [asdict(item) for item in widget.files] == state
        assert [(index.row(), index.column()) for index in widget.file_table.selectedIndexes()] == selection
        assert widget.file_table.currentRow() == 1 and widget.file_table.hasFocus()
        assert widget.summary_output.textCursor().anchor() == 3
        assert widget.summary_output.textCursor().position() == 9
        assert widget.profile_combo.currentData() == profile.identifier and widget.profile_combo.currentText() == profile.name
        assert (widget.output_field.text(), widget._output_directory) == paths_before
        assert widget._profile_service.repository.path.read_bytes() == document
        assert widget._displayed_summary is summary and widget.status_label.property("feedbackState") == "error"
        assert widget._requires_revalidation and not widget.start_button.isEnabled()


def test_real_batch_workers_produce_byte_identical_outputs_and_equal_counts(batch_pages, monkeypatch):
    outputs, metrics = [], []
    original_start = BatchProcessingWorker.start
    for locale in ("pt-BR", "en"):
        app, widget, paths, _, repository, translator, output = batch_pages(locale)
        widget.add_paths(paths)
        widget.validate_files()
        finish(app, widget, "_validation_worker")
        assert not widget._requires_revalidation and widget.start_button.isEnabled()
        assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
        summaries, failures = [], []

        def observe_then_start(worker):
            worker.completed.connect(summaries.append)
            worker.failed.connect(failures.append)
            original_start(worker)

        monkeypatch.setattr(BatchProcessingWorker, "start", observe_then_start)
        widget.start_processing()
        worker = widget._processing_worker
        assert worker is not None
        finish(app, widget, "_processing_worker")
        assert not failures and len(summaries) == 1
        summary = summaries[0]
        metrics.append((summary.selected_files, summary.completed_files, summary.error_files,
                        summary.records_processed, summary.new_mappings, summary.updated_mappings,
                        summary.normalization_fallbacks))
        outputs.append({item.output_path.name: item.output_path.read_bytes() for item in widget.files})
        assert set(outputs[-1]) == {"first_masked.csv", "second_masked.csv"}
        assert all(item.status is BatchFileStatus.COMPLETED and item.processing_result for item in widget.files)
        assert widget._output_directory == output and widget.open_output_button.isEnabled()
        assert widget.status_label.property("feedbackState") == "warning"  # Real normalization fallback.
        assert widget.summary_output.toPlainText() == _render_summary(summary, translator)
        assert "PRIVATE-INVALID-IP" not in widget.summary_output.toPlainText()
        assert all("PRIVATE-INVALID-IP" not in widget.file_table.item(row, 4).text() for row in range(2))
        assert repository.count() == 3
    assert outputs[0] == outputs[1] and metrics[0] == metrics[1]


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("case,message_id", [
    ("empty", "batch.error.no_files"), ("profile", "batch.error.no_profile"),
    ("pending", "batch.error.validate_first"), ("incompatible", "batch.error.no_compatible"),
    ("output_missing", "batch.error.no_output"), ("output_invalid", "batch.error.invalid_output"),
])
def test_localized_configuration_errors_keep_handler_gates_authoritative(batch_pages, monkeypatch, locale, case, message_id):
    _, widget, paths, _, _, translator, _ = batch_pages(locale)
    monkeypatch.setattr(BatchProcessingWorker, "start", lambda _: pytest.fail("Invalid configuration started a worker"))
    if case != "empty":
        widget.add_paths(paths)
    if case == "profile":
        widget.profile_combo.setCurrentIndex(-1)
    if case in ("incompatible", "output_missing", "output_invalid"):
        for item in widget.files:
            item.status = BatchFileStatus.INCOMPATIBLE if case == "incompatible" else BatchFileStatus.COMPATIBLE
    if case == "output_missing":
        widget.output_field.clear()
    elif case == "output_invalid":
        widget.output_field.setText(str(paths[0]))
    before = [asdict(item) for item in widget.files]
    (widget.validate_files if case in ("empty", "profile") else widget.start_processing)()
    assert widget.status_label.text() == translator.text(message_id)
    assert widget.status_label.property("feedbackState") == "error"
    assert widget._validation_worker is None and widget._processing_worker is None
    assert [asdict(item) for item in widget.files] == before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("outcome,expected", [("complete", "success"), ("incompatible", "warning"),
                                              ("failure", "error"), ("cancel", "warning")])
def test_structured_summary_metrics_and_severity_remain_distinct(batch_pages, locale, outcome, expected):
    _, widget, _, _, _, translator, output = batch_pages(locale)
    incompatible, errors, cancelled = int(outcome == "incompatible"), int(outcome == "failure"), int(outcome == "cancel")
    summary = BatchSummary(3, 3 - incompatible, 3 - incompatible - errors - cancelled, incompatible,
                           errors, cancelled, 12, 4, 5, 2.25, output)
    widget._processing_completed(summary)
    text = widget.summary_output.toPlainText()
    for key, count in (("selected", 3), ("completed", summary.completed_files), ("incompatible", incompatible),
                       ("errors", errors), ("cancelled", cancelled), ("records", 12), ("new_mappings", 4), ("updated_mappings", 5)):
        assert translator.text(f"batch.summary.{key}", count=count) in text
    assert translator.text("batch.summary.duration", seconds="2.25") in text
    assert str(output) in text and widget.status_label.property("feedbackState") == expected
    widget.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert widget.status_label.property("feedbackState") == expected and widget._displayed_summary is summary


@pytest.mark.parametrize("phase", ["validation", "processing"])
@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_retranslation_preserves_busy_workers_cancel_and_fresh_validation_gates(batch_pages, monkeypatch, phase, outcome):
    app, widget, paths, _, _, _, output = batch_pages("en")
    widget.add_paths(paths)
    for item in widget.files:
        item.status = BatchFileStatus.COMPATIBLE
    widget._refresh_table()
    old = BatchSummary(2, 2, 2, 0, 0, 0, 2, 1, 0, 0.1, output)
    widget._processing_completed(old)
    worker_type = BatchValidationWorker if phase == "validation" else BatchProcessingWorker
    monkeypatch.setattr(worker_type, "start", lambda self: None)
    (widget.validate_files if phase == "validation" else widget.start_processing)()
    attribute = "_validation_worker" if phase == "validation" else "_processing_worker"
    worker = getattr(widget, attribute)
    before = tuple(widget.files), widget._requires_revalidation, widget.overall_progress.minimum(), widget.overall_progress.maximum()
    for target in ("pt-BR", "en"):
        widget.retranslate(Translator(target))
        assert getattr(widget, attribute) is worker
        assert (tuple(widget.files), widget._requires_revalidation, widget.overall_progress.minimum(), widget.overall_progress.maximum()) == before
        assert not widget.start_button.isEnabled() and not widget.validate_button.isEnabled()
        assert widget.cancel_button.isEnabled() and widget._displayed_summary is None
    if outcome == "failure":
        worker.failed.emit(RuntimeError("PRIVATE-EXCEPTION"))
        assert "PRIVATE-EXCEPTION" not in widget.status_label.text()
        assert widget.status_label.property("feedbackState") == "error"
    else:
        widget.cancel()
        assert worker._cancellation.is_requested() and not widget.cancel_button.isEnabled()
        widget.retranslate(Translator("pt-BR"))
        assert not widget.cancel_button.isEnabled()
        if phase == "validation":
            worker.completed.emit()
            assert widget.status_label.property("feedbackState") == "warning"
    worker.finished.emit()
    app.processEvents()
    assert getattr(widget, attribute) is None and widget._displayed_summary is None
    assert not widget.summary_output.toPlainText() and not widget.current_progress_label.text()
    widget.retranslate(Translator("en"))
    assert not widget.summary_output.toPlainText() and widget.status_label.property("feedbackState") != "success"
    if phase == "validation":
        assert widget._requires_revalidation and not widget.start_button.isEnabled()
        widget.start_processing()
        assert widget._processing_worker is None


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_file_directory_dialogs_keep_filters_order_duplicates_and_cancel(batch_pages, tmp_path, monkeypatch, locale):
    _, widget, paths, _, _, translator, _ = batch_pages(locale)
    calls = []
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: calls.append(args) or ([str(paths[1]), str(paths[0]), str(paths[1])], ""))
    widget._choose_files()
    assert [item.path for item in widget.files] == [paths[1], paths[0]]
    assert calls[0][1] == translator.text("batch.files_title") and calls[0][3] == translator.text("masking.file.filter")
    assert "*.csv" in calls[0][3]
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "z.csv").write_bytes(paths[0].read_bytes())
    (folder / "A.CSV").write_bytes(paths[0].read_bytes())
    (folder / "unsupported.txt").write_text("synthetic", encoding="utf-8")
    (folder / "nested").mkdir()
    (folder / "nested" / "ignored.csv").write_bytes(paths[0].read_bytes())
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: calls.append(args) or str(folder))
    widget._choose_folder()
    assert [item.path.name for item in widget.files][-2:] == ["A.CSV", "z.csv"]
    assert len(widget.files) == 4 and calls[-1][1] == translator.text("batch.add_folder")
    snapshot = tuple(widget.files), widget.output_field.text(), widget.status_label.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([], ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")
    widget._choose_files()
    widget._choose_folder()
    widget._choose_output_directory()
    assert (tuple(widget.files), widget.output_field.text(), widget.status_label.text()) == snapshot


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_create_output_confirmation_keeps_no_default_and_no_side_effects(batch_pages, locale, key):
    app, widget, paths, _, _, translator, output = batch_pages(locale)
    widget.add_paths(paths)
    for item in widget.files:
        item.status = BatchFileStatus.COMPATIBLE
    destination = output / "not-created"
    widget.output_field.setText(str(destination))

    def refuse():
        box = app.activeModalWidget()
        assert box is widget._output_confirmation
        no = box.button(QMessageBox.StandardButton.No)
        assert box.defaultButton() is no and box.escapeButton() is no
        assert box.windowTitle() == translator.text("batch.output.create_title")
        assert box.button(QMessageBox.StandardButton.Yes).text() == translator.text("dialog.button.yes")
        assert QCoreApplication.translate("QPlatformTheme", "&No") == "&Não"
        QTest.keyClick(box, key)

    with modal_reply(widget, refuse):
        widget.start_processing()
    assert not destination.exists() and widget._processing_worker is None


@pytest.mark.parametrize("change", ["update", "delete"])
def test_english_native_batch_profile_notifications_remain_deferred(page, monkeypatch, change):
    app, window, widget, _, profile, output = page
    window.retranslate(Translator("en"))
    validate(page)
    pause = pause_after_first(page, monkeypatch, "processing")
    widget.start_processing()
    worker = widget._processing_worker
    try:
        pump_until(app, pause.reached.is_set)
        first = widget.files[0].output_path
        change_profile(page, change, navigation=True)
        assert widget._profiles_refresh_pending and widget._selected_profile() == profile
        window.retranslate(Translator("pt-BR"))
        window.retranslate(Translator("en"))
        assert widget._processing_worker is worker and widget.files[0].output_path == first
        assert widget.files[0].status is BatchFileStatus.COMPLETED
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, widget, "_processing_worker")
    assert all(item.status is BatchFileStatus.COMPLETED and item.output_path.is_file() for item in widget.files)
    assert widget._requires_revalidation and not widget.start_button.isEnabled()
    assert widget._output_directory == output and widget.open_output_button.isEnabled()
    assert "Files completed: 2" in widget.summary_output.toPlainText()
    widget.start_processing()
    assert widget._processing_worker is None


def test_main_window_shares_explicit_translator_but_does_not_activate_saved_english(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    assert PreferencesService().save_language("en").error is None
    create_application([])
    window = build_window(tmp_path)
    assert window.batch_widget.start_button.text() == "Iniciar mascaramento"
    translator = Translator("en")
    window.retranslate(translator)
    assert window.batch_widget._translator is translator
    assert window.batch_widget.start_button.text() == "Start masking"
    assert window.maintenance_widget.refresh_button.text() == "Atualizar diagnóstico"


def test_freeform_batch_errors_survive_retranslation_without_old_success(batch_pages):
    _, widget, paths, _, _, _, _ = batch_pages("en")
    widget.add_paths(paths)
    widget._set_status_id("batch.status.completed", state="success")
    error = BatchError("Synthetic safe service error; unchanged contract.")
    widget._worker_failed(error)
    widget.retranslate(Translator("pt-BR"))
    assert widget.status_label.text() == str(error)
    assert widget.status_label.property("feedbackState") == "error"
