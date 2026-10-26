"""Explicit masking presentation never changes configuration or generated data."""

import csv
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtWidgets import QFileDialog, QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.anonymization.models import AnonymizationResult
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NORMALIZATION_OPTIONS, NormalizationRule
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.transfer_package import read_package
from data_mask_studio.transfer_package.binding import verify_file_binding
from data_mask_studio.vault import VaultCipher, VaultRepository


PASSWORD = "synthetic package password"
HEADERS = ("CUSTOMER NAME", "DOCUMENT", "NOTES", "REMOVE")


class SyntheticKey:
    def get_key(self):
        return b"H" * 32


@pytest.fixture
def masking_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    pages = []

    def create(locale="pt-BR", *, load=True):
        directory = tmp_path / f"{locale}-{len(pages)}"
        directory.mkdir()
        repository = VaultRepository(directory / "vault.db", VaultCipher(b"V" * 32))
        translator = Translator(locale)
        widget = AnonymizationWidget(
            key_provider=SyntheticKey(), vault_repository_factory=lambda: repository,
            profile_service=ProfileService(ProfileRepository(directory / "profiles.json")),
            translator=translator,
        )
        pages.append(widget)
        source = directory / "synthetic.csv"
        source.write_text(
            "CUSTOMER NAME,DOCUMENT,NOTES,REMOVE\n"
            "  Alice   Example  ,111.222.333-44,  Sample   note  ,Discard\n"
            "Bob Example,555.666.777-88,Another note,Discard too\n",
            encoding="utf-8",
        )
        if load:
            widget.load_csv(str(source))
        return app, widget, source, repository, translator

    yield create
    for widget in pages:
        assert widget.stop_worker()


def configure(widget):
    actions = (ColumnAction.PRESERVE, ColumnAction.MASK, ColumnAction.PRESERVE, ColumnAction.EXCLUDE)
    rules = (NormalizationRule.PERSON_NAME, NormalizationRule.DIGITS_ONLY,
             NormalizationRule.COLLAPSE_WHITESPACE, NormalizationRule.EXACT)
    for row, (action, rule) in enumerate(zip(actions, rules, strict=True)):
        widget._action_fields[row].setCurrentIndex(widget._action_fields[row].findData(action.value))
        widget._normalization_fields[row].setCurrentIndex(widget._normalization_fields[row].findData(rule.value))
    widget._output_name_fields[0].setText("Display Name")
    widget._prefix_fields[1].setText("DOC")
    composite = CompositeColumnConfig(
        "CUSTOM RELATION", "PAIR",
        (CompositeSource(SourceColumnRef(HEADERS[0]), NormalizationRule.PERSON_NAME),
         CompositeSource(SourceColumnRef(HEADERS[1]), NormalizationRule.DIGITS_ONLY)),
        UUID("12345678-1234-5678-1234-567812345678"), ColumnAction.MASK,
    )
    widget.composite_section.set_configurations((composite,), notify=True)
    widget.validate_current_configuration()
    assert widget._configuration_validated and widget.generate_button.isEnabled()
    return widget._build_current_plan()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_idle_guidance_uses_explicit_translator_without_selecting_a_file(masking_pages, locale):
    _, widget, _, _, translator = masking_pages(locale, load=False)
    assert widget.path_field.placeholderText() == translator.text("masking.no_file")
    assert widget.status_label.text() == translator.text("masking.status.initial")
    assert widget.status_label.property("feedbackState") == "neutral"
    assert widget._inspection_result is None and widget.config_table.rowCount() == 0
    assert not widget.validate_button.isEnabled() and not widget.generate_button.isEnabled()
    assert not widget.transfer_controls.checkbox.isEnabled()
    assert widget.progress_bar.isHidden() and widget.open_folder_button.isHidden()
    assert widget._last_output_path is None


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_masking_main_labels_and_stable_column_choices(masking_pages, locale):
    _, widget, source, _, translator = masking_pages(locale)
    assert widget.select_button.text() == translator.text("masking.select_csv")
    assert widget.validate_button.text() == translator.text("masking.validate")
    assert widget.generate_button.text() == translator.text("masking.generate")
    expected_headers = {
        "pt-BR": ["Ação", "Cabeçalho", "Cabeçalho de saída", "Prefixo", "Normalização"],
        "en": ["Action", "Header", "Output header", "Prefix", "Normalization"],
    }
    assert [widget.config_table.horizontalHeaderItem(i).text() for i in range(5)] == expected_headers[locale]
    assert widget.file_name_label.text() == source.name and widget.path_field.text() == str(source)
    for row, header in enumerate(HEADERS):
        assert widget.config_table.item(row, 1).text() == header
        action = widget._action_fields[row]
        assert [action.itemData(index) for index in range(action.count())] == [
            ColumnAction.PRESERVE.value, ColumnAction.MASK.value, ColumnAction.EXCLUDE.value,
        ]
        assert [action.itemText(index) for index in range(action.count())] == [
            translator.text(f"masking.action.{value}") for value in ("preserve", "mask", "exclude")
        ]
        normalization = widget._normalization_fields[row]
        assert [normalization.itemData(index) for index in range(normalization.count())] == [
            rule.value for rule, _ in NORMALIZATION_OPTIONS
        ]
        assert [normalization.itemText(index) for index in range(normalization.count())] == [
            translator.text(f"masking.normalization.{rule.value}") for rule, _ in NORMALIZATION_OPTIONS
        ]


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_bulk_actions_and_validation_are_locale_independent(masking_pages, locale):
    _, widget, _, _, _ = masking_pages(locale)
    widget.select_all_button.click()
    assert all(column.action is ColumnAction.MASK for column in widget._column_configs)
    widget.validate_button.click()
    assert widget._configuration_validated and widget.generate_button.isEnabled()
    assert widget.status_label.property("feedbackState") == "success"
    widget.unselect_all_button.click()
    assert all(column.action is ColumnAction.PRESERVE for column in widget._column_configs)
    assert not widget._configuration_validated and not widget.generate_button.isEnabled()
    widget.validate_button.click()
    assert widget._configuration_validated and not widget._build_current_plan().requires_masking
    widget._output_name_fields[1].setText(HEADERS[0])
    widget.validate_button.click()
    assert not widget._configuration_validated and not widget.generate_button.isEnabled()
    assert widget.status_label.property("feedbackState") == "error"


