"""Semantic batch masking presentation must not change validation or publication."""

from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError
from pathlib import Path
import sqlite3
from threading import Event

import pytest
from PySide6.QtTest import QSignalSpy

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import AnonymizationResult, ColumnConfig, NormalizationFallback
from data_mask_studio.batch import (
    BatchExplanation as Explanation, BatchReason as Reason, BatchFileStatus as Status,
    CancellationRequest, invalidate_files,
)
import data_mask_studio.batch.service as service_module
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
from data_mask_studio.gui.batch_widget import _RESULT_REASONS
from data_mask_studio.gui.batch_worker import BatchProcessingWorker, BatchValidationWorker
from data_mask_studio.i18n import Translator, load_catalogs
from data_mask_studio.processing.planner import PlanningError
from data_mask_studio.vault import VaultEncryptionError
from test_batch_localization import batch_pages
from test_batch_profile_isolation import finish, pump_until


@pytest.mark.parametrize("case,contents,expected", [
    ("compatible", None, "File compatible with the profile."),
    ("missing", "Nome,IP,Nota\nSynthetic,192.0.2.1,Note\n", "Headers not found: Documento."),
    ("extra", "Nome,Documento,IP,Nota,Extra Ω {headers}\nSynthetic,123,192.0.2.1,Note,Literal\n",
     "Additional columns unknown to the profile: Extra Ω {headers}. Review the configuration."),
    ("both", "Unknown Ω {headers},Documento,IP,Nota\nSynthetic,123,192.0.2.1,Note\n",
     "Headers not found: Nome. Additional columns unknown to the profile: Unknown Ω {headers}. Review the configuration."),
])
def test_real_validation_explanations_follow_explicit_english(batch_pages, case, contents, expected):
    app, widget, paths, *_ = batch_pages()
    if contents is not None:
        paths[0].write_text(contents, encoding="utf-8")
    widget.add_paths([paths[0]])
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    widget.retranslate(Translator("en"))
    assert widget.file_table.item(0, 4).text() == expected
    assert widget.file_table.item(0, 4).toolTip() == expected


REASONS = (
    (Reason.COMPATIBLE, "Arquivo compatível com o perfil.", "File compatible with the profile."),
    (Reason.HEADER_MISMATCH,
     "Cabeçalhos não encontrados: Missing & {headers}. Colunas adicionais não conhecidas pelo perfil: Extra Ω. Revise a configuração.",
     "Headers not found: Missing & {headers}. Additional columns unknown to the profile: Extra Ω. Review the configuration."),
    (Reason.PROCESSING, "Processando arquivo.", "Processing file."),
    (Reason.COMPLETED, "Arquivo mascarado com sucesso.", "File masked successfully."),
    (Reason.CANCELLED, "Processamento cancelado.", "Processing cancelled."),
    (Reason.NOT_PROCESSED_CANCELLED, "Não processado devido ao cancelamento.", "Not processed because of cancellation."),
    (Reason.SKIPPED_STRUCTURAL, "Ignorado devido a uma falha estrutural.", "Skipped because of a structural failure."),
    (Reason.STRUCTURAL_FAILURE, "Falha estrutural no lote.", "Structural batch failure."),
)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("reason,portuguese,english", REASONS)
def test_each_semantic_category_uses_safe_templates_and_preserves_original(batch_pages, locale, reason, portuguese, english):
    _, widget, paths, _, _, _, output = batch_pages()
    widget.add_paths(paths[:1])
    item = widget.files[0]
    item.status = _RESULT_REASONS[reason][1]
    item.result_message = "Literal original message & {headers}"
    item.result_explanation = Explanation(reason,
        ("Missing & {headers}",), ("Extra Ω",)) if reason is Reason.HEADER_MISMATCH else Explanation(reason)
    if reason is Reason.COMPLETED:
        item.output_path = output / "literal & {filename}_masked.csv"
        item.processing_result = AnonymizationResult(item.output_path, 1, 0.1)
    widget._refresh_table()
    before = asdict(item)
    widget.retranslate(Translator(locale))
    assert widget.file_table.item(0, 4).text() == (portuguese if locale == "pt-BR" else english)
    assert widget.file_table.item(0, 4).toolTip() == widget.file_table.item(0, 4).text()
    assert asdict(item) == before


