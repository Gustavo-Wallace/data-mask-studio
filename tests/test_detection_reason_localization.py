"""Typed detection reasons are presentation only; legacy evidence stays intact."""

import csv
from dataclasses import asdict, FrozenInstanceError, replace
from pathlib import Path
import sqlite3
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.detection import (
    ColumnSuggestion, ConfidenceLevel as Confidence, DetectionExplanation as Explanation,
    DetectionReason as Reason, DetectionResult, SuggestedType as Kind, analyze_csv_columns,
)
import data_mask_studio.detection.analyzer as analyzer
from data_mask_studio.gui.detection_dialog import DetectionDialog, _REASON_IDS
import data_mask_studio.gui.detection_worker as worker_module
from data_mask_studio.i18n import Translator, load_catalogs
from data_mask_studio.normalization import NormalizationRule as Rule
from test_batch_profile_isolation import pump_until
from test_dialog_consistency import modal_reply
from test_masking_localization import masking_pages


DATE = "2026-11-03T10:20:30Z"
EMAIL = "synthetic@example.com"
TEXT = "Free synthetic text"
CASES = (
    (Reason.DATETIME_HEADER_NO_SAMPLE, "data", (), Kind.DATETIME, Confidence.LOW,
     "O cabeçalho indica data ou hora, mas não há amostra para confirmar.",
     "The header indicates a date or time, but there is no sample to confirm it."),
    (Reason.DATETIME_HEADER_STRONG, "data", (DATE,) * 4, Kind.DATETIME, Confidence.HIGH,
     "O cabeçalho e a maior parte da amostra indicam data ou hora.",
     "The header and most of the sample indicate a date or time."),
    (Reason.DATETIME_HEADER_PARTIAL, "data", (DATE, DATE, TEXT, TEXT), Kind.DATETIME, Confidence.MEDIUM,
     "O cabeçalho e parte relevante da amostra indicam data ou hora.",
     "The header and a substantial part of the sample indicate a date or time."),
    (Reason.DATETIME_HEADER_INCONSISTENT, "data", (TEXT,) * 4, Kind.DATETIME, Confidence.LOW,
     "O cabeçalho indica data ou hora, mas a amostra é inconsistente.",
     "The header indicates a date or time, but the sample is inconsistent."),
    (Reason.DATETIME_SAMPLE, "evento", (DATE,) * 4, Kind.DATETIME, Confidence.MEDIUM,
     "A maior parte da amostra possui formatos válidos de data ou hora.",
     "Most of the sample has valid date or time formats."),
    (Reason.COMMON_HEADER_CONFLICT, "observacao", (EMAIL,) * 4, Kind.EMAIL, Confidence.LOW,
     "O cabeçalho parece comum, mas a amostra possui outro padrão.",
     "The header appears to describe common text, but the sample has another pattern."),
    (Reason.COMMON_TEXT_HEADER, "observacao", (TEXT,) * 4, Kind.COMMON_TEXT, Confidence.HIGH,
     "O cabeçalho indica texto comum sem padrão sensível consistente.",
     "The header indicates common text without a consistent sensitive pattern."),
    (Reason.NAME_HEADER, "nome", (TEXT,) * 4, Kind.NAME, Confidence.MEDIUM,
     "O cabeçalho indica uma coluna de nome.", "The header indicates a name column."),
    (Reason.HEADER_NO_SAMPLE, "cpf", (), Kind.CPF, Confidence.LOW,
     "O cabeçalho sugere o tipo, mas não há valores para confirmar.",
     "The header suggests the type, but there are no values to confirm it."),
    (Reason.HEADER_CONFLICT, "telefone", (EMAIL,) * 4, Kind.PHONE, Confidence.LOW,
     "O cabeçalho e o padrão agregado da amostra são conflitantes.",
     "The header and the aggregate sample pattern conflict."),
    (Reason.HEADER_STRONG, "cpf", ("123.456.789-00",) * 4, Kind.CPF, Confidence.HIGH,
     "O cabeçalho e a maior parte da amostra indicam o mesmo tipo.",
     "The header and most of the sample indicate the same type."),
    (Reason.HEADER_PARTIAL, "email", (EMAIL, EMAIL, TEXT, TEXT), Kind.EMAIL, Confidence.MEDIUM,
     "O cabeçalho é compatível com parte relevante da amostra.",
     "The header matches a substantial part of the sample."),
    (Reason.HEADER_INCONSISTENT, "email", (EMAIL, TEXT, TEXT, TEXT), Kind.EMAIL, Confidence.LOW,
     "O cabeçalho sugere o tipo, mas a amostra é inconsistente.",
     "The header suggests the type, but the sample is inconsistent."),
    (Reason.SAMPLE_STRONG, "valor", (EMAIL,) * 4, Kind.EMAIL, Confidence.MEDIUM,
     "A maior parte da amostra possui um padrão estrutural consistente.",
     "Most of the sample has a consistent structural pattern."),
    (Reason.SAMPLE_PARTIAL, "valor", (EMAIL, EMAIL, EMAIL, TEXT, TEXT), Kind.EMAIL, Confidence.LOW,
     "Parte da amostra possui um possível padrão estrutural.",
     "Part of the sample has a possible structural pattern."),
    (Reason.UNRECOGNIZED_PATTERN, "x", (TEXT,) * 4, Kind.UNKNOWN, Confidence.UNDEFINED,
     "Não foi identificado um padrão confiável no cabeçalho ou na amostra.",
     "No reliable pattern was identified in the header or sample."),
)


