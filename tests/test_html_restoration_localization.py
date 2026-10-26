"""HTML locale is presentation only, including unified CSV/HTML transitions."""

import codecs
import sqlite3
from threading import Event

import pytest
from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.file_restoration_widget import FileRestorationWidget
from data_mask_studio.gui.html_restoration_widget import HTMLRestorationWidget
from data_mask_studio.gui.html_restoration_worker import HTMLAnalysisWorker, HTMLRestorationWorker
from data_mask_studio.html_restoration import (
    HTMLAnalysisResult, HTMLMissingCodeError, HTMLMissingCodePolicy, HTMLRestorationError,
    HTMLRestorationProgress, HTMLRestorationResult, HTMLRestorationSecurityError, HTMLRestorationStage,
    HTMLUnsupportedContextError, suggested_html_output_path,
)
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from test_dialog_consistency import modal_reply
from test_file_restoration_interface import finish, assert_no_feedback
from test_html_contexts import Parsed
from test_navigation_localization import build_localized_window
from test_package_csv_restoration import make_case, PASSWORD

CODE = "PERSON-ABCDEFGHIJKL"
MISSING = "NONE-BCDEFGHIJKLM"
ORIGINAL = "  Synthetic João & <safe>  "
CANONICAL = "Synthetic João & <safe>"


def document(*, missing=False):
    return ("<!DOCTYPE html>\r\n<!-- unchanged -->\r\n<html><head>"
            '<style>.x { color: #123; }</style><script>const literal = "A &amp; B";</script>'
            f'</head><body><p>{CODE}</p><div  title = "{CODE}" data-note="unchanged &amp;"></div>'
            f"<i data-name='{CODE}'></i>" + (f"<span>{MISSING}</span>" if missing else "")
            + "<b>ação &amp; literal</b></body></html>\r\n")


