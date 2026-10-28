"""Typed per-file presentation must not affect restoration or literal evidence."""

from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError
from pathlib import Path
import sqlite3
from threading import Event

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.batch_restoration import (
    BatchMissingCodePolicy, BatchRestorationOptions, BatchRestorationExplanation as Explanation,
    BatchRestorationReason as Reason, BatchRestorationStatus as Status, invalidate_files,
)
from data_mask_studio.gui.batch_restoration_widget import BatchRestorationWidget, _RESULT_REASONS
from data_mask_studio.i18n import Translator, load_catalogs
from data_mask_studio.restoration import RepresentationPolicy, RestorationCancelled, RestorationError
from data_mask_studio.vault import VaultError
from test_batch_restoration import repository, CODE, MISSING
from test_batch_restoration_localization import analyze, confirm_columns, finish
from test_batch_profile_isolation import pump_until


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.fixture
def page(tmp_path):
    app = create_application([])
    vault = repository(tmp_path)
    sources = []
    for name, value in (("compatible.csv", CODE), ("review.csv", MISSING), ("incompatible.csv", "SYNTHETIC")):
        source = tmp_path / name
        source.write_text(f"CPF,Note\n{value},SYNTHETIC\n", encoding="utf-8")
        sources.append(source)
    output = tmp_path / "output"
    output.mkdir()
    widget = BatchRestorationWidget(lambda: vault.as_read_only())
    widget.output_field.setText(str(output))
    widget.add_paths(sources)
    return app, widget, vault, sources, output


@pytest.mark.parametrize("row,expected", [
    (0, "File analyzed successfully."),
    (1, "Missing tokens require a policy review."),
    (2, "No valid tokens were found in the file."),
])
def test_real_analysis_result_explanations_follow_explicit_english(page, row, expected):
    app, widget, _, _, _ = page
    analyze(app, widget)
    widget.retranslate(Translator("en"))
    assert widget.file_table.item(row, 7).text() == expected
    assert widget.file_table.item(row, 7).toolTip() == expected


REASONS = (
    (Reason.ANALYZED, "Arquivo analisado com sucesso.", "File analyzed successfully."),
    (Reason.MISSING_TOKENS, "Há códigos ausentes que exigem revisão da política.", "Missing tokens require a policy review."),
    (Reason.NO_VALID_TOKENS, "Nenhum código válido foi encontrado no arquivo.", "No valid tokens were found in the file."),
    (Reason.OUTPUT_PUBLISHED, "Saída: {filename}", "Output: {filename}"),
    (Reason.ANALYSIS_CANCELLED, "Análise cancelada.", "Analysis cancelled."),
    (Reason.RESTORATION_CANCELLED, "Restauração cancelada.", "Restoration cancelled."),
    (Reason.CANCELLED_WITHOUT_PARTIAL, "Restauração cancelada sem publicar saída parcial.", "Restoration cancelled without publishing partial output."),
    (Reason.NO_COLUMNS_SELECTED, "Nenhuma coluna foi confirmada para restauração.", "No columns were confirmed for restoration."),
    (Reason.BATCH_INTERRUPTED, "Ignorado porque o lote foi interrompido.", "Skipped because the batch was interrupted."),
    (Reason.VAULT_ACCESS_FAILURE, "Falha estrutural ao acessar o cofre local.", "Structural failure accessing the local vault."),
    (Reason.MISSING_TOKEN_ABORT, "Arquivo interrompido porque há código ausente no cofre.", "File stopped because a token is missing from the vault."),
)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("reason,portuguese,english", REASONS)
def test_each_typed_category_renders_without_mutating_evidence(page, locale, reason, portuguese, english):
    _, widget, _, _, output = page
    item = widget.files[0]
    item.status = _RESULT_REASONS[reason][1]
    item.result_message = "Literal legacy service evidence & {filename}"
    item.result_explanation = Explanation(reason)
    name = "ação & {filename}_restaurado.csv"
    if reason is Reason.OUTPUT_PUBLISHED:
        item.output_path = output / name
    widget._refresh_file_table()
    before = asdict(item)
    widget.retranslate(Translator(locale))
    expected = (portuguese if locale == "pt-BR" else english).format(filename=name)
    assert widget.file_table.item(0, 7).text() == widget.file_table.item(0, 7).toolTip() == expected
    assert asdict(item) == before
    assert str(output) not in expected and CODE not in expected and MISSING not in expected


