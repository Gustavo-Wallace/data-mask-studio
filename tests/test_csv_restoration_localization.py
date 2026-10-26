"""Presentation locales must never authorize or alter restoration."""

from dataclasses import replace
from threading import Event
from time import perf_counter

import pytest
from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.file_restoration_widget import FileRestorationWidget
from data_mask_studio.gui.restoration_widget import RestorationWidget
from data_mask_studio.gui.restoration_worker import CSVRestorationWorker
from data_mask_studio.gui import restoration_worker as worker_module
from data_mask_studio.i18n import Translator, load_catalogs
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.restoration import (
    AnalysisResult, MissingCodePolicy, RepresentationPolicy, RestorationError,
    RestorationProgress, RestorationResult, RestorationStage, suggested_output_path,
)
from data_mask_studio.transfer_package import encrypt_package
from test_dialog_consistency import modal_reply
from test_file_restoration_interface import finish
from test_navigation_localization import build_localized_window
from test_package_csv_restoration import make_case, PASSWORD, read_rows


@pytest.fixture
def pages(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    widgets = []

    def create(locale="pt-BR", *, case=None):
        if case is None:
            root = tmp_path / f"case-{len(widgets)}"
            root.mkdir()
            case = make_case(root)
        repository, config, *_ = case
        page = FileRestorationWidget(lambda: repository.as_read_only(), translator=Translator(locale))
        widgets.append(page)
        return app, page, case

    yield create
    for widget in widgets:
        assert widget.stop_worker()


def load_csv(page, case, *, package=False, indexes=(0, 1)):
    _, config, package_path, *_ = case
    assert page.load_file(config.source_path)
    widget = page.csv_widget
    for index in indexes:
        widget._checkboxes[index].setChecked(True)
    if package:
        widget.source_combo.setCurrentIndex(widget.source_combo.findData("package"))
        widget.package_controls.path.setText(str(package_path))
        widget.package_controls.password.setText(PASSWORD)
    return widget


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_shared_shell_injection_navigation_and_csv_labels(pages, tmp_path, locale):
    app, page, case = pages(locale)
    translator = Translator(locale)
    assert page.stack.count() == 3 and page.stack.currentIndex() == 0
    assert page.select_button.text() == translator.text("restoration.file.select")
    assert page.guidance.text() == translator.text("restoration.file.guidance")
    assert page.format_label.text() == translator.text("restoration.file.formats")
    assert page.guidance.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert page.csv_widget._translator is page._translator
    csv = page.csv_widget
    for control, message_id in (
        (csv.analyze_button, "restoration.csv.analyze"),
        (csv.generate_button, "restoration.csv.generate"),
        (csv.select_all_button, "restoration.csv.select_all"),
        (csv.unselect_all_button, "restoration.csv.deselect_all"),
        (csv.open_folder_button, "restoration.csv.open_folder"),
        (csv.status_label, "restoration.csv.status.initial"),
    ):
        assert control.text() == translator.text(message_id)
    assert csv.table.empty_text == translator.text("restoration.csv.table.empty")
    assert csv.summary.empty_text == translator.text("restoration.csv.summary.empty")
    assert csv.source_combo.itemText(0) == ("Local vault" if locale == "en" else "Cofre local")
    assert csv.source_combo.itemText(1) == ("Transfer package" if locale == "en" else "Pacote de transferência")
    assert [csv.table.horizontalHeaderItem(i).text() for i in range(2)] == (
        ["Restore", "Header"] if locale == "en" else ["Restaurar", "Cabeçalho"])
    assert csv.package_controls.show_password.text() == translator.text("restoration.package.show_password")
    assert not csv.generate_button.isEnabled() and not csv.analyze_button.isEnabled()
    root = tmp_path / "window"
    root.mkdir()
    window = build_localized_window(root, translator)
    assert len(window.page_widgets) == window.page_stack.count() == 8
    assert window.file_restoration_widget._translator is translator
    assert window.restoration_widget._translator is window.restoration_widget.package_controls._translator is translator
    assert window.page_index(window.restoration_widget) == window.page_index(window.html_restoration_widget) == 2
    assert window.page_shells[2].header.title_label.text() == translator.text("navigation.restore_file")
    assert window.page_shells[2].header.description_label.text() == translator.text("navigation.description.restore_file")
    window.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert window.restoration_widget._translator is window._translator
    assert page.csv_widget is csv  # Independent windows retain their explicit locale.


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_localized_file_dialogs_preserve_filters_selection_and_cancel(pages, tmp_path, monkeypatch, locale):
    _, page, case = pages(locale)
    translator = Translator(locale)
    calls = []

    def choose(*args):
        calls.append(args)
        return str(case[1].source_path), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    page.select_button.click()
    assert calls[-1][1] == translator.text("restoration.file.select")
    assert [part[part.index("("):] for part in calls[-1][3].split(";;")] == [
        "(*.csv *.html *.htm)", "(*.csv)", "(*.html *.htm)"]
    csv = page.csv_widget
    csv.select_all_columns()
    csv.source_combo.setCurrentIndex(1)
    csv.package_controls.password.setText(PASSWORD)
    before = csv._inspection, tuple(csv._checkboxes), csv._configuration()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: ("", ""))
    csv.select_button.click()
    csv.package_controls.browse.click()
    assert (csv._inspection, tuple(csv._checkboxes), csv._configuration()) == before
    assert csv.package_controls.password.text() == PASSWORD
    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    standalone = RestorationWidget(lambda: case[0].as_read_only(), translator=translator)
    standalone.select_button.click()
    assert calls[-1][1] == translator.text("restoration.csv.select")
    assert calls[-1][3].endswith("(*.csv)")
    csv.package_controls.browse.click()
    assert calls[-1][1] == translator.text("restoration.package.select_title")
    assert calls[-1][3].endswith("(*.dmspackage)")
    assert not csv.package_controls.password.text()  # Actual selection still clears it.
    monkeypatch.setattr(csv, "_confirm_sensitive_data", lambda: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (calls.append(args) or ("", "")))
    csv._choose_output()
    assert calls[-1][1] == translator.text("restoration.csv.save_title")
    assert calls[-1][2] == str(suggested_output_path(case[1].source_path))
    assert calls[-1][3].endswith("(*.csv)") and csv._worker is None


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("missing", list(MissingCodePolicy))
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_policy_values_and_selected_columns_survive_retranslation(pages, locale, missing, representation):
    _, page, case = pages(locale)
    csv = load_csv(page, case, indexes=(1,))
    csv.missing_policy_combo.setCurrentIndex(csv.missing_policy_combo.findData(missing.value))
    csv.representation_combo.setCurrentIndex(csv.representation_combo.findData(representation.value))
    config = csv._configuration()
    assert [column.index for column in config.selected_columns] == [1]
    cells = tuple(csv.table.item(i, 1) for i in range(csv.table.rowCount()))
    for target in ("en", "pt-BR", "en"):
        page.retranslate(Translator(target))
        assert csv._configuration() == config
        assert tuple(csv.table.item(i, 1) for i in range(csv.table.rowCount())) == cells
        assert [cell.text() for cell in cells] == list(config.headers)
        assert [csv.missing_policy_combo.itemData(i) for i in range(3)] == ["keep", "empty", "abort"]
        assert [csv.representation_combo.itemData(i) for i in range(2)] == ["first_original", "canonical"]
        assert "1" in csv.selected_count_label.text() and "3" in csv.selected_count_label.text()
    csv.source_combo.setCurrentIndex(1)
    package_config = csv._configuration()
    assert package_config.missing_code_policy is MissingCodePolicy.ABORT
    for target in ("pt-BR", "en"):
        page.retranslate(Translator(target))
        assert csv._configuration() == package_config
        assert not csv.missing_policy_combo.isEnabled() and not csv.analyze_button.isEnabled()
        assert csv.missing_policy_combo.currentData() == "abort"
        assert ("required for packages" if target == "en" else "obrigatório no pacote") in csv.options_summary.text()
    csv.source_combo.setCurrentIndex(0)
    assert csv._configuration() == config


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("filename", ["ação & 漢字_masked.csv", "legacy_anonimizado.csv", "upper.CSV"])
def test_dispatch_and_retranslation_keep_literal_filenames_and_headers(pages, locale, filename):
    _, page, case = pages(locale)
    path = case[1].source_path.with_name(filename)
    headers = ["<b>Synthetic & header</b>", "Dados 漢字"]
    path.write_text(",".join(headers) + "\nSYNTHETIC,SYNTHETIC\n", encoding="utf-8")
    assert page.load_file(path)
    csv = page.csv_widget
    csv._checkboxes[1].setChecked(True)
    before = csv._configuration()
    for target in ("en", "pt-BR"):
        page.retranslate(Translator(target))
        assert page.stack.currentWidget() is csv and csv._configuration() == before
        assert csv.file_name_label.text() == filename and csv.path_field.text() == str(path)
        assert csv.file_name_label.textFormat() == Qt.TextFormat.PlainText
        assert [csv.table.item(i, 1).text() for i in range(2)] == headers
        assert headers[0] in csv._checkboxes[0].accessibleName()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("delimiter,name_id", [
    (",", "comma"), (";", "semicolon"), ("\t", "tab"), ("|", "pipe"),
])
def test_utf16_encoding_and_delimiter_metadata_are_presentation_only(pages, locale, delimiter, name_id):
    _, page, case = pages(locale)
    path = case[1].source_path.with_name("synthetic-utf16.csv")
    path.write_text(f"Name{delimiter}Kind\nSYNTHETIC{delimiter}TEST\n", encoding="utf-16")
    assert page.load_file(path)
    csv = page.csv_widget
    inspection = csv._inspection
    for target in ("en", "pt-BR"):
        page.retranslate(Translator(target))
        assert csv._inspection is inspection and inspection.delimiter == delimiter
        assert csv.encoding_label.text() == inspection.encoding
        assert csv.delimiter_label.text() == Translator(target).text("masking.separator." + name_id)


