"""GUI-owned validation retains presentation identity, never validation authority."""

from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
import pickle
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnConfig, AnonymizationResult
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.i18n import Translator
from data_mask_studio.gui.masking_validation_feedback import MaskingValidationFeedback, validation_feedback
from data_mask_studio.gui import anonymization_widget as mask_module, transfer_package_controls as transfer_module
from data_mask_studio.processing import PlanningError
from data_mask_studio.transfer_package.models import PackageError
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.anonymization import validate_configuration
from test_masking_localization import masking_pages, HEADERS
from test_localization_integration import main


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def configure_scalar(page):
    for index, action in enumerate(page._action_fields):
        action.setCurrentIndex(action.findData("mask" if index == 0 else "preserve"))
    page._prefix_fields[0].setText("NAME")
    page.validate_button.click()
    assert page._configuration_validated


def confirmation_failure(page, source):
    configure_scalar(page)
    controls = page.transfer_controls
    controls.checkbox.setChecked(True)
    controls.password.setText("synthetic-password")
    controls.confirmation.setText("different-synthetic-password")
    destination = source.with_name("synthetic-output.csv")
    page._start_processing(destination, overwrite=False)
    assert page._worker is None and not destination.exists()
    return destination


def partial_profile(page, source, *, missing=False, collision=False):
    template = source.with_name("template.csv")
    template.write_text("CUSTOMER NAME,ABSENT & <literal>\nAlice,Example\n" if missing else "CUSTOMER NAME\nAlice\n", encoding="utf-8")
    columns = [ColumnConfig("CUSTOMER NAME", True, "NAME", output_name="DOCUMENT" if collision else "")]
    if missing:
        columns.append(ColumnConfig("ABSENT & <literal>", True, "ABSENT"))
    profile = page._profile_service.create("Synthetic known profile", columns, inspection=inspect_csv(template))
    page.refresh_profiles(profile.identifier)
    page.profile_toggle.setChecked(True)
    page.apply_profile_button.click()
    assert page._unreviewed_sources == {1, 2, 3}
    assert not page._configuration_validated and not page.generate_button.isEnabled()