def test_contract_is_optional_immutable_and_catalog_parity_is_strict(page):
    _, widget, _, _, _ = page
    assert widget.files[0].result_explanation is None
    explanation = Explanation(Reason.ANALYZED, "Literal supplemental warning")
    with pytest.raises(FrozenInstanceError):
        explanation.detail = "changed"
    assert set(_RESULT_REASONS) == set(Reason)
    catalogs = load_catalogs()
    assert set(catalogs["en"]) == set(catalogs["pt-BR"])
    for identifier, _ in _RESULT_REASONS.values():
        assert identifier in catalogs["en"] and identifier in catalogs["pt-BR"]


@pytest.mark.parametrize("reason", [Reason.ANALYZED, Reason.MISSING_TOKENS, Reason.NO_VALID_TOKENS])
def test_a_known_portuguese_sentence_without_typed_metadata_is_still_literal(page, reason):
    _, widget, _, _, _ = page
    item = widget.files[0]
    identifier, item.status = _RESULT_REASONS[reason]
    item.result_message = Translator("pt-BR").text(identifier)
    item.result_explanation = None
    widget._refresh_file_table()
    widget.retranslate(Translator("en"))
    assert widget.file_table.item(0, 7).text() == item.result_message


@pytest.mark.parametrize("metadata", [Explanation("future_reason"), Explanation("analyzed"),
    Explanation([]), "unclassified metadata", Explanation(Reason.ANALYZED)])
def test_unknown_or_status_mismatched_metadata_falls_back_literally(page, metadata):
    _, widget, _, _, _ = page
    item = widget.files[0]
    item.status = Status.ERROR
    item.result_message = "Serviço: <literal> & {filename} — mensagem desconhecida"
    item.result_explanation = metadata
    for locale in ("en", "pt-BR"):
        widget.retranslate(Translator(locale))
        widget._refresh_file_table()
        assert widget.file_table.item(0, 7).text() == item.result_message


def test_real_header_warning_keeps_literal_supplement_and_legacy_message(page):
    app, widget, _, sources, _ = page
    sources[0].write_text(f",Note\n{CODE},SYNTHETIC\n", encoding="utf-8")
    analyze(app, widget)
    item = widget.files[0]
    assert item.status is Status.COMPATIBLE and item.result_explanation.reason is Reason.ANALYZED
    warning = item.result_explanation.detail
    assert "1 cabeçalho vazio foi substituído" in warning and "column_1" in warning
    raw = "Arquivo analisado com sucesso. " + warning
    assert item.result_message == raw
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert widget.file_table.item(0, 7).text() == Translator(locale).text("batch_restoration.result.analyzed") + " " + warning
        assert item.result_message == raw and item.headers[0] == "column_1"