def load_case(page, source, header, values):
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([header, "aux"])
        writer.writerows((value, TEXT) for value in values)
    page.load_csv(str(source))
    return analyze_csv_columns(page._inspection_result)


@pytest.mark.parametrize("header,values,expected", [
    ("cpf", ["123.456.789-00"], "The header and most of the sample indicate the same type."),
    ("data", [], "The header indicates a date or time, but there is no sample to confirm it."),
    ("telefone", ["synthetic@example.com"], "The header and the aggregate sample pattern conflict."),
    ("x", ["sem padrão"], "No reliable pattern was identified in the header or sample."),
])
def test_real_detection_explanations_follow_explicit_english(masking_pages, header, values, expected):
    _, page, source, *_ = masking_pages()
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([header, "aux"])
        writer.writerows((value, "Synthetic note") for value in values)
    page.load_csv(str(source))
    result = analyze_csv_columns(page._inspection_result)
    page._detection_completed(result)
    page.retranslate(Translator("en"))
    dialog = page._detection_dialog
    assert dialog.table.item(0, 7).text() == expected
    assert dialog.table.item(0, 7).toolTip() == expected


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("reason,header,values,kind,confidence,portuguese,english", CASES)
def test_each_reason_comes_from_real_detection_with_identical_legacy_evidence(
    masking_pages, locale, reason, header, values, kind, confidence, portuguese, english,
):
    _, page, source, *_ = masking_pages(locale)
    result = load_case(page, source, header, values)
    suggestion = result.suggestions[0]
    assert suggestion.explanation == Explanation(reason)
    assert suggestion.reason == portuguese and suggestion.suggested_type is kind and suggestion.confidence is confidence
    assert suggestion.sampled_values == len(values)
    page._detection_completed(result)
    before = asdict(result)
    expected = portuguese if locale == "pt-BR" else english
    assert page._detection_dialog.table.item(0, 7).text() == expected
    assert page._detection_dialog.table.item(0, 7).toolTip() == expected
    assert asdict(result) == before and page._suggestions is result.suggestions
    assert all(value not in expected for value in values)


def test_metadata_is_optional_immutable_and_does_not_change_legacy_constructor():
    suggestion = ColumnSuggestion("USER Ω", Kind.CPF, True, "CPF", Rule.CPF, Confidence.HIGH, "legacy", 4, 4)
    assert suggestion.explanation is None and suggestion.reason == "legacy"
    explanation = Explanation(Reason.HEADER_STRONG)
    with pytest.raises(FrozenInstanceError):
        explanation.reason = Reason.NAME_HEADER
    with pytest.raises(FrozenInstanceError):
        suggestion.explanation = explanation
    legacy = analyzer._build("USER Ω", Kind.CPF, Confidence.HIGH, [TEXT], 0.0, "custom literal")
    assert legacy.explanation is None and legacy.reason == "custom literal"
    assert set(_REASON_IDS) == {case[0] for case in CASES} == set(Reason)
    catalogs = load_catalogs()
    assert set(catalogs["en"]) == set(catalogs["pt-BR"])
    assert set(_REASON_IDS.values()) <= set(catalogs["en"])


@pytest.mark.parametrize("metadata", [None, Explanation("future"), Explanation("header_strong"), Explanation([]), "unclassified"])
def test_unknown_explanations_are_literal_even_if_the_words_match_a_known_reason(masking_pages, metadata):
    _, page, source, *_ = masking_pages()
    suggestion = load_case(page, source, "cpf", ("123.456.789-00",)).suggestions[0]
    literal = suggestion.reason
    suggestion = replace(suggestion, header="USER <b>Ω</b> & {header}", explanation=metadata)
    dialog = DetectionDialog((suggestion,), 1, page)
    for locale in ("en", "pt-BR", "en"):
        dialog.retranslate(Translator(locale))
        assert dialog.table.item(0, 7).text() == dialog.table.item(0, 7).toolTip() == literal
        assert dialog.table.item(0, 1).text() == suggestion.header
        assert suggestion.header in dialog._acceptance_fields[0].accessibleName()
        assert suggestion.header in dialog.table.cellWidget(0, 8).accessibleName()