def test_progress_and_result_retranslation_preserves_typed_counts_and_severity(pages):
    _, page, case = pages()
    csv = load_csv(page, case)
    csv._processing_started_at = perf_counter() - 2.0
    progress = RestorationProgress(RestorationStage.RESTORING, 12, 5, 2, 3, 1)
    csv._progress_changed(progress)
    message = csv._progress_message
    result = RestorationResult(case[-1], 12, 5, 2, 3, 0, 2.0,
                               MissingCodePolicy.KEEP, RepresentationPolicy.CANONICAL)
    csv._restoration_completed(result)
    for target in ("en", "pt-BR"):
        page.retranslate(Translator(target))
        assert csv._displayed_result is result and csv._progress_message is message
        assert csv.status_label.property("feedbackState") == "warning"
        assert csv.progress_label.text() == Translator(target).text(message[0], **message[1])
        assert str(result.output_path) in csv.summary.toPlainText()
        assert csv._last_output_path == result.output_path


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("package", [False, True])
def test_retranslation_preserves_transient_inputs_focus_and_does_not_emit_selection_signals(pages, package, visible):
    app, page, case = pages()
    csv = load_csv(page, case, package=package, indexes=(0,))
    csv.package_controls.path.setText(str(case[2]))
    csv.package_controls.password.setText(PASSWORD)
    csv.package_controls.show_password.setChecked(visible)
    page.resize(1180, 780)
    page.show()
    app.processEvents()
    focus = csv.package_controls.password if package else csv.options_toggle
    focus.setFocus()
    csv.package_controls.password.setSelection(2, 5)
    before = (csv._inspection, csv._configuration(), tuple(csv._checkboxes),
              csv.package_controls.path.text(), csv.package_controls.password.text(),
              csv.package_controls.password.echoMode(), csv.package_controls.password.selectionStart(),
              csv.package_controls.password.selectedText(), csv.options_toggle.isChecked())
    spies = [QSignalSpy(signal) for signal in (
        csv.source_combo.currentIndexChanged, csv.missing_policy_combo.currentIndexChanged,
        csv.representation_combo.currentIndexChanged, csv.package_controls.password.textChanged,
        csv.package_controls.show_password.toggled)]
    for target in ("en", "pt-BR", "en"):
        page.retranslate(Translator(target))
        app.processEvents()
        assert (csv._inspection, csv._configuration(), tuple(csv._checkboxes),
                csv.package_controls.path.text(), csv.package_controls.password.text(),
                csv.package_controls.password.echoMode(), csv.package_controls.password.selectionStart(),
                csv.package_controls.password.selectedText(), csv.options_toggle.isChecked()) == before
        assert all(spy.count() == 0 for spy in spies)
        assert app.focusWidget() is focus
        assert csv._worker is None
    assert csv.package_controls.password.echoMode() == (
        QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)