def test_retranslate_preserves_real_analyzed_rows_checkboxes_focus_scroll_and_does_no_io(page, monkeypatch):
    app, widget, _, _, _ = page
    analyze(app, widget)
    seed = tuple(widget.files)
    widget.files.extend(deepcopy(seed[index % 3]) for index in range(57))
    widget._refresh_file_table()
    widget.resize(1100, 600)
    widget.show()
    app.processEvents()
    widget.file_table.selectRow(30)
    widget.column_table.cellWidget(0, 0).setChecked(True)
    widget.column_table.cellWidget(0, 0).setFocus()
    app.processEvents()
    widget.file_table.verticalScrollBar().setValue(20)
    assert widget.file_table.verticalScrollBar().value() > 0
    files, state = tuple(widget.files), [asdict(item) for item in widget.files]
    cells = tuple(widget.file_table.item(row, col) for row in range(60) for col in range(8))
    boxes = tuple(widget.column_table.cellWidget(row, 0) for row in range(widget.column_table.rowCount()))
    scroll = widget.file_table.verticalScrollBar().value(), widget.file_table.horizontalScrollBar().value()
    selected = [(index.row(), index.column()) for index in widget.file_table.selectedIndexes()]
    focus = app.focusWidget()
    refs = widget._analysis_worker, widget._processing_worker
    progress = tuple((bar.minimum(), bar.maximum(), bar.value())
                     for bar in (widget.current_progress, widget.overall_progress))
    spies = [QSignalSpy(widget.file_table.itemChanged), QSignalSpy(widget.file_table.itemSelectionChanged),
        QSignalSpy(widget.busy_changed), QSignalSpy(boxes[0].toggled)]
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation may not touch filesystem, re-analyze or execute a worker")
    with monkeypatch.context() as guard:
        for name in ("analyze_files", "restore_files", "_analyze_file", "_restore_file"):
            guard.setattr(widget._service, name, forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        for name in ("open", "stat", "iterdir", "unlink", "rglob"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            widget.retranslate(translator)
            app.processEvents()
            assert all(actual is original for actual, original in zip(widget.files, files, strict=True))
            assert [asdict(item) for item in widget.files] == state
            assert tuple(widget.file_table.item(row, col) for row in range(60) for col in range(8)) == cells
            assert tuple(widget.column_table.cellWidget(row, 0) for row in range(len(boxes))) == boxes
            assert [(index.row(), index.column()) for index in widget.file_table.selectedIndexes()] == selected
            assert (widget.file_table.verticalScrollBar().value(), widget.file_table.horizontalScrollBar().value()) == scroll
            assert app.focusWidget() is focus and refs == (widget._analysis_worker, widget._processing_worker)
            assert tuple((bar.minimum(), bar.maximum(), bar.value())
                         for bar in (widget.current_progress, widget.overall_progress)) == progress
    assert all(spy.count() == 0 for spy in spies)


@pytest.mark.parametrize("policy", list(BatchMissingCodePolicy))
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_real_bytes_decisions_and_reason_codes_match_between_locales(page, monkeypatch, policy, representation):
    app, original, vault, sources, root_output = page
    html = sources[0].with_name("safe.html")
    html.write_text(f'<p title="{CODE}">{CODE}</p>', encoding="utf-8")
    source_bytes = {path: path.read_bytes() for path in [*sources, html]}
    vault_before = vault.database_path.read_bytes()
    results, output_bytes = [], []
    for locale in ("pt-BR", "en"):
        output = root_output / locale
        output.mkdir()
        widget = BatchRestorationWidget(lambda: vault.as_read_only(), translator=Translator(locale))
        widget.output_field.setText(str(output))
        widget.add_paths([*sources, html])
        analyze(app, widget)
        assert [item.result_explanation.reason for item in widget.files] == [Reason.ANALYZED, Reason.MISSING_TOKENS, Reason.NO_VALID_TOKENS, Reason.ANALYZED]
        confirm_columns(widget)
        widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(policy.value))
        widget.representation_combo.setCurrentIndex(widget.representation_combo.findData(representation.value))
        monkeypatch.setattr(widget, "_confirm_restoration", lambda: QMessageBox.StandardButton.Yes)
        widget.start_restoration()
        finish(app, widget, "_processing_worker")
        results.append(tuple((item.status, item.result_explanation.reason, item.result_message) for item in widget.files))
        output_bytes.append({item.output_path.name: item.output_path.read_bytes() for item in widget.files if item.output_path})
        assert widget.files[0].result_explanation.reason is Reason.OUTPUT_PUBLISHED
        if policy is not BatchMissingCodePolicy.KEEP:
            assert widget.files[1].result_explanation.reason is Reason.MISSING_TOKEN_ABORT
        if policy is BatchMissingCodePolicy.ABORT_BATCH:
            assert widget.files[3].result_explanation.reason is Reason.BATCH_INTERRUPTED
        assert not list(output.glob("*.tmp"))
    assert output_bytes[0] == output_bytes[1] and results[0] == results[1]
    assert all(path.read_bytes() == data for path, data in source_bytes.items())
    assert vault.database_path.read_bytes() == vault_before


@pytest.mark.parametrize("during_file", [False, True])
def test_real_partial_cancel_keeps_published_output_and_localizes_cancel_and_skip(page, monkeypatch, during_file):
    app, widget, _, sources, output = page
    last = sources[0].with_name("last.csv")
    last.write_bytes(sources[0].read_bytes())
    widget.add_paths([last])
    analyze(app, widget)
    confirm_columns(widget)
    entered, release = Event(), Event()
    restore_files, restore_file = widget._service.restore_files, widget._service._restore_file
    if during_file:
        def paused(item, *args, **kwargs):
            if item is widget.files[1]:
                entered.set()
                assert release.wait(10)
            return restore_file(item, *args, **kwargs)
        monkeypatch.setattr(widget._service, "_restore_file", paused)
    else:
        def paused_batch(*args, file_callback, **kwargs):
            def changed(item):
                file_callback(item)
                if item is widget.files[0] and item.status is Status.COMPLETED:
                    entered.set()
                    assert release.wait(10)
            return restore_files(*args, file_callback=changed, **kwargs)
        monkeypatch.setattr(widget._service, "restore_files", paused_batch)
    monkeypatch.setattr(widget, "_confirm_restoration", lambda: QMessageBox.StandardButton.Yes)
    widget.start_restoration()
    worker = widget._processing_worker
    try:
        pump_until(app, entered.is_set)
        published = widget.files[0].output_path
        before = published.read_bytes()
        assert widget.open_output_button.isEnabled()
        widget.retranslate(Translator("en"))
        assert widget._processing_worker is worker and worker.isRunning()
        # Until a result replaces it, the existing analysis explanation remains
        # valid while the current file is processing, and must stay localized.
        assert widget.file_table.item(1, 7).text() == "Missing tokens require a policy review."
        widget.cancel()
        widget.retranslate(Translator("pt-BR"))
        assert worker._cancellation.is_set()
    finally:
        release.set()
    finish(app, widget, "_processing_worker")
    assert [item.status for item in widget.files] == [Status.COMPLETED, Status.CANCELLED, Status.INCOMPATIBLE, Status.SKIPPED]
    assert widget.files[1].result_explanation.reason is (Reason.CANCELLED_WITHOUT_PARTIAL if during_file else Reason.RESTORATION_CANCELLED)
    assert widget.files[3].result_explanation.reason is Reason.BATCH_INTERRUPTED
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert widget.file_table.item(0, 7).text() == Translator(locale).text("batch_restoration.result.output", filename=published.name)
        assert widget.file_table.item(1, 7).text() == Translator(locale).text(
            "batch_restoration.result.cancelled_without_partial" if during_file else "batch_restoration.result.restoration_cancelled")
        assert widget.file_table.item(3, 7).text() == Translator(locale).text("batch_restoration.result.batch_interrupted")
        assert published.read_bytes() == before and widget.open_output_button.isEnabled() and widget._output_directory == output
    assert widget.overall_progress.value() < widget.overall_progress.maximum()
    assert widget.current_progress.maximum() == 1 and widget.current_progress.value() == 0
    assert widget._displayed_summary.completed_files == 1 and widget._displayed_summary.cancelled
    assert not list(output.glob("*.tmp"))


@pytest.mark.parametrize("phase", ["analysis_before_file", "analysis_during_file", "analysis_structural", "processing_structural", "unclassified_error", "no_columns"])
def test_service_branches_assign_only_reliable_reasons_and_keep_error_fallback(page, monkeypatch, phase):
    app, widget, _, _, output = page
    if phase in ("analysis_before_file", "analysis_during_file", "analysis_structural"):
        if phase == "analysis_before_file":
            widget._service.analyze_files(widget.files, should_cancel=lambda: True)
        else:
            def fail(*args, **kwargs):
                if phase == "analysis_structural":
                    raise VaultError("PRIVATE_SYNTHETIC_DETAIL")
                raise RestorationCancelled("Literal cancellation")
            monkeypatch.setattr(widget._service, "_analyze_file", fail)
            widget.analyze_files()
            finish(app, widget, "_analysis_worker")
        assert widget.files[0].result_explanation.reason is (Reason.VAULT_ACCESS_FAILURE if phase == "analysis_structural" else Reason.ANALYSIS_CANCELLED)
    else:
        analyze(app, widget)
        confirm_columns(widget)
        if phase == "no_columns":
            for item in widget.files:
                for column in item.columns:
                    column.selected = False
            widget._service.restore_files(widget.files, output, BatchRestorationOptions())
            assert widget.files[0].result_explanation.reason is Reason.NO_COLUMNS_SELECTED
        else:
            def fail(*args, **kwargs):
                if phase == "processing_structural":
                    raise VaultError("PRIVATE_SYNTHETIC_DETAIL")
                raise RestorationError("Literal unclassified error & {count}")
            monkeypatch.setattr(widget._service, "_restore_file", fail)
            monkeypatch.setattr(widget, "_confirm_restoration", lambda: QMessageBox.StandardButton.Yes)
            widget.start_restoration()
            finish(app, widget, "_processing_worker")
            assert widget.files[0].result_explanation == (Explanation(Reason.VAULT_ACCESS_FAILURE) if phase == "processing_structural" else None)
    widget._refresh_file_table()
    widget.retranslate(Translator("en"))
    text = widget.file_table.item(0, 7).text()
    assert "PRIVATE_SYNTHETIC_DETAIL" not in text
    if phase == "unclassified_error":
        assert text == widget.files[0].result_message == "Literal unclassified error & {count}"


def test_invalidating_clears_explanation_without_deleting_published_file(page):
    _, widget, _, _, output = page
    item = widget.files[0]
    published = output / "synthetic_restaurado.csv"
    published.write_bytes(b"synthetic previous published output")
    item.status = Status.COMPLETED
    item.output_path = published
    item.result_message = "Saída: " + published.name
    item.result_explanation = Explanation(Reason.OUTPUT_PUBLISHED)
    invalidate_files(widget.files)
    assert all(item.result_explanation is None and item.status is Status.PENDING and item.result_message == "" for item in widget.files)
    assert published.read_bytes() == b"synthetic previous published output"