def test_unclassified_freeform_reason_with_special_characters_is_not_rewritten(masking_pages):
    _, page, *_ = masking_pages()
    literal = "Unclassified <b>evidence</b> & {header}, Ω — literal diagnostics"
    suggestion = ColumnSuggestion("User {header}", Kind.EMAIL, True, "USER", Rule.EMAIL, Confidence.LOW, literal, 3, 1)
    dialog = DetectionDialog((suggestion,), 3, page)
    for locale in ("pt-BR", "en"):
        dialog.retranslate(Translator(locale))
        assert dialog.table.item(0, 7).text() == dialog.table.item(0, 7).toolTip() == literal


@pytest.mark.parametrize("header,matching,total,expected", [
    ("email", 4, 10, Reason.HEADER_INCONSISTENT),
    ("email", 5, 10, Reason.HEADER_PARTIAL),
    ("email", 7, 10, Reason.HEADER_PARTIAL),
    ("email", 8, 10, Reason.HEADER_STRONG),
    ("valor", 5, 10, Reason.UNRECOGNIZED_PATTERN),
    ("valor", 6, 10, Reason.SAMPLE_PARTIAL),
    ("valor", 7, 10, Reason.SAMPLE_PARTIAL),
    ("valor", 8, 10, Reason.SAMPLE_STRONG),
    ("telefone", 7, 10, Reason.HEADER_INCONSISTENT),
    ("telefone", 8, 10, Reason.HEADER_CONFLICT),
])
def test_existing_threshold_boundaries_are_unchanged(header, matching, total, expected):
    result = analyzer._suggest_column(header, [EMAIL] * matching + [TEXT] * (total - matching))
    assert result.explanation == Explanation(expected)
    assert result.sampled_values == total
    assert analyzer.STRONG_MATCH_RATIO == 0.8 and analyzer.PARTIAL_MATCH_RATIO == 0.6 and analyzer.HEADER_SUPPORT_RATIO == 0.5


@pytest.mark.parametrize("header,values,kind,confidence", [
    ("valor", ["12345678900"], Kind.CPF, Confidence.MEDIUM),
    ("telefone", ["12345678900"], Kind.PHONE, Confidence.HIGH),
    ("observacao", ["USR-101"], Kind.COMMON_TEXT, Confidence.HIGH),
    ("valor", ["USR-101"], Kind.GENERIC_ID, Confidence.LOW),
    ("telefone", [DATE], Kind.DATETIME, Confidence.MEDIUM),
])
def test_candidate_tie_breaking_and_datetime_precedence_are_preserved(header, values, kind, confidence):
    result = analyzer._suggest_column(header, values)
    assert result.suggested_type is kind and result.confidence is confidence