@pytest.mark.parametrize("indexes", [(0,), (0, 1)])
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_real_local_and_package_outputs_are_identical_under_both_locales(pages, indexes, representation):
    app, _, case = pages()
    repository, config, package, *_ = case
    original = config.source_path.read_bytes(), package.read_bytes()
    outputs = []
    for locale in ("pt-BR", "en"):
        for package_mode in (False, True):
            _, page, _ = pages(locale, case=case)
            csv = load_csv(page, case, package=package_mode, indexes=indexes)
            csv.missing_policy_combo.setCurrentIndex(csv.missing_policy_combo.findData("abort"))
            csv.representation_combo.setCurrentIndex(csv.representation_combo.findData(representation.value))
            destination = config.source_path.parent / f"{locale}-{package_mode}.csv"
            csv.start_restoration(destination, overwrite=False)
            finish(app, csv)
            outputs.append(destination.read_bytes())
            assert csv._last_error is None and csv._last_output_path == destination
            assert csv._displayed_result.rows_processed == 4
            assert csv._displayed_result.restored_codes == 2 * len(indexes)
            assert csv.progress_bar.value() == csv.progress_bar.maximum() == 1
            assert csv.status_label.property("feedbackState") == "success"
            assert csv.status_label.text() == Translator(locale).text("restoration.csv.status.completed")
            assert "Synthetic Person" not in csv.summary.toPlainText() + csv.status_label.text()
            assert not csv.package_controls.password.text()
            if indexes == (0,):
                assert read_rows(destination)[1][1] == read_rows(config.source_path)[1][1]
    assert all(content == outputs[0] for content in outputs)
    assert (config.source_path.read_bytes(), package.read_bytes()) == original
    assert repository.count() == 1


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("failure", ["password", "binding", "scalar", "composite", "tamper", "publication"])
def test_real_package_failures_remain_fail_closed_without_vault_fallback(pages, monkeypatch, locale, failure):
    app, page, case = pages(locale)
    _, config, package, payload, output = case
    csv = load_csv(page, case, package=True)
    if failure == "password":
        csv.package_controls.password.setText("wrong synthetic password")
    elif failure == "binding":
        config.source_path.write_bytes(config.source_path.read_bytes() + b"\n")
    elif failure in ("scalar", "composite"):
        package.write_bytes(encrypt_package(replace(payload, **{failure + "_mappings": ()}), PASSWORD))
    elif failure == "tamper":
        content = package.read_bytes()
        package.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    else:
        def publication_failed(*args, **kwargs):
            raise OSError("PRIVATE-PUBLICATION-DETAIL")
        monkeypatch.setattr("data_mask_studio.restoration.csv_restorer.publish", publication_failed)

    def forbidden(*args, **kwargs):
        pytest.fail("Package restoration must never fall back to the local vault or environment")

    monkeypatch.setattr(csv._service, "restore", forbidden)
    monkeypatch.setattr("data_mask_studio.environment.environment_lease", forbidden)
    csv.start_restoration(output, overwrite=False)
    finish(app, csv)
    error = csv._last_error
    assert error is not None and error.__traceback__ is error.__context__ is None
    assert not output.exists() and csv._last_output_path is None
    assert csv.progress_bar.maximum() == 1 and csv.progress_bar.value() == 0
    assert csv.open_folder_button.isHidden() and not csv._operation_succeeded
    assert not list(output.parent.glob("*.tmp"))
    for target in (locale, "en" if locale == "pt-BR" else "pt-BR"):
        page.retranslate(Translator(target))
        assert csv._last_error is error and csv.status_label.property("feedbackState") == "error"
        assert csv.status_label.text() == Translator(target).text("restoration.csv.error.package")
        assert not csv.summary.toPlainText() and not csv.package_controls.password.text()
        assert PASSWORD not in csv.status_label.text() and "PRIVATE-" not in csv.status_label.text()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("failure,message_id", [
    ("no_csv", "restoration.csv.error.no_csv"), ("columns", "restoration.csv.error.no_columns"),
    ("destination", "restoration.csv.error.destination"), ("package_path", "restoration.package.error.path"),
    ("password", "restoration.package.error.password"),
])
def test_gui_validation_errors_retranslate_without_changing_eligibility(pages, locale, failure, message_id):
    _, page, case = pages(locale)
    csv = page.csv_widget if failure == "no_csv" else load_csv(page, case)
    if failure == "columns":
        csv.unselect_all_columns()
    if failure in ("package_path", "password"):
        csv.source_combo.setCurrentIndex(1)
        if failure == "password":
            csv.package_controls.path.setText(str(case[2]))
    destination = case[1].source_path if failure == "destination" else case[-1]
    source_bytes = case[1].source_path.read_bytes()
    csv.start_restoration(destination, overwrite=False)
    for target in (locale, "en" if locale == "pt-BR" else "pt-BR"):
        page.retranslate(Translator(target))
        assert csv.status_label.text() == Translator(target).text(message_id)
        assert csv.status_label.property("feedbackState") == "error" and csv._worker is None
        assert not case[-1].exists()
    assert case[1].source_path.read_bytes() == source_bytes


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_analysis_summary_and_free_form_service_messages_remain_safe(pages, locale):
    app, page, case = pages(locale)
    csv = load_csv(page, case)
    csv.start_analysis()
    finish(app, csv)
    result = csv._displayed_result
    assert isinstance(result, AnalysisResult) and result.found_codes == 4
    assert result.cells_analyzed == 8 and result.empty_cells == 2
    assert csv.status_label.text() == Translator(locale).text("restoration.csv.status.analyzed")
    cursor = csv.summary.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    csv.summary.setTextCursor(cursor)
    page.retranslate(Translator("en"))
    assert csv._displayed_result is result and "Found in vault: 4" in csv.summary.toPlainText()
    assert (csv.summary.textCursor().anchor(), csv.summary.textCursor().position()) == (3, 9)
    assert "Synthetic Person" not in csv.summary.toPlainText()
    message = "Safe free-form service message remains literal."
    csv._failed(RestorationError(message))
    for target in ("pt-BR", "en"):
        page.retranslate(Translator(target))
        assert csv.status_label.text() == message and csv.status_label.property("feedbackState") == "error"


