"""HTML error localization must not reinterpret content or alter restoration."""

from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
import pickle
import sqlite3

import pytest
from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QTextCursor

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.gui.html_restoration_widget import HTMLRestorationWidget
from data_mask_studio.gui.html_restoration_worker import HTMLRestorationWorker
from data_mask_studio.html_restoration import (
    HTMLMissingCodeError, HTMLMissingCodePolicy, HTMLRestorationCancelled, HTMLRestorationError,
    HTMLRestorationExplanation, HTMLRestorationReason, HTMLRestorationSecurityError,
    HTMLUnsupportedContextError, inspect_html,
)
from data_mask_studio.html_restoration import inspector
from data_mask_studio.i18n import Translator
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import MappingCandidate
from test_file_restoration_interface import finish
from test_html_restoration_localization import pages, ORIGINAL


@pytest.mark.parametrize("unified", [False, True])
def test_empty_input_is_localized_at_selection_and_retranslation(pages, unified):
    _, shell, case = pages()
    empty = case[1].with_name("empty.html")
    empty.touch()
    page = shell.html_widget if unified else HTMLRestorationWidget(lambda: case[0].as_read_only())
    if unified:
        assert not shell.load_file(empty)
    else:
        page.load_html(str(empty))
    for target in ("en", "pt-BR", "en"):
        (shell if unified else page).retranslate(Translator(target))
        expected = "The HTML file is empty." if target == "en" else "O arquivo HTML está vazio."
        assert page.status_label.text() == expected
        if unified:
            assert shell.selection_status.text() == expected
        assert page.status_label.property("feedbackState") == "error"


@pytest.mark.parametrize("failure,expected", [
    ("publication", "Unable to generate the restored HTML file."),
    ("existing", "The destination file already exists."),
])
def test_real_worker_errors_are_localized_and_never_publish(pages, monkeypatch, failure, expected):
    app, shell, case = pages("en")
    assert shell.load_file(case[1])
    page = shell.html_widget
    output = case[1].with_name("restored.html")
    if failure == "existing":
        output.write_bytes(b"PRESERVE")
    else:
        def rejected(*args, **kwargs):
            raise PermissionError("PRIVATE-PUBLICATION-DETAIL")
        monkeypatch.setattr("data_mask_studio.html_restoration.restorer.publish", rejected)
    page.start_restoration(output, overwrite=False)
    finish(app, page)
    assert page.status_label.text() == expected
    assert page.status_label.property("feedbackState") == "error"
    assert page._last_output_path is None and page.open_folder_button.isHidden()
    assert output.read_bytes() == b"PRESERVE" if failure == "existing" else not output.exists()


CASES = [
    (HTMLRestorationReason.SOURCE_MISSING, "O arquivo HTML selecionado não existe."),
    (HTMLRestorationReason.INPUT_PERMISSION_DENIED, "Não foi possível ler o arquivo por falta de permissão."),
    (HTMLRestorationReason.INPUT_ACCESS_FAILED, "Não foi possível acessar o arquivo HTML selecionado."),
    (HTMLRestorationReason.INPUT_EMPTY, "O arquivo HTML está vazio."),
    (HTMLRestorationReason.ENCODING_UNIDENTIFIED, "Não foi possível identificar a codificação do arquivo HTML."),
    (HTMLRestorationReason.INPUT_PROCESSING_FAILED, "Não foi possível processar a codificação do arquivo HTML."),
    (HTMLRestorationReason.OUTPUT_ENCODING_FAILED, "A codificação original não representa um dos valores restaurados."),
    (HTMLRestorationReason.OUTPUT_FAILED, "Não foi possível gerar o arquivo HTML restaurado."),
    (HTMLRestorationReason.DESTINATION_ALIAS, "O arquivo restaurado não pode substituir o HTML de entrada."),
    (HTMLRestorationReason.DESTINATION_FOLDER_MISSING, "A pasta escolhida para o arquivo não existe."),
    (HTMLRestorationReason.DESTINATION_EXISTS, "O arquivo de destino já existe."),
]


def message_id(reason):
    return {
        HTMLRestorationReason.DESTINATION_ALIAS: "restoration.html.error.source_alias",
        HTMLRestorationReason.DESTINATION_FOLDER_MISSING: "restoration.csv.error.destination_folder_missing",
        HTMLRestorationReason.DESTINATION_EXISTS: "restoration.csv.error.destination_exists",
    }.get(reason, "restoration.html.error." + reason.value)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("reason,legacy", CASES, ids=[reason.value for reason, _ in CASES])