def test_optional_immutable_contract_and_catalog_parity(batch_pages):
    _, widget, paths, *_ = batch_pages()
    widget.add_paths(paths)
    assert all(item.result_explanation is None for item in widget.files)
    explanation = Explanation(Reason.HEADER_MISMATCH, ("A",), ("B",))
    with pytest.raises(FrozenInstanceError):
        explanation.reason = Reason.COMPATIBLE
    assert set(_RESULT_REASONS) == set(Reason)
    catalogs = load_catalogs()
    assert set(catalogs["en"]) == set(catalogs["pt-BR"])
    for identifier, _ in _RESULT_REASONS.values():
        if identifier is not None:
            assert identifier in catalogs["en"]
    error = PlanningError("literal", "second", missing_headers=("A",), extra_headers=("B",))
    assert error.args == ("literal", "second") and str(error) == str(ValueError("literal", "second"))
    assert error.missing_headers == ("A",) and error.extra_headers == ("B",)
    assert not PlanningError("legacy").missing_headers and not PlanningError().extra_headers


@pytest.mark.parametrize("metadata", [None, Explanation("future"), Explanation("compatible"),
    Explanation([]), "invalid metadata", Explanation(Reason.COMPATIBLE), Explanation(Reason.HEADER_MISMATCH)])
def test_unclassified_inconsistent_or_unknown_metadata_is_literal(batch_pages, metadata):
    _, widget, paths, *_ = batch_pages()
    widget.add_paths(paths[:1])
    item = widget.files[0]
    item.status = Status.INCOMPATIBLE
    item.result_message = "Cabeçalhos não encontrados: <literal> & {headers}."
    item.result_explanation = metadata
    widget._refresh_table()
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert widget.file_table.item(0, 4).text() == item.result_message


def test_untyped_planner_error_is_not_inferred_from_application_or_sentence(batch_pages, monkeypatch):
    app, widget, paths, *_ = batch_pages()
    paths[0].write_text("Unknown\nSynthetic\n", encoding="utf-8")
    literal = "Cabeçalhos não encontrados: Nome."
    def fail(*args):
        raise PlanningError(literal)
    monkeypatch.setattr(widget._profile_service, "build_plan", fail)
    widget.add_paths(paths[:1])
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    assert widget.files[0].missing_headers
    assert widget.files[0].status is Status.INCOMPATIBLE
    assert widget.files[0].result_explanation is None
    widget.retranslate(Translator("en"))
    assert widget.file_table.item(0, 4).text() == literal


def test_real_header_warning_is_preserved_as_literal_supplement(batch_pages):
    app, widget, paths, *_ = batch_pages()
    paths[0].write_text(",Name\nSynthetic,Example\n", encoding="utf-8")
    profile = widget._profile_service.create("Blank headers", [ColumnConfig("column_1", True, "FIELD"),
        ColumnConfig("Name")], inspection=inspect_csv(paths[0]))
    widget.refresh_profiles()
    widget.profile_combo.setCurrentIndex(widget.profile_combo.findData(profile.identifier))
    widget.add_paths(paths[:1])
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    item = widget.files[0]
    assert item.status is Status.COMPATIBLE and item.result_explanation.reason is Reason.COMPATIBLE
    warning = item.result_explanation.detail
    assert "column_1" in warning and "1 cabeçalho vazio" in warning
    raw = item.result_message
    for locale in ("en", "pt-BR"):
        widget.retranslate(Translator(locale))
        assert widget.file_table.item(0, 4).text() == Translator(locale).text("batch.result.compatible") + " " + warning
        assert item.result_message == raw


