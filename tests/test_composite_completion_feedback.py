"""Completion feedback reflects persisted occurrences, not emitted token metadata."""

import csv
from contextlib import closing
from dataclasses import replace
import sqlite3

import pytest

from qt_lifecycle import qt_widget_lifecycle
from localization_fixtures import presentation_translators
from data_mask_studio.anonymization import AnonymizationResult, ColumnAction
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv, CSVAnonymizationError
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from test_masking_localization import SyntheticKey, configure, masking_pages


def mask_current_configuration(widget, source, repository, *, name="result_masked.csv"):
    inspection = widget._inspection_result
    result = anonymize_csv(
        source, source.with_name(name), encoding=inspection.encoding, delimiter=inspection.delimiter,
        secret_key=SyntheticKey().get_key(), processing_plan=widget._build_current_plan(),
        vault_repository=repository, track_emitted_codes=False,
    )
    widget._processing_completed(result)
    return result


def output_composite_mappings(result, repository):
    with result.output_path.open(encoding="utf-8-sig", newline="") as stream:
        _, *rows = csv.reader(stream)
    return tuple(repository.composite_repository().get_composite_mapping(row[-1]) for row in rows)


def composite_identity_count(repository):
    with closing(sqlite3.connect(repository.database_path)) as connection:
        return connection.execute("SELECT count(*) FROM composite_mappings").fetchone()[0]


def assert_completion(widget, result, translator, *, changed, fallback_notice="", state="success"):
    parameters = dict(count=result.records_processed, new=result.new_mappings,
                      updated=result.updated_mappings, fallback_notice=fallback_notice,
                      vault_notice=translator.text("masking.status.vault_updated" if changed
                                                   else "masking.status.vault_unchanged"))
    message_id = "masking.status.completed"
    if result.composite_mapping_occurrences:
        message_id = "masking.status.completed_composites"
        parameters["composite"] = result.composite_mapping_occurrences
    assert widget.status_label.text() == translator.text(message_id, **parameters)
    assert widget.status_label.property("feedbackState") == state
    assert widget._completed_result is result and widget._status_result is result
    assert widget._last_output_path == result.output_path and widget.open_folder_button.isEnabled()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_composite_only_persistence_does_not_claim_vault_unchanged(
    masking_pages, locale, presentation_translators,
):
    _, widget, source, repository, translator = masking_pages(
        locale, translator=presentation_translators[locale])
    configure(widget)
    action = widget._action_fields[1]
    action.setCurrentIndex(action.findData(ColumnAction.PRESERVE.value))
    result = mask_current_configuration(widget, source, repository)
    assert result.new_mappings == result.updated_mappings == repository.count() == 0
    assert result.composite_mapping_occurrences == 2
    assert result.emitted_scalar_codes == result.emitted_composite_codes == ()
    for mapping in output_composite_mappings(result, repository):
        assert mapping is not None and mapping.occurrence_count == 1
    assert translator.text("masking.status.vault_updated") in widget.status_label.text()
    assert translator.text("masking.status.vault_unchanged") not in widget.status_label.text()
    assert_completion(widget, result, translator, changed=True)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_repeated_composites_update_counters_without_claiming_new_composite_identities(
    masking_pages, locale, presentation_translators,
):
    _, widget, source, repository, translator = masking_pages(
        locale, translator=presentation_translators[locale])
    configure(widget)
    action = widget._action_fields[1]
    action.setCurrentIndex(action.findData(ColumnAction.PRESERVE.value))
    first = mask_current_configuration(widget, source, repository)
    before = output_composite_mappings(first, repository)
    second = mask_current_configuration(widget, source, repository, name="repeated_masked.csv")
    after = output_composite_mappings(second, repository)
    assert first.output_path.read_bytes() == second.output_path.read_bytes()
    assert second.new_mappings == second.updated_mappings == repository.count() == 0
    assert first.composite_mapping_occurrences == second.composite_mapping_occurrences == 2
    assert [mapping.code for mapping in before] == [mapping.code for mapping in after]
    assert composite_identity_count(repository) == 2
    for original, repeated in zip(before, after, strict=True):
        assert original.occurrence_count == 1 and repeated.occurrence_count == 2
        assert len(original.variations) == len(repeated.variations) == 1
        assert original.variations[0].identifier == repeated.variations[0].identifier
        assert repeated.variations[0].occurrence_count == 2
    assert_completion(widget, second, translator, changed=True)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("kind", ["scalar", "mixed", "preserve", "empty"])