def test_retranslate_preserves_editors_plan_package_inputs_and_validation(masking_pages):
    app, widget, _, _, _ = masking_pages()
    plan = configure(widget)
    controls = widget.transfer_controls
    controls.checkbox.setChecked(True)
    controls.destination.setText(str(Path(widget.path_field.text()).with_name("custom.dmspackage")))
    controls.password.setText(PASSWORD)
    controls.confirmation.setText(PASSWORD)
    controls.show_passwords.setChecked(True)
    widget.profile_toggle.setChecked(True)
    widget.show()
    app.processEvents()
    focused_editor = widget._prefix_fields[1]
    focused_editor.setFocus()
    app.processEvents()
    assert focused_editor.hasFocus()
    refs = (widget._inspection_result, widget.config_table, tuple(widget._action_fields),
            tuple(widget._prefix_fields), tuple(widget._output_name_fields), tuple(widget._normalization_fields),
            widget.composite_section.table.cellWidget(0, 0), widget.composite_section.table.cellWidget(0, 3))
    data = (controls.destination.text(), controls.password.text(), controls.confirmation.text(),
            controls.output_path, controls._manual_destination)
    configuration_changed, index_changes = [], []
    widget.composite_section.changed.connect(lambda: configuration_changed.append(True))
    for combo in (*widget._action_fields, *widget._normalization_fields):
        combo.currentIndexChanged.connect(index_changes.append)
    worker_refs = widget._worker, widget._detection_worker
    for locale in ("en", "pt-BR", "en"):
        translator = Translator(locale)
        widget.retranslate(translator)
        app.processEvents()
        assert widget._translator is translator
        assert widget._build_current_plan() == plan
        assert widget._configuration_validated and widget.generate_button.isEnabled()
        assert refs == (widget._inspection_result, widget.config_table, tuple(widget._action_fields),
                        tuple(widget._prefix_fields), tuple(widget._output_name_fields), tuple(widget._normalization_fields),
                        widget.composite_section.table.cellWidget(0, 0), widget.composite_section.table.cellWidget(0, 3))
        assert data == (controls.destination.text(), controls.password.text(), controls.confirmation.text(),
                        controls.output_path, controls._manual_destination)
        assert controls.checkbox.isChecked() and controls.show_passwords.isChecked()
        assert controls.password.echoMode() == controls.confirmation.echoMode() == QLineEdit.EchoMode.Normal
        assert widget.profile_toggle.isChecked()
        assert focused_editor.hasFocus()
        assert (widget._worker, widget._detection_worker) == worker_refs
    assert not configuration_changed and not index_changes