@pytest.mark.parametrize("package", [False, True])
def test_retranslation_during_real_worker_cancellation_keeps_ownership_and_no_output(pages, monkeypatch, package):
    app, page, case = pages()
    csv = load_csv(page, case, package=package)
    entered, proceed = Event(), Event()
    original = worker_module.restore_csv_from_package if package else csv._service.restore

    def synchronized(*args, **kwargs):
        entered.set()
        assert proceed.wait(10)
        return original(*args, **kwargs)

    if package:
        monkeypatch.setattr(worker_module, "restore_csv_from_package", synchronized)
    else:
        monkeypatch.setattr(csv._service, "restore", synchronized)
    output = case[-1]
    csv.start_restoration(output, overwrite=False)
    worker = csv._worker
    try:
        assert entered.wait(10)
        page.retranslate(Translator("en"))
        assert csv._worker is worker and not csv.source_combo.isEnabled()
        assert csv.status_label.text() == "Generating restored CSV..."
        csv.cancel_processing()
        assert csv.status_label.text() == "Cancellation requested..."
        assert not csv.cancel_button.isEnabled()
        page.retranslate(Translator("pt-BR"))
        assert not csv.cancel_button.isEnabled() and csv._worker is worker
    finally:
        proceed.set()
    finish(app, csv)
    assert not output.exists() and csv.progress_bar.value() == 0
    assert csv.status_label.property("feedbackState") == "warning"
    assert csv.status_label.text() == Translator("pt-BR").text("restoration.csv.status.cancelled")
    assert not csv.package_controls.password.text() and not csv.package_controls.show_password.isChecked()