def test_package_confirmation_feedback_retranslates_without_repeating_validation(masking_pages):
    _, page, source, _, _ = masking_pages()
    confirmation_failure(page, source)
    for locale in ("pt-BR", "en", "pt-BR"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == Translator(locale).text("masking.package.error.confirmation")
        assert page.status_label.property("feedbackState") == "error"


@pytest.mark.parametrize("wrapper", [False, True])
def test_real_profile_unknown_columns_retranslate_local_references(masking_pages, wrapper):
    _, page, source, _, _ = masking_pages()
    partial_profile(page, source)
    if not wrapper:
        page.validate_button.click()
    for locale in ("pt-BR", "en", "pt-BR"):
        translator = Translator(locale)
        page.retranslate(translator)
        details = translator.text("masking.error.unknown_columns", columns=", ".join(
            translator.text("masking.column.reference", header=page._inspection_result.headers[index], number=index + 1)
            for index in (1, 2, 3)))
        assert page.status_label.text() == (translator.text("profile.partial", details=details) if wrapper else details)
        assert not page._configuration_validated and not page.generate_button.isEnabled()


@pytest.mark.parametrize("wrapper", [False, True])
def test_missing_profile_headers_remain_literal_data_in_translated_feedback(masking_pages, wrapper):
    _, page, source, _, _ = masking_pages()
    partial_profile(page, source, missing=True)
    if not wrapper:
        page.validate_button.click()
    for locale in ("en", "pt-BR", "en"):
        translator = Translator(locale)
        page.retranslate(translator)
        detail = translator.text("masking.error.missing_headers", headers="ABSENT & <literal>")
        assert page.status_label.text() == (translator.text("profile.partial", details=detail) if wrapper else detail)
        assert page.status_label.textFormat() is Qt.TextFormat.PlainText
        assert not page.generate_button.isEnabled()


def test_partial_profile_preserves_unclassified_service_suffix_exactly(masking_pages):
    _, page, source, _, _ = masking_pages()
    partial_profile(page, source, collision=True)
    raw = validate_configuration(page._column_configs).error_message
    assert raw and "DOCUMENT" in raw
    feedback = page._validation_feedback
    assert feedback.literal_suffix == " " + raw
    for locale in ("en", "pt-BR", "en"):
        translator = Translator(locale)
        page.retranslate(translator)
        inner = feedback.details.render(translator)
        assert page.status_label.text() == translator.text("profile.partial", details=inner + " " + raw)
        assert page._validation_feedback is feedback and feedback.literal_suffix == " " + raw


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("case", ["no_csv", "missing_headers", "unknown_columns", "confirmation", "requires_masking"])
def test_metadata_producers_preserve_exact_exception_classes_and_legacy_args(masking_pages, locale, case):
    _, page, source, _, translator = masking_pages(locale, load=case != "no_csv")
    output = source.with_name("destination.csv")
    if case in ("confirmation", "requires_masking"):
        configure_scalar(page)
        controls = page.transfer_controls
        controls.checkbox.setChecked(True)
        controls.password.setText("synthetic-password")
        controls.confirmation.setText("different-synthetic-password")
        action = lambda: controls.request(source, output, case == "confirmation")
        error_type = PackageError if case == "confirmation" else CSVAnonymizationError
        identifier = "masking.package.error." + case
        expected = translator.text(identifier)
    else:
        action = page._build_current_plan
        error_type = PlanningError
        identifier = "masking.error." + case
        if case == "missing_headers":
            page._profile_missing_headers = ("Absent & <literal>",)
            expected = translator.text(identifier, headers="Absent & <literal>")
        elif case == "unknown_columns":
            page._unreviewed_sources = {3, 1}
            expected = translator.text(identifier, columns=", ".join(translator.text(
                "masking.column.reference", header=HEADERS[index], number=index + 1) for index in (1, 3)))
        else:
            expected = translator.text(identifier)
    with pytest.raises(error_type) as caught:
        action()
    error = caught.value
    assert type(error) is error_type and error.args == (expected,) and str(error) == expected
    feedback = validation_feedback(error)
    assert feedback.message_id == identifier
    assert "synthetic-password" not in repr(feedback)
    assert "different-synthetic-password" not in repr(feedback)
    if error_type is PlanningError:
        assert error.missing_headers == error.extra_headers == ()
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is error_type and restored.args == error.args
    assert validation_feedback(restored) == feedback
    for target in ("en", "pt-BR"):
        page._show_validation_error(error)
        page.retranslate(Translator(target))
        assert page.status_label.text() == feedback.render(Translator(target))
        assert str(error) == expected


@pytest.mark.parametrize("case", ["confirmation", "unknown_columns", "partial", "missing_headers"])
def test_main_window_feedback_retranslation_is_pure_and_preserves_live_state(main, tmp_path, monkeypatch, case):
    app, window = main
    page = window.anonymization_widget
    source = tmp_path / "synthetic.csv"
    source.write_text("CUSTOMER NAME,DOCUMENT,NOTES,REMOVE\nAlice,Example,Note,Discard\n", encoding="utf-8")
    page.load_csv(str(source))
    published = None
    if case == "confirmation":
        configure_scalar(page)
        previous = tmp_path / "published.csv"
        page._start_processing(previous, overwrite=False)
        worker = page._worker
        assert worker is not None and worker.wait(10000)
        app.processEvents()
        assert page._worker is None and page._last_output_path == previous
        published = previous.read_bytes()
        confirmation_failure(page, source)
        focus = page.transfer_controls.password
        page.transfer_controls.password.setSelection(2, 5)
        page.transfer_controls.confirmation.setSelection(1, 4)
    else:
        partial_profile(page, source, missing=case == "missing_headers")
        if case != "partial":
            page.validate_button.click()
        focus = page._prefix_fields[0]
        focus.setSelection(1, 2)
    window.resize(1000, 700)
    page.config_table.selectRow(0)
    focus.setFocus()
    app.processEvents()
    assert app.focusWidget() is focus
    shell_scroll = window.page_shells[0].scroll_area.verticalScrollBar()
    shell_scroll.setValue(shell_scroll.maximum())
    app.processEvents()
    scroll = shell_scroll.value()
    fields = page.findChildren(QLineEdit)
    field_state = [(field.text(), field.echoMode(), field.cursorPosition(), field.selectedText()) for field in fields]
    data = [asdict(item) for item in page._column_configs]
    selected = [(index.row(), index.column()) for index in page.config_table.selectedIndexes()]
    controls = page.transfer_controls
    checkboxes = controls.checkbox.isChecked(), controls.show_passwords.isChecked()
    gates = (page._configuration_validated, page._configuration_dirty, page.generate_button.isEnabled(),
             page.validate_button.isEnabled(), page.config_table.isEnabled(), controls.isEnabled(),
             page.open_folder_button.isEnabled(), page.cancel_button.isEnabled())
    state = (page._worker, page._detection_worker, page._completed_result, page._last_output_path,
             page.progress_bar.minimum(), page.progress_bar.maximum(), page.progress_bar.value())
    feedback = page._validation_feedback
    assert feedback is not None
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    spies = [QSignalSpy(field.textChanged) for field in fields]
    spies.extend((QSignalSpy(page.config_table.itemSelectionChanged), QSignalSpy(page.profiles_changed),
                  QSignalSpy(controls.checkbox.toggled), QSignalSpy(controls.show_passwords.toggled)))

    def forbidden(*args, **kwargs):
        pytest.fail("Feedback retranslation must not validate, perform IO or run services/workers")

    with monkeypatch.context() as guard:
        for name in ("validate_current_configuration", "_build_current_plan", "_start_processing"):
            guard.setattr(page, name, forbidden)
        for name in ("validate_configuration", "inspect_csv", "output_header_errors", "AnonymizationWorker"):
            guard.setattr(mask_module, name, forbidden)
        for name in ("validate_password", "validate_transfer_package_destination"):
            guard.setattr(transfer_module, name, forbidden)
        guard.setattr(controls, "request", forbidden)
        guard.setattr(page._profile_service, "build_configuration_plan", forbidden)
        guard.setattr(page._profile_service, "list_profiles", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        for name in ("stat", "exists", "open", "iterdir", "unlink", "resolve"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            window.retranslate(translator)
            app.processEvents()
            assert page._validation_feedback is feedback
            assert page.status_label.text() == feedback.render(translator)
            assert page.status_label.property("feedbackState") == "error"
            assert app.focusWidget() is focus
            assert [(field.text(), field.echoMode(), field.cursorPosition(), field.selectedText()) for field in fields] == field_state
            assert [(index.row(), index.column()) for index in page.config_table.selectedIndexes()] == selected
            assert (controls.checkbox.isChecked(), controls.show_passwords.isChecked()) == checkboxes
            assert (page._configuration_validated, page._configuration_dirty, page.generate_button.isEnabled(),
                    page.validate_button.isEnabled(), page.config_table.isEnabled(), controls.isEnabled(),
                    page.open_folder_button.isEnabled(), page.cancel_button.isEnabled()) == gates
            assert (page._worker, page._detection_worker, page._completed_result, page._last_output_path,
                    page.progress_bar.minimum(), page.progress_bar.maximum(), page.progress_bar.value()) == state
            assert shell_scroll.value() == min(scroll, shell_scroll.maximum())
    assert [asdict(item) for item in page._column_configs] == data
    assert all(spy.count() == 0 for spy in spies)
    if published is not None:
        assert page._last_output_path.read_bytes() == published


@pytest.mark.parametrize("replacement", ["clear", "new_csv", "edit", "no_mask", "success", "literal", "cancel"])
def test_obsolete_feedback_is_not_resurrected(masking_pages, replacement):
    _, page, source, _, _ = masking_pages()
    confirmation_failure(page, source)
    assert page._validation_feedback is not None
    if replacement == "clear":
        page.clear_selection()
    elif replacement == "new_csv":
        another = source.with_name("another.csv")
        another.write_text("Other\nExample\n", encoding="utf-8")
        page.load_csv(str(another))
    elif replacement == "edit":
        page._prefix_fields[0].setText("CHANGED")
    elif replacement == "no_mask":
        page.unselect_all_columns()
        assert not page.transfer_controls.checkbox.isChecked() and not page.transfer_controls.password.text()
    elif replacement == "success":
        page.validate_button.click()
    elif replacement == "literal":
        page._set_status("Unclassified safe error", is_error=True)
    else:
        page._processing_cancelled()
    assert page._validation_feedback is None
    for locale in ("en", "pt-BR", "en"):
        page.retranslate(Translator(locale))
        assert page._validation_feedback is None
        assert page.status_label.text() != Translator(locale).text("masking.package.error.confirmation")


@pytest.mark.parametrize("error_type", [PlanningError, PackageError, CSVAnonymizationError])
@pytest.mark.parametrize("raw", ["A confirmação da senha não corresponde à senha do pacote.", "masking.error.unknown_columns & <literal> {columns}"])
def test_unclassified_errors_remain_literal_even_when_they_look_familiar(masking_pages, error_type, raw):
    _, page, source, _, _ = masking_pages()
    confirmation_failure(page, source)
    error = error_type(raw)
    page._show_validation_error(error)
    assert page._validation_feedback is None
    for locale in ("en", "pt-BR", "en"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == raw and page.status_label.property("feedbackState") == "error"
        assert page.status_label.textFormat() is Qt.TextFormat.PlainText


@pytest.mark.parametrize("metadata", ["masking.package.error.confirmation", object(), MaskingValidationFeedback("future.validation.reason")])
def test_unsupported_metadata_keeps_legacy_fallback(masking_pages, metadata):
    _, page, _, _, _ = masking_pages()
    error = PackageError("Existing safe literal")
    error.gui_validation_feedback = metadata
    page._show_validation_error(error)
    page.retranslate(Translator("en"))
    assert page.status_label.text() == str(error) and page._validation_feedback is None


def test_feedback_is_deeply_immutable_and_captures_no_mutable_models_or_passwords():
    inner = MaskingValidationFeedback("masking.error.unknown_columns", column_references=(("Header & <literal>", 2),))
    outer = MaskingValidationFeedback("profile.partial", details=inner, literal_suffix=" Safe service suffix")
    with pytest.raises(FrozenInstanceError):
        outer.message_id = "other.reason"
    with pytest.raises(TypeError):
        inner.column_references[0][1] = 9
    assert pickle.loads(pickle.dumps(outer)) == outer
    assert isinstance(hash(outer), int)


@pytest.mark.parametrize("fields", [
    {"parameters": {"headers": "Mutable"}}, {"parameters": (("headers", object()),)},
    {"column_references": [("Mutable", 1)]}, {"column_references": (("Header", True),)},
    {"details": object()}, {"literal_suffix": []},
])
def test_feedback_rejects_mutable_or_unsupported_presentation_parameters(fields):
    with pytest.raises(TypeError, match="Invalid GUI validation presentation"):
        MaskingValidationFeedback("masking.error.unknown_columns", **fields)


@pytest.mark.parametrize("case", ["short_password", "existing_destination", "header_collision"])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_service_owned_validation_remains_literal_and_clears_prior_local_feedback(masking_pages, locale, case):
    _, page, source, _, _ = masking_pages(locale)
    output = confirmation_failure(page, source)
    protected = None
    if case == "short_password":
        page.transfer_controls.password.setText("1234567")
        page.transfer_controls.confirmation.setText("1234567")
        page._start_processing(output, overwrite=False)
    elif case == "existing_destination":
        protected = source.with_name("protected.dmspackage")
        protected.write_bytes(b"synthetic protected sentinel")
        page.transfer_controls.destination.setText(str(protected))
        page._start_processing(output, overwrite=False)
    else:
        page._output_name_fields[0].setText("Repeated")
        page._output_name_fields[1].setText("Repeated")
        page.validate_button.click()
        assert not page._configuration_validated and not page.generate_button.isEnabled()
    raw = page.status_label.text()
    assert raw and page._validation_feedback is None and page._status_message is None
    for language in ("en", "pt-BR", "en"):
        page.retranslate(Translator(language))
        assert page.status_label.text() == raw and page._validation_feedback is None
        assert page.status_label.property("feedbackState") == "error"
    assert page._worker is None and not output.exists()
    if protected is not None:
        assert protected.read_bytes() == b"synthetic protected sentinel"


@pytest.mark.parametrize("terminal", ["success", "failure", "cancelled"])
def test_valid_retry_replaces_feedback_and_preserves_existing_worker_lifecycle(masking_pages, monkeypatch, terminal):
    app, page, source, _, _ = masking_pages()
    output = confirmation_failure(page, source)
    page.transfer_controls.confirmation.setText(page.transfer_controls.password.text())
    monkeypatch.setattr(AnonymizationWorker, "start", lambda self: None)
    page._start_processing(output, overwrite=False)
    worker = page._worker
    assert worker is not None and page._validation_feedback is None
    assert not page.transfer_controls.password.text() and not page.transfer_controls.confirmation.text()
    page.retranslate(Translator("en"))
    assert page.status_label.text() == Translator("en").text("masking.status.processing")
    assert page._worker is worker and page._validation_feedback is None
    if terminal == "success":
        worker.completed.emit(AnonymizationResult(output, 2, 0.1, new_mappings=2))
    elif terminal == "failure":
        worker.failed.emit(CSVAnonymizationError("Synthetic safe service failure"))
    else:
        worker.cancelled.emit()
    # The paused test worker never runs its normal finally; release its test input.
    worker._transfer_package_request = None
    worker.finished.emit()
    app.processEvents()
    for locale in ("pt-BR", "en", "pt-BR"):
        page.retranslate(Translator(locale))
        assert page._worker is None and page._validation_feedback is None
        assert page.status_label.property("feedbackState") == {"success": "success", "failure": "error", "cancelled": "warning"}[terminal]
        assert page.status_label.text() != Translator(locale).text("masking.package.error.confirmation")