def test_original_failure_points_supply_only_safe_reason_metadata(pages, monkeypatch, locale, reason, legacy):
    _, shell, (repository, source) = pages(locale)
    page = shell.html_widget
    inspection = inspect_html(source)
    output = source.with_name("not-published.html")
    operation = lambda: page._service.restore(inspection, output,
        missing_code_policy=HTMLMissingCodePolicy.KEEP,
        representation_policy=RepresentationPolicy.FIRST_ORIGINAL, overwrite=False)
    if reason is HTMLRestorationReason.SOURCE_MISSING:
        source.unlink()  # This test's synthetic source, never user data.
    elif reason in (HTMLRestorationReason.INPUT_PERMISSION_DENIED, HTMLRestorationReason.INPUT_ACCESS_FAILED):
        original_open = Path.open

        def blocked(path, *args, **kwargs):
            if path == source:
                raise (PermissionError if reason is HTMLRestorationReason.INPUT_PERMISSION_DENIED else OSError)(
                    "PRIVATE-INPUT-DETAIL")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", blocked)
        operation = lambda: inspect_html(source)
    elif reason is HTMLRestorationReason.INPUT_EMPTY:
        source.write_bytes(b"")
        operation = lambda: inspect_html(source)
    elif reason is HTMLRestorationReason.ENCODING_UNIDENTIFIED:
        # The existing final guard is normally unreachable with Latin-1 available.
        # Exercise that guard with a controlled decoder failure, not new policy.
        def undecodable(*args):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "synthetic")
        monkeypatch.setattr(inspector, "_decode_sample", undecodable)
        operation = lambda: inspector.detect_html_encoding(b"synthetic")
    elif reason is HTMLRestorationReason.INPUT_PROCESSING_FAILED:
        source.write_bytes(b"<p>\xff</p>")  # Becomes invalid after the UTF-8 inspection.
        operation = lambda: page._service.analyze(inspection)
    elif reason is HTMLRestorationReason.OUTPUT_ENCODING_FAILED:
        with repository.transaction() as transaction:
            transaction.upsert_batch([MappingCandidate("EMOJI-ABCDEFGHIJKL", "EMOJI", "Synthetic 😀", "Synthetic")])
        source.write_bytes("<p>EMOJI-ABCDEFGHIJKL</p> ação".encode("cp1252"))
        inspection = inspect_html(source)
        assert inspection.encoding == "windows-1252"
    elif reason is HTMLRestorationReason.OUTPUT_FAILED:
        def fail_publication(*args, **kwargs):
            raise PermissionError("PRIVATE-PUBLICATION-DETAIL")
        monkeypatch.setattr("data_mask_studio.html_restoration.restorer.publish", fail_publication)
    elif reason is HTMLRestorationReason.DESTINATION_ALIAS:
        output = source
    elif reason is HTMLRestorationReason.DESTINATION_FOLDER_MISSING:
        output = source.parent / "absent-folder" / "out.html"
    elif reason is HTMLRestorationReason.DESTINATION_EXISTS:
        output.write_bytes(b"DO NOT OVERWRITE")

    with pytest.raises(HTMLRestorationError) as raised:
        operation()
    error = raised.value
    assert type(error) is HTMLRestorationError and error.args == (legacy,) and str(error) == legacy
    assert asdict(error.explanation) == {"reason": reason}
    assert not hasattr(error.explanation, "__dict__")
    page._failed(error)
    for target in (locale, "en", "pt-BR"):
        page.retranslate(Translator(target))
        assert page.status_label.text() == Translator(target).text(message_id(reason))
        assert page.status_label.property("feedbackState") == "error"
        assert page._last_error is error
        assert "PRIVATE-" not in page.status_label.text() and ORIGINAL not in page.status_label.text()
        assert page.status_label.textFormat() == Qt.TextFormat.PlainText
    assert not list(source.parent.glob(".*.tmp"))
    if reason is HTMLRestorationReason.DESTINATION_EXISTS:
        assert output.read_bytes() == b"DO NOT OVERWRITE"
    elif reason is not HTMLRestorationReason.DESTINATION_ALIAS:
        assert not output.exists()


@pytest.mark.parametrize("error_type", [HTMLRestorationError, HTMLRestorationSecurityError,
    HTMLUnsupportedContextError, HTMLMissingCodeError, HTMLRestorationCancelled])
def test_optional_immutable_metadata_preserves_exception_contract_and_pickle(error_type):
    legacy = error_type("legacy safe message", 42)
    assert legacy.args == ("legacy safe message", 42) and legacy.explanation is None
    assert str(legacy) == str(RuntimeError("legacy safe message", 42))
    explanation = HTMLRestorationExplanation(HTMLRestorationReason.INPUT_EMPTY)
    with pytest.raises(FrozenInstanceError):
        explanation.reason = HTMLRestorationReason.OUTPUT_FAILED
    error = error_type("legacy safe message", explanation=explanation)
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is error_type and restored.args == error.args
    assert restored.explanation == explanation