@pytest.mark.parametrize("package", [False, True])
def test_retired_csv_worker_signals_cannot_restore_old_translated_feedback(pages, monkeypatch, package):
    app, page, case = pages()
    csv = load_csv(page, case, package=package)
    monkeypatch.setattr(CSVRestorationWorker, "start", lambda _: None)
    csv.start_restoration(case[-1], overwrite=False)
    old = csv._worker
    result = RestorationResult(case[-1], 4, 4, 0, 0, 2, 0.1,
                               MissingCodePolicy.ABORT, RepresentationPolicy.FIRST_ORIGINAL)

    class LateSignals(QThread):
        def run(self):
            old.progress.emit(RestorationProgress(RestorationStage.RESTORING, 4, 4))
            old.completed.emit(result)
            old.failed.emit(RestorationError("OLD-FAILURE-SENTINEL"))
            old.cancelled.emit()
            old.finished.emit()

    emitter = LateSignals(csv)
    emitter.start()
    assert emitter.wait(5000)
    old.finished.emit()  # Retire before delivering the queued signals from the other thread.
    page.clear_selection()
    page.retranslate(Translator("en"))
    before = csv.status_label.text(), page.format_label.text(), csv.progress_bar.value()
    app.processEvents()
    assert (csv.status_label.text(), page.format_label.text(), csv.progress_bar.value()) == before
    assert csv._worker is None and csv._displayed_result is None
    assert not csv.summary.toPlainText() and csv._last_output_path is None
    assert csv.open_folder_button.isHidden() and not csv.package_controls.password.text()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("interaction", ["enter", "escape", "ok"])