@pytest.mark.parametrize("package", [False, True])
def test_real_workers_produce_identical_csv_across_locales(masking_pages, monkeypatch, package):
    outputs, payloads = [], []
    original_start = AnonymizationWorker.start
    for locale in ("pt-BR", "en"):
        app, widget, source, repository, _ = masking_pages(locale)
        plan = configure(widget)
        output = source.with_name("synthetic_masked.csv")
        controls = widget.transfer_controls
        if package:
            controls.checkbox.setChecked(True)
            controls.password.setText(PASSWORD)
            controls.confirmation.setText(PASSWORD)
        completed, failures = [], []

        def observe_then_start(worker):
            # Connect before starting even a very small, quickly completed CSV.
            worker.completed.connect(completed.append)
            worker.failed.connect(failures.append)
            original_start(worker)

        monkeypatch.setattr(AnonymizationWorker, "start", observe_then_start)
        widget._start_processing(output, overwrite=False)
        worker = widget._worker
        assert worker is not None
        assert worker.wait(10000)
        app.processEvents()
        app.processEvents()
        assert not failures and len(completed) == 1 and widget._worker is None
        result = completed[0]
        assert result.records_processed == 2 and result.output_path == output
        assert widget.status_label.property("feedbackState") == "success"
        assert widget.progress_bar.minimum() != widget.progress_bar.maximum()
        assert widget.progress_bar.value() == widget.progress_bar.maximum()
        assert widget.open_folder_button.isEnabled() and str(output) in widget.output_path_label.text()
        assert not controls.password.text() and not controls.confirmation.text()
        assert worker._transfer_package_request is None
        outputs.append(output.read_bytes())
        with output.open(encoding="utf-8-sig", newline="") as stream:
            headers, first, second = list(csv.reader(stream))
        assert headers == list(plan.final_headers)
        assert headers == ["Display Name", "DOCUMENT", "NOTES", "CUSTOM RELATION"]
        assert first[0] == "alice example" and first[2] == "Sample note"
        assert first[1].startswith("DOC-") and first[3].startswith("PAIR-")
        assert second[1] != first[1]
        assert repository.count() == 2
        if package:
            assert result.transfer_package_path == output.with_suffix(".dmspackage")
            assert str(result.transfer_package_path) in widget.output_path_label.text()
            payload = read_package(result.transfer_package_path, PASSWORD)
            verify_file_binding(output, payload.masked_file)
            assert len(payload.scalar_mappings) == len(payload.composite_mappings) == 2
            payloads.append(payload)
        else:
            assert result.transfer_package_path is None and not output.with_suffix(".dmspackage").exists()
        widget.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
        assert widget._last_output_path == output and widget.open_folder_button.isEnabled()
        assert str(output) in widget.output_path_label.text()
    assert outputs[0] == outputs[1]
    if package:
        assert payloads[0] == payloads[1]


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_retranslation_during_worker_preserves_terminal_and_secret_lifecycle(
    masking_pages, tmp_path, monkeypatch, locale, outcome,
):
    app, widget, source, _, _ = masking_pages(locale)
    configure(widget)
    controls = widget.transfer_controls
    controls.checkbox.setChecked(True)
    controls.password.setText(PASSWORD)
    controls.confirmation.setText(PASSWORD)
    controls.show_passwords.setChecked(True)
    monkeypatch.setattr(AnonymizationWorker, "start", lambda self: None)
    output = source.with_name("result_masked.csv")
    widget._start_processing(output, overwrite=False)
    worker = widget._worker
    before = (widget.generate_button.isEnabled(), widget.select_button.isEnabled(),
              widget.config_table.isEnabled(), widget.cancel_button.isEnabled())
    widget.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert widget._worker is worker
    assert before == (widget.generate_button.isEnabled(), widget.select_button.isEnabled(),
                      widget.config_table.isEnabled(), widget.cancel_button.isEnabled())
    assert not controls.password.text() and not controls.confirmation.text()
    assert not controls.show_passwords.isChecked()
    worker.progress.emit(19)
    assert "19" in widget.processed_count_label.text()
    if outcome == "success":
        worker.completed.emit(AnonymizationResult(output, 19, 0.5, new_mappings=3, updated_mappings=2))
        assert widget.status_label.property("feedbackState") == "success"
        assert all(str(count) in widget.status_label.text() for count in (19, 3, 2))
        assert widget._last_output_path == output and widget.open_folder_button.isEnabled()
    elif outcome == "failure":
        message = "Synthetic safe service failure; unchanged public contract."
        worker.failed.emit(CSVAnonymizationError(message))
        assert widget.status_label.text() == message
        assert widget.status_label.property("feedbackState") == "error"
        assert widget._last_output_path is None and not widget.open_folder_button.isEnabled()
        widget.retranslate(Translator(locale))
        assert widget.status_label.text() == message
    else:
        worker.cancelled.emit()
        assert widget.status_label.property("feedbackState") == "warning"
        assert widget._last_output_path is None and not widget.open_folder_button.isEnabled()
    worker._transfer_package_request = None
    worker.finished.emit()
    app.processEvents()
    assert widget._worker is None
    widget.clear_selection()
    assert widget._last_output_path is None and not widget.output_path_label.text()
    assert not widget.progress_bar.isVisible() and not widget.open_folder_button.isEnabled()
    assert widget.status_label.property("feedbackState") == "neutral"


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_unknown_failures_use_safe_localized_message(masking_pages, locale):
    _, widget, _, _, _ = masking_pages(locale)
    secret = "synthetic-private-exception-detail"
    widget._processing_failed(RuntimeError(secret))
    assert secret not in widget.status_label.text()
    assert widget.status_label.text() and widget.status_label.property("feedbackState") == "error"
    before = widget.status_label.text()
    widget.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert widget.status_label.text() != before and secret not in widget.status_label.text()


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_retranslation_cannot_restore_previous_success_after_a_second_operation(
    masking_pages, monkeypatch, outcome,
):
    app, widget, source, _, _ = masking_pages()
    configure(widget)
    previous_output = source.with_name("previous_masked.csv")
    widget._processing_completed(AnonymizationResult(previous_output, 2, 0.1, new_mappings=2))
    assert widget.status_label.property("feedbackState") == "success"
    monkeypatch.setattr(AnonymizationWorker, "start", lambda self: None)
    widget._start_processing(source.with_name("next_masked.csv"), overwrite=False)
    worker = widget._worker
    assert worker is not None and widget._last_output_path is None
    assert not widget.open_folder_button.isEnabled() and not widget.output_path_label.text()
    if outcome == "failure":
        worker.failed.emit(CSVAnonymizationError("Synthetic second-operation failure."))
        expected_state = "error"
    else:
        worker.cancelled.emit()
        expected_state = "warning"
    worker.finished.emit()
    app.processEvents()
    assert widget._worker is None
    for locale in ("en", "pt-BR"):
        widget.retranslate(Translator(locale))
        assert widget.status_label.property("feedbackState") == expected_state
        assert str(previous_output) not in widget.output_path_label.text()
        assert widget._last_output_path is None and not widget.open_folder_button.isEnabled()
        assert widget.progress_bar.isHidden()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_localized_file_choosers_preserve_extension_and_cancellation_contracts(
    masking_pages, monkeypatch, locale,
):
    _, widget, source, _, _ = masking_pages(locale)
    plan = configure(widget)
    calls = []

    def cancel(*args, **kwargs):
        calls.append((args, kwargs))
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", cancel)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", cancel)
    widget._select_csv()
    widget._choose_output_file()
    controls = widget.transfer_controls
    controls.checkbox.setChecked(True)
    controls._browse()
    assert len(calls) == 3
    assert all(not kwargs for _, kwargs in calls)
    assert all("*.csv" in calls[index][0][3] for index in (0, 1))
    assert "*.dmspackage" in calls[2][0][3]
    assert Path(calls[1][0][2]).name == "synthetic_masked.csv"
    assert widget._inspection_result.path == source and widget._build_current_plan() == plan
    assert widget._configuration_validated and widget.generate_button.isEnabled()
    assert widget._worker is None