@pytest.mark.parametrize("explanation", [None, "unclassified", HTMLRestorationExplanation("future_reason")])
def test_unknown_metadata_remains_literal_in_widget_and_unified_selection(pages, monkeypatch, explanation):
    _, shell, (_, source) = pages()
    page = shell.html_widget
    # Deliberately identical to a known message: content must not classify it.
    error = HTMLRestorationError("O arquivo HTML está vazio.", explanation=explanation)
    page._failed(error)

    def fail_inspection(*args):
        raise error

    monkeypatch.setattr("data_mask_studio.gui.html_restoration_widget.inspect_html", fail_inspection)
    assert not shell.load_file(source)
    for locale in ("en", "pt-BR", "en"):
        shell.retranslate(Translator(locale))
        assert page.status_label.text() == shell.selection_status.text() == str(error)
        assert page._status_message is shell._selection_message_id is None
        assert page.status_label.property("feedbackState") == "error"
        assert page._last_error is error


@pytest.mark.parametrize("error_type,identifier", [
    (HTMLRestorationSecurityError, "restoration.csv.error.security"),
    (HTMLMissingCodeError, "restoration.html.error.missing"),
    (HTMLUnsupportedContextError, "restoration.html.error.context"),
])
def test_preexisting_typed_redactions_take_precedence_over_optional_metadata(pages, error_type, identifier):
    _, shell, _ = pages()
    page = shell.html_widget
    page._failed(error_type("PRIVATE-DETAIL", explanation=HTMLRestorationExplanation(
        HTMLRestorationReason.INPUT_EMPTY)))
    for locale in ("en", "pt-BR"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == Translator(locale).text(identifier)
        assert page.status_label.property("feedbackState") == "error"
        assert "PRIVATE" not in page.status_label.text()


@pytest.mark.parametrize("completed", ["analysis", "restoration"])
def test_retranslation_of_failed_new_selection_preserves_previous_valid_state_without_io(pages, monkeypatch, completed):
    app, shell, (_, source) = pages()
    assert shell.load_file(source)
    page = shell.html_widget
    page.options_toggle.setChecked(True)
    page.missing_policy_combo.setCurrentIndex(1)
    page.representation_combo.setCurrentIndex(1)
    output = source.with_name("completed.html")
    page.start_analysis() if completed == "analysis" else page.start_restoration(output, overwrite=False)
    finish(app, page)
    shell.show()
    app.processEvents()
    assert not shell.load_file(source.with_name("missing-new-selection.html"))
    page.summary.setFocus()
    cursor = page.summary.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    page.summary.setTextCursor(cursor)
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en", "pt-BR")]

    def state():
        return (page._inspection, page.path_field.text(), page._displayed_result,
            page._last_output_path, page._last_error, page._worker, shell.stack.currentWidget(),
            page.options_toggle.isChecked(), page.missing_policy_combo.currentData(),
            page.representation_combo.currentData(), page.progress_bar.value(), page.progress_bar.isHidden(),
            page.open_folder_button.isVisible(), app.focusWidget(), page.summary.textCursor().anchor(),
            page.summary.textCursor().position(), page.summary.verticalScrollBar().value(),
            page.summary.horizontalScrollBar().value())

    before = state()
    result = page._displayed_result
    assert result is not None and page.progress_bar.value() == 100

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not access input/vault or parse/restore HTML")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        patch.setattr(page._service, "analyze", forbidden)
        patch.setattr(page._service, "restore", forbidden)
        patch.setattr(page._service, "_repository_factory", forbidden)
        patch.setattr("data_mask_studio.gui.html_restoration_widget.inspect_html", forbidden)
        patch.setattr("data_mask_studio.html_restoration.context._Contexts", forbidden)
        patch.setattr(HTMLRestorationWorker, "start", forbidden)
        for translator in translators:
            shell.retranslate(translator)
            assert state() == before and page._displayed_result is result
            assert page.status_label.text() == shell.selection_status.text() == translator.text(
                "restoration.html.error.source_missing")
            assert page.status_label.property("feedbackState") == "error"
    assert source.exists() and (output.exists() if completed == "restoration" else not output.exists())


@pytest.mark.parametrize("replacement", [False, True])
def test_retired_worker_cannot_deliver_a_typed_error_to_a_new_context(pages, monkeypatch, replacement):
    app, shell, (_, source) = pages()
    assert shell.load_file(source)
    page = shell.html_widget
    monkeypatch.setattr(HTMLRestorationWorker, "start", lambda _: None)
    page.start_restoration(source.with_name("old.html"), overwrite=False)
    old = page._worker

    class LateSignals(QThread):
        def run(self):
            old.failed.emit(HTMLRestorationError("OLD-TYPED-ERROR", explanation=
                HTMLRestorationExplanation(HTMLRestorationReason.OUTPUT_FAILED)))
            old.finished.emit()

    emitter = LateSignals(page)
    emitter.start()
    assert emitter.wait(5000)
    old.finished.emit()
    if replacement:
        page.start_restoration(source.with_name("new.html"), overwrite=False)
    else:
        shell.clear_selection()
    shell.retranslate(Translator("en"))
    before = page._worker, page.status_label.text(), page._inspection, page.progress_bar.value()
    app.processEvents()
    assert (page._worker, page.status_label.text(), page._inspection, page.progress_bar.value()) == before
    assert page._last_error is None and page._last_output_path is None
    if page._worker is not None:
        page._worker.finished.emit()