def test_sensitive_confirmation_keeps_native_roles_and_safe_cancel_default(pages, locale, interaction):
    app, page, _ = pages(locale)
    csv = page.csv_widget

    def reply():
        dialog = app.activeModalWidget()
        assert dialog.windowTitle() == Translator(locale).text("restoration.csv.sensitive_title")
        assert dialog.text() == Translator(locale).text("restoration.csv.sensitive_text")
        assert dialog.standardButtons() == QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.StandardButton.Cancel
        assert dialog.standardButton(dialog.escapeButton()) == QMessageBox.StandardButton.Cancel
        assert dialog.button(QMessageBox.StandardButton.Cancel).text() == Translator(locale).text("dialog.button.cancel")
        if interaction == "ok":
            dialog.button(QMessageBox.StandardButton.Ok).click()
        else:
            QTest.keyClick(dialog, Qt.Key.Key_Return if interaction == "enter" else Qt.Key.Key_Escape)

    with modal_reply(csv, reply):
        result = csv._confirm_sensitive_data()
    assert result == (QMessageBox.StandardButton.Ok if interaction == "ok" else QMessageBox.StandardButton.Cancel)
    assert csv._confirmation_dialog is None and csv._worker is None


@pytest.mark.parametrize("extension", [".html", ".HTM"])
def test_shared_retranslation_does_not_migrate_or_reset_html_specific_content(pages, extension):
    app, page, case = pages()
    html_file = case[1].source_path.with_suffix(extension)
    html_file.write_text("<!doctype html><p>SYNTHETIC</p>", encoding="utf-8")
    assert page.load_file(html_file)
    html = page.html_widget
    html.missing_policy_combo.setCurrentIndex(1)
    html.summary.setPlainText("Synthetic HTML feedback remains literal.")
    before = html._inspection, html.summary.toPlainText(), html.missing_policy_combo.currentData(), html.status_label.text()
    for target in ("en", "pt-BR", "en"):
        page.retranslate(Translator(target))
        assert page.stack.currentWidget() is html
        assert (html._inspection, html.summary.toPlainText(), html.missing_policy_combo.currentData(), html.status_label.text()) == before
        assert html.generate_button.text() == "Gerar HTML restaurado"
        assert html.select_button.text() == Translator(target).text("restoration.file.select")
        assert page.format_label.text() == Translator(target).text("restoration.file.html_type")
    load_csv(page, case)
    assert html._inspection is None and not html.summary.toPlainText()
    assert page.stack.currentWidget() is page.csv_widget and page.csv_widget.status_label.property("feedbackState") == "success"


def test_saved_english_preference_does_not_activate_global_locale(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    preferences = PreferencesService(tmp_path / "preferences.json")
    preferences.save_language("en")
    window = build_localized_window(tmp_path, preferences_service=preferences)
    assert window._translator.locale == window.file_restoration_widget._translator.locale == "pt-BR"
    assert window.restoration_widget.source_combo.currentText() == "Cofre local"
    assert window.file_restoration_widget.select_button.text() == "Selecionar arquivo mascarado"
    assert window.navigation.buttons[2].text() == "Restaurar arquivo"


def test_restoration_catalogs_have_matching_ids_and_unmodified_protocols():
    catalogs = load_catalogs()
    ids = {key for key in catalogs["en"] if key.startswith(("restoration.file.", "restoration.csv.", "restoration.package."))}
    assert ids and ids <= set(catalogs["pt-BR"])
    assert MissingCodePolicy.ABORT.value == "abort"
    assert RepresentationPolicy.FIRST_ORIGINAL.value == "first_original"
