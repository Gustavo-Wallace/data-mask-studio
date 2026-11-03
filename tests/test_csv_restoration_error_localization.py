"""Typed CSV error presentation must preserve restoration and redaction contracts."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import pickle
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.restoration_widget import RestorationWidget
from data_mask_studio.i18n import Translator
from data_mask_studio.restoration import (
    MissingCodeError, RestorationCancelled, RestorationError, RestorationExplanation,
    RestorationReason, RestorationSecurityError,
)
from data_mask_studio.restoration import csv_restorer
from test_file_restoration_interface import finish
from test_restoration import make_repository, MISSING_CODE, CPF_CODE
from test_csv_restoration_localization import pages, load_csv


@pytest.fixture
def local_page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    repository = make_repository(tmp_path)
    source = tmp_path / "masked.csv"
    source.write_text(f"CPF,Note\n{MISSING_CODE},SYNTHETIC\n", encoding="utf-8")
    page = RestorationWidget(lambda: repository.as_read_only())
    page.load_csv(str(source))
    page._checkboxes[0].setChecked(True)
    page.missing_policy_combo.setCurrentIndex(page.missing_policy_combo.findData("abort"))
    return app, page, repository, source, tmp_path / "restored.csv"


@pytest.mark.parametrize("failure,expected", [
    ("missing", "Restoration stopped because a token was not found (column 'CPF', row 2)."),
    ("headers", "The file headers have changed since selection."),
    ("row", "Row 2: inconsistent number of columns."),
    ("source", "The selected CSV file does not exist."),
])
def test_real_local_failure_uses_explicit_english_and_remains_unpublished(local_page, failure, expected):
    app, page, _, source, destination = local_page
    if failure == "headers":
        source.write_text(f"Changed,Note\n{MISSING_CODE},SYNTHETIC\n", encoding="utf-8")
    elif failure == "row":
        source.write_text(f"CPF,Note\n{MISSING_CODE},SYNTHETIC,extra\n", encoding="utf-8")
    elif failure == "source":
        source.unlink()  # Only the synthetic input created by this fixture.
    page.retranslate(Translator("en"))
    page.start_restoration(destination, overwrite=False)
    finish(app, page)
    assert page.status_label.text() == expected
    assert page.status_label.property("feedbackState") == "error"
    assert not destination.exists() and page._last_output_path is None


# Assert legacy strings independently of the catalogs: metadata is additive only.
CASES = [
    (RestorationReason.SOURCE_MISSING, "O arquivo CSV selecionado nao existe."),
    (RestorationReason.HEADERS_CHANGED, "Os cabecalhos do arquivo foram alterados desde a selecao."),
    (RestorationReason.ROW_COLUMNS, "Linha 2: quantidade de colunas inconsistente."),
    (RestorationReason.ANALYSIS_FAILED, "Nao foi possivel analisar o arquivo CSV selecionado."),
    (RestorationReason.MISSING_MAPPING,
     "A restauracao foi interrompida porque um codigo nao foi encontrado (coluna 'CPF', linha 2)."),
    (RestorationReason.DESTINATION_ALIAS, "O arquivo restaurado nao pode substituir o CSV de entrada."),
    (RestorationReason.DESTINATION_FOLDER_MISSING, "A pasta escolhida para o arquivo nao existe."),
    (RestorationReason.DESTINATION_EXISTS, "O arquivo de destino ja existe."),
    (RestorationReason.DISK_FULL, "Não há espaço suficiente para concluir o arquivo restaurado."),
    (RestorationReason.FILE_REMOVED, "O arquivo CSV foi removido durante a restauração."),
    (RestorationReason.ACCESS_DENIED, "O arquivo está bloqueado ou a pasta de destino não permite escrita."),
    (RestorationReason.IO_FAILED, "Falha de leitura ou escrita durante a restauração do CSV."),
    (RestorationReason.CSV_GENERATION_FAILED, "Nao foi possivel gerar o arquivo CSV restaurado."),
]


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("reason,legacy", CASES, ids=[reason.value for reason, _ in CASES])
def test_real_failure_sources_attach_metadata_without_changing_legacy_contract(
    local_page, monkeypatch, locale, reason, legacy,
):
    _, page, _, source, output = local_page
    config = page._configuration()
    source.write_text(f"CPF,Note\n{CPF_CODE},SYNTHETIC\n", encoding="utf-8")
    if reason is RestorationReason.SOURCE_MISSING:
        source.unlink()
    elif reason is RestorationReason.HEADERS_CHANGED:
        source.write_text("Changed,Note\nSYNTHETIC,SYNTHETIC\n", encoding="utf-8")
    elif reason is RestorationReason.ROW_COLUMNS:
        source.write_text("CPF,Note\n1,2,3\n", encoding="utf-8")
    elif reason in (RestorationReason.ANALYSIS_FAILED, RestorationReason.CSV_GENERATION_FAILED):
        source.write_text('CPF,Note\n"unterminated', encoding="utf-8")
    elif reason is RestorationReason.MISSING_MAPPING:
        source.write_text(f"CPF,Note\n{MISSING_CODE},SYNTHETIC\n", encoding="utf-8")
    elif reason is RestorationReason.DESTINATION_ALIAS:
        output = source
    elif reason is RestorationReason.DESTINATION_FOLDER_MISSING:
        output = output.parent / "absent-folder" / "restored.csv"
    elif reason is RestorationReason.DESTINATION_EXISTS:
        output.write_bytes(b"DO NOT OVERWRITE")
    else:
        failure = {
            RestorationReason.DISK_FULL: OSError(28, "PRIVATE-IO-DETAIL"),
            RestorationReason.FILE_REMOVED: FileNotFoundError("PRIVATE-IO-DETAIL"),
            RestorationReason.ACCESS_DENIED: PermissionError("PRIVATE-IO-DETAIL"),
            RestorationReason.IO_FAILED: OSError(5, "PRIVATE-IO-DETAIL"),
        }[reason]

        def fail_publication(*args, **kwargs):
            raise failure

        monkeypatch.setattr(csv_restorer, "publish", fail_publication)

    original = source.read_bytes() if source.exists() else None
    with pytest.raises(RestorationError) as raised:
        if reason is RestorationReason.ANALYSIS_FAILED:
            page._service.analyze(config)
        else:
            page._service.restore(config, output, overwrite=False)
    error = raised.value
    assert type(error) is (MissingCodeError if reason is RestorationReason.MISSING_MAPPING else RestorationError)
    assert error.args == (legacy,) and str(error) == legacy
    assert error.explanation.reason is reason
    if reason is RestorationReason.MISSING_MAPPING:
        assert error.explanation.header == "CPF" and error.explanation.row_number == 2
    elif reason is RestorationReason.ROW_COLUMNS:
        assert error.explanation.header is None and error.explanation.row_number == 2
    else:
        assert error.explanation.header is error.explanation.row_number is None
    page.retranslate(Translator(locale))
    page._failed(error)
    message_id = "restoration.csv.error." + (
        "source_alias" if reason is RestorationReason.DESTINATION_ALIAS else reason.value)
    params = ({"header": "CPF", "row": 2} if reason is RestorationReason.MISSING_MAPPING else
              {"row": 2} if reason is RestorationReason.ROW_COLUMNS else {})
    assert page.status_label.text() == Translator(locale).text(message_id, **params)
    assert page.status_label.property("feedbackState") == "error"
    assert "PRIVATE-" not in page.status_label.text() and MISSING_CODE not in page.status_label.text()
    assert page._last_error is error
    assert not list(source.parent.glob("*.tmp"))
    assert not list(source.parent.glob(".*.tmp"))
    assert (source.read_bytes() if source.exists() else None) == original
    if reason is RestorationReason.DESTINATION_EXISTS:
        assert output.read_bytes() == b"DO NOT OVERWRITE"
    elif reason is not RestorationReason.DESTINATION_ALIAS:
        assert not output.exists()


@pytest.mark.parametrize("error_type", [RestorationError, MissingCodeError, RestorationSecurityError, RestorationCancelled])
def test_optional_frozen_metadata_preserves_exception_construction_and_round_trip(error_type):
    legacy = error_type("safe", 7)
    assert legacy.args == ("safe", 7) and legacy.explanation is None
    assert str(legacy) == str(RuntimeError("safe", 7))
    explanation = RestorationExplanation(RestorationReason.ACCESS_DENIED)
    with pytest.raises(FrozenInstanceError):
        explanation.reason = RestorationReason.IO_FAILED
    typed = error_type("safe", explanation=explanation)
    restored = pickle.loads(pickle.dumps(typed))
    assert type(restored) is error_type and restored.args == ("safe",)
    assert restored.explanation == explanation


@pytest.mark.parametrize("explanation", [
    None, "unclassified", RestorationExplanation("future_reason"),
    RestorationExplanation(RestorationReason.ROW_COLUMNS),
    RestorationExplanation(RestorationReason.ROW_COLUMNS, row_number=True),
    RestorationExplanation(RestorationReason.ROW_COLUMNS, row_number=0),
    RestorationExplanation(RestorationReason.MISSING_MAPPING, row_number=2),
])
def test_unknown_or_incomplete_metadata_is_literal_not_inferred(local_page, explanation):
    _, page, *_ = local_page
    # Intentionally resembles a known error: text alone must not select a category.
    message = "O arquivo de destino ja existe. | unchanged safe fallback"
    error = RestorationError(message, explanation=explanation)
    page._failed(error)
    for locale in ("en", "pt-BR", "en"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == message and page._last_error is error
        assert page._status_message is None
        assert page.status_label.property("feedbackState") == "error"


def test_windows_disk_full_uses_winerror_without_exposing_os_detail():
    error = OSError("PRIVATE-IO-DETAIL")
    error.winerror = 112
    message, reason = csv_restorer._safe_io_failure(error)
    assert reason is RestorationReason.DISK_FULL
    assert message == csv_restorer._safe_io_message(error) and "PRIVATE-" not in message


def test_security_error_is_not_downgraded_by_presentation_metadata(local_page):
    _, page, *_ = local_page
    error = RestorationSecurityError("PRIVATE-SECURITY-DETAIL",
        explanation=RestorationExplanation(RestorationReason.SOURCE_MISSING))
    page._failed(error)
    for locale in ("en", "pt-BR"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == Translator(locale).text("restoration.csv.error.security")
        assert page.status_label.property("feedbackState") == "error"
        assert "PRIVATE-" not in page.status_label.text()


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("package", [False, True])
def test_typed_failure_retranslation_is_presentation_only_and_preserves_entered_state(
    pages, monkeypatch, package, visible,
):
    app, shell, case = pages()
    page = load_csv(shell, case, package=package, indexes=(1,))
    page._failed(RestorationError("legacy", explanation=RestorationExplanation(
        RestorationReason.MISSING_MAPPING, header="<b>Literal & header</b>", row_number=9)))
    page.package_controls.password.setText("transient synthetic value")
    page.package_controls.show_password.setChecked(visible)
    shell.show()
    app.processEvents()
    page.table.setCurrentCell(1, 1)
    page.table.setFocus()
    app.processEvents()
    before = (page._inspection, page._configuration(), page.path_field.text(),
        page.package_controls.path.text(), page.package_controls.password.text(),
        page.package_controls.password.echoMode(), tuple(page._checkboxes),
        page._worker, page._last_error, page._displayed_result, page._last_output_path,
        page.progress_bar.minimum(), page.progress_bar.maximum(), page.progress_bar.value(),
        page.table.currentRow(), page.table.currentColumn(), app.focusWidget(),
        page.table.verticalScrollBar().value(), page.open_folder_button.isHidden())

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not touch files, SQLite, authentication or workers")

    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    # Existing files and crypto were used to construct the case, not while translating.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        patch.setattr(page._service, "restore", forbidden)
        patch.setattr(page._service, "analyze", forbidden)
        patch.setattr("data_mask_studio.gui.restoration_worker.restore_csv_from_package", forbidden)
        patch.setattr("data_mask_studio.transfer_package.restoration_source.read_package", forbidden)
        for translator in translators:
            shell.retranslate(translator)
            after = (page._inspection, page._configuration(), page.path_field.text(),
                page.package_controls.path.text(), page.package_controls.password.text(),
                page.package_controls.password.echoMode(), tuple(page._checkboxes),
                page._worker, page._last_error, page._displayed_result, page._last_output_path,
                page.progress_bar.minimum(), page.progress_bar.maximum(), page.progress_bar.value(),
                page.table.currentRow(), page.table.currentColumn(), app.focusWidget(),
                page.table.verticalScrollBar().value(), page.open_folder_button.isHidden())
            assert after == before
            assert page.status_label.text() == translator.text(
                "restoration.csv.error.package" if package else "restoration.csv.error.missing_mapping",
                **({} if package else {"header": "<b>Literal & header</b>", "row": 9}))
            assert page.status_label.textFormat() == Qt.TextFormat.PlainText
            assert page.package_controls.password.echoMode() == (
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)


@pytest.mark.parametrize("failure", ["password", "tamper", "binding", "publication"])
def test_real_package_worker_redacts_typed_failures_and_never_exposes_an_authentication_oracle(
    pages, monkeypatch, failure,
):
    app, shell, case = pages()
    _, config, package, _, output = case
    page = load_csv(shell, case, package=True)
    if failure == "password":
        page.package_controls.password.setText("wrong synthetic password")
    elif failure == "tamper":
        content = package.read_bytes()
        package.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    elif failure == "binding":
        config.source_path.write_bytes(config.source_path.read_bytes() + b"\n")
    else:
        def fail_publication(*args, **kwargs):
            raise PermissionError("PRIVATE-DESTINATION-DETAIL")
        monkeypatch.setattr(csv_restorer, "publish", fail_publication)
    original = config.source_path.read_bytes(), package.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Package failure must not fall back to the local environment")

    monkeypatch.setattr(page._service, "restore", forbidden)
    monkeypatch.setattr("data_mask_studio.environment.environment_lease", forbidden)
    page.start_restoration(output, overwrite=False)
    worker = finish(app, page)
    error = page._last_error
    assert type(error) is RestorationError and error.explanation is None
    assert error.__traceback__ is error.__cause__ is error.__context__ is None
    assert worker._package_request is None
    assert not output.exists() and page._last_output_path is None
    assert page.open_folder_button.isHidden() and page.progress_bar.value() == 0
    assert not page.summary.toPlainText() and not page.package_controls.password.text()
    assert not list(output.parent.glob(".*.tmp"))
    for locale in ("en", "pt-BR", "en"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == Translator(locale).text("restoration.csv.error.package")
        assert page.status_label.property("feedbackState") == "error"
        assert "PRIVATE-" not in page.status_label.text()
        assert error is page._last_error and error.explanation is None
    assert (config.source_path.read_bytes(), package.read_bytes()) == original