def test_retranslate_analyzed_rows_preserves_objects_focus_scroll_and_performs_no_io(batch_pages, monkeypatch):
    app, widget, paths, profile, _, _, _ = batch_pages()
    widget.add_paths(paths)
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    seed = tuple(widget.files)
    widget.files.extend(deepcopy(seed[index % 2]) for index in range(58))
    widget._refresh_table()
    widget.resize(1100, 650)
    widget.show()
    app.processEvents()
    widget.file_table.selectRow(30)
    widget.file_table.setFocus()
    widget.file_table.verticalScrollBar().setValue(20)
    assert widget.file_table.verticalScrollBar().value() > 0
    files, state = tuple(widget.files), [asdict(item) for item in widget.files]
    cells = tuple(widget.file_table.item(row, col) for row in range(60) for col in range(5))
    selected = [(index.row(), index.column()) for index in widget.file_table.selectedIndexes()]
    scroll = widget.file_table.verticalScrollBar().value(), widget.file_table.horizontalScrollBar().value()
    focus, destination = app.focusWidget(), widget.output_field.text()
    progress = widget.overall_progress.minimum(), widget.overall_progress.maximum(), widget.overall_progress.value()
    spies = [QSignalSpy(widget.file_table.itemChanged), QSignalSpy(widget.file_table.itemSelectionChanged),
             QSignalSpy(widget.profile_combo.currentIndexChanged)]
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation may not access files, validate or start workers")
    with monkeypatch.context() as guard:
        for name in ("validate", "process"):
            guard.setattr(widget._service, name, forbidden)
        for name in ("list_profiles", "apply", "build_plan"):
            guard.setattr(widget._profile_service, name, forbidden)
        for worker in (BatchProcessingWorker, BatchValidationWorker):
            guard.setattr(worker, "start", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        for name in ("open", "stat", "iterdir", "unlink", "rglob"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            widget.retranslate(translator)
            app.processEvents()
            assert all(actual is original for actual, original in zip(widget.files, files, strict=True))
            assert [asdict(item) for item in widget.files] == state
            assert tuple(widget.file_table.item(row, col) for row in range(60) for col in range(5)) == cells
            assert [(index.row(), index.column()) for index in widget.file_table.selectedIndexes()] == selected
            assert (widget.file_table.verticalScrollBar().value(), widget.file_table.horizontalScrollBar().value()) == scroll
            assert app.focusWidget() is focus and widget.output_field.text() == destination
            assert widget.profile_combo.currentData() == profile.identifier and widget.profile_combo.currentText() == profile.name
            assert (widget.overall_progress.minimum(), widget.overall_progress.maximum(), widget.overall_progress.value()) == progress
            assert widget._validation_worker is None and widget._processing_worker is None
            assert not widget._requires_revalidation
    assert all(spy.count() == 0 for spy in spies)


@pytest.mark.parametrize("phase", ["validation", "processing"])
def test_real_active_worker_retranslation_keeps_execution_and_rows_intact(batch_pages, monkeypatch, phase):
    app, widget, paths, *_ = batch_pages()
    widget.add_paths(paths)
    if phase == "processing":
        widget.validate_files()
        finish(app, widget, "_validation_worker")
    widget.resize(1100, 650)
    widget.show()
    app.processEvents()
    method = "validate" if phase == "validation" else "process"
    original = getattr(widget._service, method)
    entered, release = Event(), Event()
    terminal = Status.COMPATIBLE if phase == "validation" else Status.COMPLETED
    def paused(*args, file_callback, **kwargs):
        def changed(item):
            file_callback(item)
            if item is widget.files[0] and item.status is terminal:
                entered.set()
                assert release.wait(10)
        return original(*args, file_callback=changed, **kwargs)
    monkeypatch.setattr(widget._service, method, paused)
    (widget.validate_files if phase == "validation" else widget.start_processing)()
    attribute = "_validation_worker" if phase == "validation" else "_processing_worker"
    worker = getattr(widget, attribute)
    try:
        pump_until(app, entered.is_set)
        widget.file_table.selectRow(0)
        widget.file_table.setFocus()
        app.processEvents()
        cells = tuple(widget.file_table.item(row, col) for row in range(2) for col in range(5))
        state = [asdict(item) for item in widget.files]
        for locale in ("en", "pt-BR", "en"):
            widget.retranslate(Translator(locale))
            app.processEvents()
            assert getattr(widget, attribute) is worker and worker.isRunning()
            assert [asdict(item) for item in widget.files] == state
            assert tuple(widget.file_table.item(row, col) for row in range(2) for col in range(5)) == cells
            assert widget.file_table.currentRow() == 0 and widget.file_table.hasFocus()
            assert widget.file_table.item(0, 4).text() == Translator(locale).text(
                "batch.result.compatible" if phase == "validation" else "batch.result.completed")
    finally:
        release.set()
    finish(app, widget, attribute)
    assert all(item.status is terminal for item in widget.files)


@pytest.mark.parametrize("outcome", ["cancel_before", "cancel_during", "structural", "file_error"])
def test_real_partial_publication_and_typed_terminal_reasons(batch_pages, monkeypatch, outcome):
    app, widget, paths, _, _, _, output = batch_pages()
    third = paths[0].with_name("third.csv")
    third.write_bytes(paths[0].read_bytes())
    widget.add_paths([*paths, third])
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    entered, release = Event(), Event()
    anonymize, process = service_module.anonymize_csv, widget._service.process
    if outcome == "cancel_before":
        def pause_before(*args, file_callback, **kwargs):
            def changed(item):
                file_callback(item)
                if item is widget.files[0] and item.status is Status.COMPLETED:
                    entered.set()
                    assert release.wait(10)
            return process(*args, file_callback=changed, **kwargs)
        monkeypatch.setattr(widget._service, "process", pause_before)
    else:
        def file_operation(source, *args, **kwargs):
            if source == paths[1]:
                entered.set()
                assert release.wait(10)
                if outcome == "structural":
                    raise VaultEncryptionError("PRIVATE_SYNTHETIC_KEY_DETAIL")
                if outcome == "file_error":
                    raise CSVAnonymizationError("Literal service error & {count}")
            return anonymize(source, *args, **kwargs)
        monkeypatch.setattr(service_module, "anonymize_csv", file_operation)
    widget.start_processing()
    worker = widget._processing_worker
    try:
        pump_until(app, entered.is_set)
        published = widget.files[0].output_path
        published_bytes = published.read_bytes()
        widget.retranslate(Translator("en"))
        assert widget._processing_worker is worker and worker.isRunning()
        assert widget.open_output_button.isEnabled() and widget._output_directory == output
        if outcome != "cancel_before":
            assert widget.file_table.item(1, 4).text() == "Processing file."
        if outcome.startswith("cancel"):
            widget.cancel()
    finally:
        release.set()
    finish(app, widget, "_processing_worker")
    expected = {
        "cancel_before": [Reason.COMPLETED, Reason.NOT_PROCESSED_CANCELLED, Reason.NOT_PROCESSED_CANCELLED],
        "cancel_during": [Reason.COMPLETED, Reason.CANCELLED, Reason.NOT_PROCESSED_CANCELLED],
        "structural": [Reason.COMPLETED, Reason.STRUCTURAL_FAILURE, Reason.SKIPPED_STRUCTURAL],
        "file_error": [Reason.COMPLETED, None, Reason.COMPLETED],
    }[outcome]
    assert [item.result_explanation.reason if item.result_explanation else None for item in widget.files] == expected
    assert [result.result_explanation for result in widget._displayed_summary.results] == [item.result_explanation for item in widget.files]
    state = [asdict(item) for item in widget.files]
    for locale in ("pt-BR", "en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert [asdict(item) for item in widget.files] == state
        assert published.read_bytes() == published_bytes and widget.open_output_button.isEnabled()
        assert "PRIVATE_SYNTHETIC_KEY_DETAIL" not in widget.file_table.item(1, 4).text()
        if outcome == "file_error":
            assert widget.file_table.item(1, 4).text() == "Literal service error & {count}"
    assert not list(output.glob("*.tmp"))
    assert widget._current_progress is None and not widget.current_progress_label.text()
    assert not widget.has_running_workers()


def test_preflight_header_mismatch_is_typed_without_reusing_old_compatibility(batch_pages):
    app, widget, paths, *_ = batch_pages()
    widget.add_paths(paths)
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    paths[0].write_text("Nome,IP,Nota\nSynthetic,192.0.2.1,Note\n", encoding="utf-8")
    widget.start_processing()
    finish(app, widget, "_processing_worker")
    assert widget.files[0].status is Status.INCOMPATIBLE and widget.files[0].output_path is None
    assert widget.files[0].result_explanation == Explanation(Reason.HEADER_MISMATCH, ("Documento",))
    widget.retranslate(Translator("en"))
    assert widget.file_table.item(0, 4).text() == "Headers not found: Documento."
    assert widget.files[1].status is Status.COMPLETED


def test_real_outputs_counters_original_messages_and_fallbacks_match_between_locales(batch_pages):
    outcomes = []
    for locale in ("pt-BR", "en"):
        app, widget, paths, _, repository, _, _ = batch_pages(locale)
        original_sources = {path: path.read_bytes() for path in paths}
        widget.add_paths(paths)
        widget.validate_files()
        finish(app, widget, "_validation_worker")
        widget.start_processing()
        finish(app, widget, "_processing_worker")
        assert all(item.result_explanation == Explanation(Reason.COMPLETED) for item in widget.files)
        second = widget.files[1]
        assert second.normalization_fallbacks == (NormalizationFallback("IP", 1),)
        assert "IP: 1" in widget.file_table.item(1, 4).text()
        assert "PRIVATE-INVALID-IP" not in widget.file_table.item(1, 4).text()
        assert all(path.read_bytes() == data for path, data in original_sources.items())
        outcomes.append((tuple((item.status, item.records_processed, item.new_mappings, item.updated_mappings,
            item.result_message, item.normalization_fallbacks, item.output_path.name, item.output_path.read_bytes())
            for item in widget.files), repository.count()))
    assert outcomes[0] == outcomes[1]


def test_invalidation_clears_semantics_but_keeps_published_files(batch_pages):
    app, widget, paths, *_ = batch_pages()
    widget.add_paths(paths)
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    widget.start_processing()
    finish(app, widget, "_processing_worker")
    published = {item.output_path: item.output_path.read_bytes() for item in widget.files}
    invalidate_files(widget.files)
    assert all(item.status is Status.PENDING and item.result_explanation is None and not item.result_message
               and item.output_path is None for item in widget.files)
    assert all(path.read_bytes() == data for path, data in published.items())


def test_cancellation_before_preflight_never_publishes_or_prepares_keys(batch_pages, monkeypatch):
    app, widget, paths, profile, _, _, output = batch_pages()
    widget.add_paths(paths)
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    cancellation = CancellationRequest()
    cancellation.request()
    def forbidden(*args):
        pytest.fail("Preflight cancellation must not prepare keys or vault")
    monkeypatch.setattr(widget._key_provider, "get_key", forbidden)
    summary = widget._service.process(widget.files, profile, output,
        widget._key_provider, forbidden, cancellation=cancellation)
    assert summary.completed_files == 0 and summary.cancelled_or_skipped_files == 2
    assert all(item.status is Status.CANCELLED and item.output_path is None
        and item.result_explanation == Explanation(Reason.NOT_PROCESSED_CANCELLED) for item in widget.files)
    widget._refresh_table()
    widget.retranslate(Translator("en"))
    assert all(widget.file_table.item(row, 4).text() == "Not processed because of cancellation." for row in range(2))
    assert not list(output.iterdir())


def test_inspection_error_clears_old_compatible_metadata_and_stays_literal(batch_pages):
    app, widget, paths, *_ = batch_pages()
    widget.add_paths(paths[:1])
    widget.validate_files()
    finish(app, widget, "_validation_worker")
    item = widget.files[0]
    assert item.result_explanation == Explanation(Reason.COMPATIBLE)
    item.path = paths[0].with_name("nonexistent.csv")
    widget._service.validate(widget.files, widget._selected_profile())
    assert item.status is Status.INCOMPATIBLE and item.result_explanation is None
    assert item.result_message
    widget._refresh_table()
    widget.retranslate(Translator("en"))
    assert widget.file_table.item(0, 4).text() == item.result_message