def test_retranslate_existing_rows_preserves_edits_checkboxes_focus_scroll_and_has_no_io(masking_pages, monkeypatch):
    app, page, source, *_ = masking_pages()
    headers = [f"email User Ω & {{header}} {index}" for index in range(60)]
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows([[EMAIL] * 60] * 3)
    page.load_csv(str(source))
    page._action_fields[3].setCurrentIndex(page._action_fields[3].findData("mask"))
    page._prefix_fields[3].setText("UNSAVED")
    page._output_name_fields[2].setText("User output Ω")
    result = analyze_csv_columns(page._inspection_result)
    page._detection_completed(result)
    dialog = page._detection_dialog
    dialog.resize(1300, 600)
    dialog.show()
    dialog.activateWindow()
    app.processEvents()
    dialog.table.setCurrentCell(30, 7)
    dialog._acceptance_fields[30].setChecked(True)
    dialog._acceptance_fields[30].setFocus()
    app.processEvents()
    dialog.table.verticalScrollBar().setValue(20)
    assert dialog.table.verticalScrollBar().value() > 0
    items = tuple(dialog.table.item(row, col) for row in range(60) for col in range(1, 8))
    boxes = tuple(dialog._acceptance_fields)
    buttons = tuple(dialog.table.cellWidget(row, 8) for row in range(60))
    state, configuration = asdict(result), [asdict(column) for column in page._column_configs]
    selection = [(index.row(), index.column()) for index in dialog.table.selectedIndexes()]
    scroll = dialog.table.horizontalScrollBar().value(), dialog.table.verticalScrollBar().value()
    current = dialog.table.currentRow(), dialog.table.currentColumn()
    focus = app.focusWidget()
    assert focus is boxes[30]
    workers = page._worker, page._detection_worker
    dirty = page._configuration_dirty, set(page._manually_changed_rows), page._configuration_validated
    signals = [QSignalSpy(dialog.individual_requested), QSignalSpy(dialog.bulk_requested),
               QSignalSpy(dialog.suggestions_cleared), QSignalSpy(boxes[30].toggled)]
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation may not read files, rerun detection, plan or apply suggestions")
    with monkeypatch.context() as guard:
        guard.setattr(analyzer, "analyze_csv_columns", forbidden)
        guard.setattr(worker_module, "analyze_csv_columns", forbidden)
        guard.setattr(worker_module.DetectionWorker, "start", forbidden)
        for name in ("analyze_columns", "_build_current_plan", "validate_current_configuration", "apply_detection_suggestion", "apply_detection_suggestions"):
            guard.setattr(page, name, forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        for name in ("open", "stat", "iterdir", "unlink"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            page.retranslate(translator)
            app.processEvents()
            assert dialog is page._detection_dialog and dialog._suggestions is result.suggestions
            assert asdict(result) == state and [asdict(column) for column in page._column_configs] == configuration
            assert tuple(dialog.table.item(row, col) for row in range(60) for col in range(1, 8)) == items
            assert tuple(dialog._acceptance_fields) == boxes and tuple(dialog.table.cellWidget(row, 8) for row in range(60)) == buttons
            assert boxes[30].isChecked() and app.focusWidget() is focus
            assert (dialog.table.currentRow(), dialog.table.currentColumn()) == current
            assert [(index.row(), index.column()) for index in dialog.table.selectedIndexes()] == selection
            assert (dialog.table.horizontalScrollBar().value(), dialog.table.verticalScrollBar().value()) == scroll
            assert (page._worker, page._detection_worker) == workers
            assert (page._configuration_dirty, set(page._manually_changed_rows), page._configuration_validated) == dirty
            assert dialog.table.item(30, 7).text() == translator.text("detection.reason.header_strong")
    assert all(signal.count() == 0 for signal in signals)


@pytest.mark.parametrize("cancel", [False, True])
def test_language_change_during_real_worker_does_not_restart_or_change_terminal_state(masking_pages, monkeypatch, cancel):
    app, page, _, *_ = masking_pages()
    entered, release = Event(), Event()
    calls = []
    analyze = worker_module.analyze_csv_columns
    def paused(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        return analyze(*args, **kwargs)
    monkeypatch.setattr(worker_module, "analyze_csv_columns", paused)
    page.analyze_columns()
    worker = page._detection_worker
    try:
        pump_until(app, entered.is_set)
        before = [asdict(column) for column in page._column_configs]
        for locale in ("en", "pt-BR", "en"):
            page.retranslate(Translator(locale))
            assert page._detection_worker is worker and worker.isRunning() and calls == [1]
            assert [asdict(column) for column in page._column_configs] == before
        if cancel:
            page.analyze_columns()  # Existing UI action requests cooperative cancellation.
            assert worker._cancel_requested.is_set()
    finally:
        release.set()
    assert worker.wait(10000)
    app.processEvents()
    assert page._detection_worker is None and calls == [1]
    if cancel:
        assert page._detection_dialog is None and not page._suggestions
        assert page.status_label.property("feedbackState") == "warning"
    else:
        assert page._suggestions and page._detection_dialog is not None
        assert all(item.explanation is not None for item in page._suggestions)
        for row, suggestion in enumerate(page._suggestions):
            assert page._detection_dialog.table.item(row, 7).text() == Translator("en").text(_REASON_IDS[suggestion.explanation.reason])


def test_real_suggestions_and_explicit_application_are_equivalent_in_both_locales(masking_pages):
    outcomes = []
    for locale in ("pt-BR", "en"):
        app, page, source, *_ = masking_pages(locale)
        with source.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["cpf", "email", "nome", "observacao", "data", "unknown Ω"])
            writer.writerow(["123.456.789-00", EMAIL, "Synthetic Person", TEXT, DATE, TEXT])
        page.load_csv(str(source))
        before = source.read_bytes()
        page.analyze_columns()
        worker = page._detection_worker
        assert worker is not None and worker.wait(10000)
        app.processEvents()
        suggestions = page._suggestions
        dialog = page._detection_dialog
        assert all(not column.anonymize for column in page._column_configs)
        for index in (0, 1, 2):
            dialog._acceptance_fields[index].setChecked(True)
        def yes():
            box = app.activeModalWidget()
            box.button(box.StandardButton.Yes).click()
        with modal_reply(page, yes):
            dialog.apply_accepted_button.click()
        assert [column.anonymize for column in page._column_configs] == [True, True, True, False, False, False]
        assert [column.normalization_rule for column in page._column_configs[:3]] == [Rule.CPF, Rule.EMAIL, Rule.PERSON_NAME]
        assert page._suggestions is suggestions and source.read_bytes() == before
        outcomes.append((asdict(DetectionResult(suggestions, 1, 100)), [asdict(column) for column in page._column_configs]))
    assert outcomes[0] == outcomes[1]