@pytest.fixture
def pages(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    widgets = []

    def create(locale="pt-BR", *, case=None, encoding="utf-8", missing=False, extension=".html"):
        if case is None:
            root = tmp_path / f"case-{len(widgets)}"
            root.mkdir()
            repository = VaultRepository(root / "vault.db", VaultCipher(b"L" * 32))
            with repository.transaction() as transaction:
                transaction.upsert_batch([MappingCandidate(CODE, "PERSON", ORIGINAL, "Name",
                    canonical_value=CANONICAL, normalization_rule=NormalizationRule.COLLAPSE_WHITESPACE)])
            path = root / ("ação & masked" + extension)
            path.write_bytes(document(missing=missing).encode(encoding))
            case = repository, path
        repository, path = case
        page = FileRestorationWidget(lambda: repository.as_read_only(), translator=Translator(locale))
        widgets.append(page)
        return app, page, case

    yield create
    for page in widgets:
        assert page.stop_worker()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_html_labels_injection_and_absence_of_package_controls(pages, tmp_path, locale):
    _, page, case = pages(locale)
    translator = Translator(locale)
    html = page.html_widget
    assert html._translator is page._translator
    assert page.load_file(case[1]) and page.stack.currentWidget() is html
    for control, message_id in (
        (html.select_button, "restoration.file.select"), (html.generate_button, "restoration.html.generate"),
        (html.analyze_button, "restoration.csv.analyze"), (html.open_folder_button, "restoration.csv.open_folder"),
        (html.options_toggle, "restoration.csv.options.show"), (html.status_label, "restoration.html.status.inspected"),
    ):
        assert control.text() == translator.text(message_id)
    assert html.generate_button.text() == ("Generate restored HTML" if locale == "en" else "Gerar HTML restaurado")
    assert html.path_field.accessibleName() == translator.text("restoration.html.path_accessible")
    assert html.path_field.isReadOnly() and html.file_name_label.text() == case[1].name
    assert html.encoding_label.text() == "utf-8"
    assert not hasattr(html, "package_controls") and not hasattr(html, "source_combo")
    assert not page.csv_widget.package_controls.isVisible()
    assert html.summary.empty_text == translator.text("restoration.csv.summary.empty")
    assert page.format_label.text() == translator.text("restoration.file.html_type")
    root = tmp_path / "window"
    root.mkdir()
    window = build_localized_window(root, translator)
    assert window.html_restoration_widget._translator is translator
    assert window.html_restoration_widget.path_field.accessibleName() == translator.text("restoration.html.path_accessible")
    assert window.page_stack.count() == 8 and window.page_index(window.html_restoration_widget) == 2


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("missing", list(HTMLMissingCodePolicy))
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_policy_values_focus_and_connections_survive_retranslation(pages, locale, missing, representation):
    app, page, case = pages(locale)
    page.load_file(case[1])
    html = page.html_widget
    html.missing_policy_combo.setCurrentIndex(html.missing_policy_combo.findData(missing.value))
    html.representation_combo.setCurrentIndex(html.representation_combo.findData(representation.value))
    html.options_toggle.setChecked(True)
    page.resize(1180, 780)
    page.show()
    app.processEvents()
    html.representation_combo.setFocus()
    inspection = html._inspection
    spies = [QSignalSpy(signal) for signal in (
        html.missing_policy_combo.currentIndexChanged, html.representation_combo.currentIndexChanged,
        html.file_loaded, html.busy_changed)]
    for target in ("en", "pt-BR", "en"):
        page.retranslate(Translator(target))
        app.processEvents()
        assert html._inspection is inspection and html.path_field.text() == str(case[1])
        assert html.missing_policy_combo.currentData() == missing.value
        assert html.representation_combo.currentData() == representation.value
        assert [html.missing_policy_combo.itemData(i) for i in range(2)] == ["keep", "abort"]
        assert [html.representation_combo.itemData(i) for i in range(2)] == ["first_original", "canonical"]
        assert all(spy.count() == 0 for spy in spies)
        assert html.representation_combo.hasFocus() and html.options_controls.isVisible()
        assert page.stack.currentWidget() is html and html._worker is None
    html.options_toggle.setChecked(False)
    assert html.options_toggle.hasFocus()
    QTest.keyClick(html.options_toggle, Qt.Key.Key_Tab)
    assert html.analyze_button.hasFocus()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("extension", [".html", ".HTM"])
def test_file_dialog_filters_cancel_and_output_alias_guards(pages, monkeypatch, locale, extension):
    _, page, case = pages(locale, extension=extension)
    path = case[1]
    calls = []

    def choose(*args):
        calls.append(args)
        return str(path), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    standalone = HTMLRestorationWidget(lambda: case[0].as_read_only(), translator=Translator(locale))
    standalone.select_button.click()
    assert calls[-1][1] == Translator(locale).text("restoration.html.select")
    assert calls[-1][3].endswith("(*.html *.htm)")
    page.select_button.click()
    assert "(*.csv *.html *.htm)" in calls[-1][3] and page.stack.currentWidget() is page.html_widget
    html = page.html_widget
    html.summary.setPlainText("Synthetic completed feedback remains valid on picker cancellation.")
    before = html._inspection, html.summary.toPlainText(), html.status_label.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    html.select_button.click()
    assert (html._inspection, html.summary.toPlainText(), html.status_label.text()) == before
    monkeypatch.setattr(html, "_confirm_sensitive_data", lambda: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (calls.append(args) or ("", "")))
    html._choose_output()
    assert calls[-1][1] == Translator(locale).text("restoration.html.save_title")
    assert calls[-1][2] == str(suggested_html_output_path(path))
    assert calls[-1][3].endswith("(*.html)") and html._worker is None
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    original = path.read_bytes()
    html._choose_output()
    assert html.status_label.text() == Translator(locale).text("restoration.html.error.source_alias")
    assert html._worker is None and path.read_bytes() == original


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_real_output_bytes_encoding_and_html_structure_are_locale_independent(pages, encoding, representation):
    app, _, case = pages(encoding=encoding)
    repository, source = case
    original_bytes = source.read_bytes()
    vault_bytes = repository.database_path.read_bytes()
    outputs = []
    for locale in ("pt-BR", "en"):
        _, page, _ = pages(locale, case=case)
        assert page.load_file(source)
        html = page.html_widget
        html.missing_policy_combo.setCurrentIndex(html.missing_policy_combo.findData("abort"))
        html.representation_combo.setCurrentIndex(html.representation_combo.findData(representation.value))
        output = source.with_name(locale + "-restored.html")
        html.start_restoration(output, overwrite=False)
        worker = html._worker
        assert worker._missing_code_policy is HTMLMissingCodePolicy.ABORT
        assert worker._representation_policy is representation
        finish(app, html)
        assert html._last_error is None and html._last_output_path == output
        assert html._displayed_result.total_occurrences == html._displayed_result.restored_occurrences == 3
        assert html.status_label.text() == Translator(locale).text("restoration.html.status.completed")
        assert html.status_label.property("feedbackState") == "success" and html.progress_bar.value() == 100
        assert ORIGINAL not in html.summary.toPlainText() and CANONICAL not in html.summary.toPlainText()
        outputs.append(output.read_bytes())
        rendered = output.read_bytes().decode(encoding)
        parsed = Parsed(rendered)
        value = ORIGINAL if representation is RepresentationPolicy.FIRST_ORIGINAL else CANONICAL
        assert ("div", [("title", value), ("data-note", "unchanged &")]) in parsed.tags
        assert ("i", [("data-name", value)]) in parsed.tags and value in parsed.data
        assert '<style>.x { color: #123; }</style>' in rendered
        assert '<script>const literal = "A &amp; B";</script>' in rendered
        assert rendered.startswith("<!DOCTYPE html>\r\n<!-- unchanged -->\r\n")
        assert '<div  title = "' in rendered and "<b>ação &amp; literal</b>" in rendered
        assert output.read_bytes().startswith(codecs.BOM_UTF8) == (encoding == "utf-8-sig")
    assert outputs[0] == outputs[1]
    assert source.read_bytes() == original_bytes and repository.database_path.read_bytes() == vault_bytes


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("policy", list(HTMLMissingCodePolicy))
def test_missing_tokens_keep_or_abort_with_identical_security_outcomes(pages, locale, policy):
    app, page, case = pages(locale, missing=True)
    page.load_file(case[1])
    html = page.html_widget
    html.missing_policy_combo.setCurrentIndex(html.missing_policy_combo.findData(policy.value))
    output = case[1].with_name("restored.html")
    html.start_restoration(output, overwrite=False)
    finish(app, html)
    result, error = html._displayed_result, html._last_error
    for target in (locale, "en" if locale == "pt-BR" else "pt-BR"):
        page.retranslate(Translator(target))
        assert html._displayed_result is result and html._last_error is error
        if policy is HTMLMissingCodePolicy.KEEP:
            assert output.exists() and MISSING in output.read_text(encoding="utf-8")
            assert result.missing_occurrences == 1 and result.restored_occurrences == 3
            assert html.status_label.property("feedbackState") == "warning"
        else:
            assert not output.exists() and html._last_output_path is None
            assert isinstance(error, HTMLMissingCodeError) and result is None
            assert html.status_label.text() == Translator(target).text("restoration.html.error.missing")
            assert html.status_label.property("feedbackState") == "error"
            assert html.progress_bar.isHidden() and html.progress_bar.value() == 0
            assert html.open_folder_button.isHidden()
        assert ORIGINAL not in html.status_label.text() and MISSING not in html.status_label.text()
    assert not list(output.parent.glob("*.tmp"))


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_analysis_typed_result_progress_and_scroll_survive_retranslation(pages, locale):
    app, page, case = pages(locale, missing=True)
    page.load_file(case[1])
    html = page.html_widget
    html.start_analysis()
    finish(app, html)
    result = html._displayed_result
    assert isinstance(result, HTMLAnalysisResult)
    assert (result.unique_codes, result.total_occurrences, result.found_codes, result.missing_codes) == (2, 4, 1, 1)
    assert html.status_label.property("feedbackState") == "warning"
    progress = html._progress_message
    percentage = html.progress_bar.value()
    page.resize(1000, 640)
    page.show()
    app.processEvents()
    html.summary.setFocus()
    cursor = html.summary.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    html.summary.setTextCursor(cursor)
    scroll = html.summary.verticalScrollBar().value()
    for target in ("en", "pt-BR"):
        page.retranslate(Translator(target))
        assert html._displayed_result is result and html._progress_message is progress
        assert html.progress_bar.value() == percentage == 100
        assert (html.summary.textCursor().anchor(), html.summary.textCursor().position()) == (3, 9)
        assert html.summary.hasFocus()
        assert html.summary.verticalScrollBar().value() == min(scroll, html.summary.verticalScrollBar().maximum())
        assert html.status_label.property("feedbackState") == "warning"
        assert html.status_label.text() == Translator(target).text("restoration.html.status.analyzed")
        assert ORIGINAL not in html.summary.toPlainText()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("failure", ["wrong_key", "tamper", "context", "publication", "collision"])
def test_real_failures_are_safe_and_never_publish_or_replace_output(pages, monkeypatch, locale, failure):
    app, page, case = pages(locale)
    repository, source = case
    html = page.html_widget
    if failure == "wrong_key":
        wrong = VaultRepository(repository.database_path, VaultCipher(b"W" * 32), read_only=True)
        monkeypatch.setattr(html._service, "_repository_factory", lambda: wrong)
    elif failure == "tamper":
        with sqlite3.connect(repository.database_path) as connection:
            encrypted = connection.execute("SELECT encrypted_value FROM vault_variations WHERE code = ?", (CODE,)).fetchone()[0]
            connection.execute("UPDATE vault_variations SET encrypted_value = ? WHERE code = ?",
                               (bytes([encrypted[0] ^ 1]) + encrypted[1:], CODE))
    elif failure == "context":
        source.write_text(f'<script>const value = "{CODE}";</script>', encoding="utf-8")
    elif failure == "publication":
        def fail_publish(*args):
            raise OSError("PRIVATE-PUBLICATION-DETAIL")
        monkeypatch.setattr("data_mask_studio.html_restoration.restorer.publish", fail_publish)
    output = source.with_name("must-not-publish.html")
    if failure == "collision":
        output.write_bytes(b"previous complete synthetic output")
    source_bytes = source.read_bytes()
    page.load_file(source)
    html.start_restoration(output, overwrite=False)
    finish(app, html)
    error = html._last_error
    assert error is not None and html._last_output_path is None
    assert output.read_bytes() == b"previous complete synthetic output" if failure == "collision" else not output.exists()
    assert source.read_bytes() == source_bytes and not list(output.parent.glob("*.tmp"))
    assert html.open_folder_button.isHidden() and html.progress_bar.value() == 0 and html.progress_bar.isHidden()
    for target in ("en", "pt-BR"):
        page.retranslate(Translator(target))
        assert html._last_error is error and html.status_label.property("feedbackState") == "error"
        assert not html.summary.toPlainText()
        assert ORIGINAL not in html.status_label.text() and "PRIVATE-" not in html.status_label.text()
        if failure in ("wrong_key", "tamper"):
            assert isinstance(error, HTMLRestorationSecurityError)
            assert html.status_label.text() == Translator(target).text("restoration.csv.error.security")
        if failure == "context":
            assert isinstance(error, HTMLUnsupportedContextError)
            assert html.status_label.text() == Translator(target).text("restoration.html.error.context")


@pytest.mark.parametrize("phase", ["analysis", "restoration"])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_retranslation_and_cancellation_preserve_real_worker_ownership(pages, monkeypatch, locale, phase):
    app, page, case = pages(locale)
    page.load_file(case[1])
    html = page.html_widget
    entered, proceed = Event(), Event()
    original = html._service.analyze if phase == "analysis" else html._service.restore

    def synchronized(*args, **kwargs):
        entered.set()
        assert proceed.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(html._service, "analyze" if phase == "analysis" else "restore", synchronized)
    output = case[1].with_name("cancelled.html")
    html.start_analysis() if phase == "analysis" else html.start_restoration(output, overwrite=False)
    worker = html._worker
    try:
        assert entered.wait(10)
        inspection = html._inspection
        page.retranslate(Translator("en"))
        assert html._worker is worker and html._inspection is inspection
        assert not html.select_button.isEnabled() and not page.select_button.isEnabled()
        assert not page.load_file(case[1].with_suffix(".csv"))
        page.clear_selection()
        assert html._inspection is inspection and page.stack.currentWidget() is html
        html.cancel_processing()
        assert not html.cancel_button.isEnabled() and html.status_label.text() == "Cancellation requested..."
        page.retranslate(Translator("pt-BR"))
        assert not html.cancel_button.isEnabled() and html._worker is worker
    finally:
        proceed.set()
    finish(app, html)
    assert not output.exists() and html._displayed_result is None
    assert html.status_label.property("feedbackState") == "warning"
    assert html.status_label.text() == "A operação foi cancelada com segurança."
    assert html.progress_bar.isHidden() and html.progress_bar.value() == 0
    assert not list(output.parent.glob("*.tmp"))


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("context", ["clear", "csv", "new_worker"])
def test_queued_terminal_signals_cannot_repopulate_a_new_translated_context(pages, monkeypatch, locale, context):
    app, page, case = pages(locale)
    page.load_file(case[1])
    html = page.html_widget
    monkeypatch.setattr(HTMLRestorationWorker, "start", lambda _: None)
    html.start_restoration(case[1].with_name("old.html"), overwrite=False)
    old = html._worker
    result = HTMLRestorationResult(case[1].with_name("old.html"), "utf-8", 3, 3, 0, 0.1,
                                   HTMLMissingCodePolicy.KEEP, RepresentationPolicy.FIRST_ORIGINAL)

    class LateSignals(QThread):
        def run(self):
            old.progress.emit(HTMLRestorationProgress(HTMLRestorationStage.RESTORING, 100, 100, 3, 3))
            old.completed.emit(result)
            old.failed.emit(HTMLRestorationError("OLD-ERROR-SENTINEL"))
            old.cancelled.emit()
            old.finished.emit()

    emitter = LateSignals(html)
    emitter.start()
    assert emitter.wait(5000)
    assert not page.load_file(case[1])  # Finished thread still owns pending callbacks.
    old.finished.emit()
    if context == "clear":
        page.clear_selection()
    elif context == "csv":
        csv = case[1].with_suffix(".csv")
        csv.write_text("Name\nSYNTHETIC\n", encoding="utf-8")
        assert page.load_file(csv)
    else:
        html.start_restoration(case[1].with_name("new.html"), overwrite=False)
    page.retranslate(Translator("en"))
    current = html._worker
    before = (html._inspection, html.summary.toPlainText(), html.status_label.text(),
              html.progress_bar.value(), page.stack.currentWidget(), page.csv_widget.status_label.text())
    app.processEvents()
    assert (html._inspection, html.summary.toPlainText(), html.status_label.text(),
            html.progress_bar.value(), page.stack.currentWidget(), page.csv_widget.status_label.text()) == before
    assert html._worker is current and html._last_output_path is None
    if current is not None:
        current.finished.emit()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_repeated_csv_package_html_switching_cleans_feedback_without_duplicate_signals(pages, tmp_path, locale):
    app, page, case = pages(locale)
    root = tmp_path / "package-fixture"
    root.mkdir()
    _, config, package, _, _ = make_case(root)
    csv, html = page.csv_widget, page.html_widget
    csv_loaded, html_loaded = QSignalSpy(csv.file_loaded), QSignalSpy(html.file_loaded)
    for cycle in range(3):
        assert page.load_file(config.source_path)
        csv.source_combo.setCurrentIndex(csv.source_combo.findData("package"))
        csv.package_controls.path.setText(str(package))
        csv.package_controls.password.setText(PASSWORD)
        csv.package_controls.show_password.setChecked(True)
        assert page.load_file(case[1])
        assert not csv.package_controls.password.text() and not csv.package_controls.show_password.isChecked()
        assert csv.package_controls.password.echoMode() == QLineEdit.EchoMode.Password
        assert not csv.package_controls.isVisible() and not hasattr(html, "package_controls")
        assert_no_feedback(csv)
        output = case[1].with_name(f"html-{cycle}.html")
        html.start_restoration(output, overwrite=False)
        finish(app, html)
        assert html._last_output_path == output and html.summary.toPlainText()
        page.retranslate(Translator("en" if cycle % 2 == 0 else "pt-BR"))
        assert page.stack.currentWidget() is html
    assert page.load_file(config.source_path)
    assert_no_feedback(html)
    assert csv_loaded.count() == 4 and html_loaded.count() == 3
    assert csv.source_combo.currentData() == "package" and csv.missing_policy_combo.currentData() == "abort"
    assert not csv.package_controls.password.text() and not csv.summary.toPlainText()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("interaction", ["enter", "escape", "ok"])
def test_sensitive_confirmation_and_standard_buttons_keep_safe_semantics(pages, locale, interaction):
    app, page, _ = pages(locale)
    html = page.html_widget

    def reply():
        dialog = app.activeModalWidget()
        assert dialog.windowTitle() == Translator(locale).text("restoration.csv.sensitive_title")
        assert dialog.text() == Translator(locale).text("restoration.html.sensitive_text")
        assert dialog.standardButtons() == QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        assert dialog.standardButton(dialog.defaultButton()) == dialog.standardButton(dialog.escapeButton()) == QMessageBox.StandardButton.Cancel
        assert dialog.button(QMessageBox.StandardButton.Cancel).text() == Translator(locale).text("dialog.button.cancel")
        if interaction == "ok":
            dialog.button(QMessageBox.StandardButton.Ok).click()
        else:
            QTest.keyClick(dialog, Qt.Key.Key_Return if interaction == "enter" else Qt.Key.Key_Escape)

    with modal_reply(html, reply):
        result = html._confirm_sensitive_data()
    assert result == (QMessageBox.StandardButton.Ok if interaction == "ok" else QMessageBox.StandardButton.Cancel)
    assert html._worker is None and html._confirmation_dialog is None


def test_free_form_domain_errors_remain_literal_but_unknown_exceptions_are_redacted(pages):
    _, page, _ = pages()
    html = page.html_widget
    safe = HTMLRestorationError("Safe free-form service message, not a translation identifier.")
    html._failed(safe)
    for target in ("en", "pt-BR"):
        page.retranslate(Translator(target))
        assert html.status_label.text() == str(safe) and html._last_error is safe
        assert html.status_label.property("feedbackState") == "error"
    html._failed(RuntimeError("PRIVATE-TRACEBACK-SENTINEL"))
    for target in ("en", "pt-BR"):
        page.retranslate(Translator(target))
        assert html.status_label.text() == Translator(target).text("restoration.csv.error.unexpected")
        assert "PRIVATE" not in html.status_label.text()


def test_saved_english_preference_does_not_activate_html_translation(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    preferences = PreferencesService(tmp_path / "preferences.json")
    assert preferences.save_language("en").error is None
    window = build_localized_window(tmp_path, preferences_service=preferences)
    html = window.html_restoration_widget
    assert html._translator.locale == window._translator.locale == "pt-BR"
    assert html.generate_button.text() == "Gerar HTML restaurado"
    assert window.navigation.buttons[2].text() == "Restaurar arquivo"