def test_real_scalar_mixed_preserved_and_empty_results_keep_distinct_persistence_feedback(
    masking_pages, locale, kind, presentation_translators,
):
    _, widget, source, repository, translator = masking_pages(
        locale, translator=presentation_translators[locale])
    if kind == "empty":
        source.write_text("CUSTOMER NAME,DOCUMENT,NOTES,REMOVE\n ,\t,Synthetic note,Discard\n", encoding="utf-8")
        widget.load_csv(str(source))
    configure(widget)
    if kind == "scalar":
        widget.composite_section.set_configurations((), notify=True)
    elif kind == "preserve":
        action = widget._action_fields[1]
        action.setCurrentIndex(action.findData(ColumnAction.PRESERVE.value))
        definition, = widget.composite_section.configurations
        widget.composite_section.set_configurations((replace(
            definition, action=ColumnAction.PRESERVE, prefix=""),), notify=True)
    result = mask_current_configuration(widget, source, repository)
    scalar = 2 if kind in ("scalar", "mixed") else 0
    composite = 2 if kind == "mixed" else 0
    assert result.new_mappings == repository.count() == scalar and result.updated_mappings == 0
    assert result.scalar_mapping_occurrences == scalar
    assert result.composite_mapping_occurrences == result.composite_tokens_generated == composite
    assert composite_identity_count(repository) == composite
    assert_completion(widget, result, translator, changed=bool(scalar or composite))
    if composite:
        assert all(mapping.occurrence_count == 1 for mapping in output_composite_mappings(result, repository))
    if kind == "scalar":
        repeated = mask_current_configuration(widget, source, repository, name="repeated_masked.csv")
        assert repeated.new_mappings == 0 and repeated.updated_mappings == 2
        assert repeated.composite_mapping_occurrences == 0
        assert_completion(widget, repeated, translator, changed=True)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_generated_or_emitted_codes_without_persistence_do_not_claim_vault_update(
    masking_pages, locale, presentation_translators,
):
    _, widget, source, _, translator = masking_pages(locale, translator=presentation_translators[locale])
    result = AnonymizationResult(source.with_name("synthetic_result.csv"), 1, 0.1,
        scalar_mapping_occurrences=1, composite_tokens_generated=1,
        emitted_scalar_codes=("DOC-ABCDEFGHIJKL",), emitted_composite_codes=("PAIR-ABCDEFGHIJKL",))
    widget._processing_completed(result)
    assert_completion(widget, result, translator, changed=False)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_completed_composite_feedback_retranslates_without_reprocessing_or_losing_counts(
    masking_pages, monkeypatch, locale, presentation_translators,
):
    _, widget, source, repository, _ = masking_pages(locale, translator=presentation_translators[locale])
    configure(widget)
    action = widget._action_fields[1]
    action.setCurrentIndex(action.findData(ColumnAction.PRESERVE.value))
    result = mask_current_configuration(widget, source, repository)
    output = result.output_path.read_bytes()
    mappings = output_composite_mappings(result, repository)
    plan = widget._build_current_plan()

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must only render the existing result")

    monkeypatch.setattr(repository, "transaction", forbidden)
    monkeypatch.setattr(widget, "_start_processing", forbidden)
    monkeypatch.setattr(widget, "validate_current_configuration", forbidden)
    for language in ("en", "pt-BR", "en"):
        translator = presentation_translators[language]
        widget.retranslate(translator)
        assert_completion(widget, result, translator, changed=True)
        assert widget._build_current_plan() == plan and widget._worker is None
    assert result.output_path.read_bytes() == output
    assert output_composite_mappings(result, repository) == mappings


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_composite_completion_keeps_existing_fallback_notice_and_warning_severity(
    masking_pages, locale, presentation_translators,
):
    _, widget, source, repository, _ = masking_pages(locale, translator=presentation_translators[locale])
    source.write_text("CUSTOMER NAME,DOCUMENT,NOTES,REMOVE\n\u0301,11122233344,Synthetic note,Discard\n",
                      encoding="utf-8")
    widget.load_csv(str(source))
    configure(widget)
    action = widget._action_fields[1]
    action.setCurrentIndex(action.findData(ColumnAction.PRESERVE.value))
    result = mask_current_configuration(widget, source, repository)
    assert result.new_mappings == result.updated_mappings == 0
    assert result.composite_mapping_occurrences == composite_identity_count(repository) == 1
    fallback, = result.normalization_fallbacks
    assert fallback.header == "CUSTOMER NAME" and fallback.count == 1
    for language in (locale, "en", "pt-BR"):
        translator = presentation_translators[language]
        widget.retranslate(translator)
        details = translator.text("masking.status.fallback_detail", header=fallback.header, count=1)
        notice = translator.text("masking.status.fallbacks", details=details)
        assert_completion(widget, result, translator, changed=True, fallback_notice=notice, state="warning")


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_new_operation_failure_or_cancel_cannot_restore_previous_composite_success(
    masking_pages, monkeypatch, outcome, presentation_translators,
):
    app, widget, source, repository, _ = masking_pages(translator=presentation_translators["pt-BR"])
    configure(widget)
    action = widget._action_fields[1]
    action.setCurrentIndex(action.findData(ColumnAction.PRESERVE.value))
    previous = mask_current_configuration(widget, source, repository)
    assert previous.composite_mapping_occurrences == 2
    monkeypatch.setattr(AnonymizationWorker, "start", lambda self: None)
    widget._start_processing(source.with_name("next_masked.csv"), overwrite=False)
    worker = widget._worker
    assert worker is not None
    if outcome == "failure":
        worker.failed.emit(CSVAnonymizationError("Synthetic safe failure."))
    else:
        worker.cancelled.emit()
    worker.finished.emit()
    app.processEvents()
    assert widget._worker is None
    for language in ("en", "pt-BR"):
        translator = presentation_translators[language]
        widget.retranslate(translator)
        assert widget._completed_result is None and widget._status_result is None
        assert widget.status_label.property("feedbackState") == ("error" if outcome == "failure" else "warning")
        assert widget.status_label.text() == ("Synthetic safe failure." if outcome == "failure"
                                              else translator.text("masking.status.cancelled"))
        assert widget._last_output_path is None and widget.progress_bar.isHidden()
        assert not widget.open_folder_button.isEnabled()
    assert previous.output_path.is_file() and composite_identity_count(repository) == 2
