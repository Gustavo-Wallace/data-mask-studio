"""Maintenance backup validation: explicit localization and unchanged authentication."""

from dataclasses import replace
from pathlib import Path
import sqlite3
import struct
from threading import Event, Thread

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QFileDialog, QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.backup import MAGIC, BackupCompatibilityError, BackupError, create_backup, validate_backup
from data_mask_studio.gui import backup_worker as worker_module
from data_mask_studio.gui.backup_worker import BackupValidationWorker
from data_mask_studio.gui.maintenance_widget import _render_backup
from data_mask_studio.i18n import Translator
from data_mask_studio.maintenance import safe_diagnostic_report
from data_mask_studio.preferences import PreferencesService
from test_backup import prepare_environment, PASSWORD
from test_backup_screen_layout import validation_result
from test_batch_profile_isolation import pump_until
from test_maintenance_interface import prepare, widget_for, wait
from test_navigation_localization import build_localized_window


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.fixture
def page(tmp_path):
    app = create_application([])
    paths, _ = prepare(tmp_path)
    widget = widget_for(paths)
    widget.sections.setCurrentIndex(1)
    widget.resize(1000, 700)
    widget.show()
    app.processEvents()
    return app, widget, paths


@pytest.mark.parametrize("outcome", ["failure", "cancelled"])
def test_new_validation_does_not_retain_old_success_or_report_completion_after_failure(page, monkeypatch, outcome):
    _, widget, _ = page
    widget.backup_path_field.setText("synthetic.dmsbackup")
    widget.backup_password_field.setText("synthetic validation password")
    monkeypatch.setattr(BackupValidationWorker, "start", lambda self: None)
    widget.start_backup_validation()
    old = widget._worker
    old.completed.emit(validation_result())
    old.finished.emit()
    previous = widget.backup_result.toPlainText()
    assert previous and widget.progress.value() == 1
    widget.backup_password_field.setText("synthetic validation password")
    widget.start_backup_validation()
    worker = widget._worker
    if outcome == "failure":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    else:
        widget.cancel()
        worker.cancelled.emit()
    worker.finished.emit()
    assert widget._worker is None
    assert widget.progress.value() == 0
    assert widget.backup_result.toPlainText() != previous
    assert "Arquivo válido: sim" not in widget.backup_result.toPlainText()


def test_retired_backup_worker_cannot_publish_into_another_operation(page, monkeypatch):
    _, widget, _ = page
    widget.backup_path_field.setText("synthetic.dmsbackup")
    widget.backup_password_field.setText("synthetic validation password")
    monkeypatch.setattr(BackupValidationWorker, "start", lambda self: None)
    widget.start_backup_validation()
    old = widget._worker
    old.completed.emit(validation_result())
    old.finished.emit()
    widget.backup_password_field.setText("synthetic validation password")
    widget.start_backup_validation()
    current = widget._worker
    widget.backup_result.clear()
    old.completed.emit(validation_result())
    assert widget._worker is current and not widget.backup_result.toPlainText()
    assert widget.progress.maximum() == 0
    current.cancelled.emit()
    current.finished.emit()


@pytest.fixture
def archive(tmp_path):
    paths, hmac, aes, _ = prepare_environment(tmp_path / "source")
    destination = tmp_path / "synthetic backup ação & #.dmsbackup"
    create_backup(destination, PASSWORD, PASSWORD, paths=paths, hmac_key_provider=hmac, vault_key_provider=aes)
    return destination, paths, hmac, aes


def finish(app, widget):
    worker = widget._worker
    assert worker is not None and worker.wait(10000)
    app.processEvents()
    assert widget._worker is None and not widget._backup_accept_result
    assert worker._password == ""


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_initial_tab_labels_accessibility_and_masking(page, locale):
    _, widget, _ = page
    translator = Translator(locale)
    widget.retranslate(translator)
    assert widget.sections.tabText(1) == widget.validate_backup_button.text() == translator.text("backup.validate")
    assert widget.choose_backup_button.text() == translator.text("maintenance.backup.select")
    assert widget.backup_file_label.text() == translator.text("maintenance.backup.file_label")
    assert widget.backup_password_label.text() == translator.text("masking.package.password_label")
    assert widget.show_backup_password.text() == translator.text("restoration.package.show_password")
    assert widget.backup_result.empty_text == widget.backup_result.accessibleDescription() == (
        translator.text("maintenance.backup.empty"))
    assert widget.backup_path_field.accessibleName() == translator.text("maintenance.backup.path_accessible")
    assert widget.backup_password_field.accessibleName() == translator.text("maintenance.backup.password_accessible")
    assert widget.backup_result.accessibleName() == translator.text("maintenance.backup.result_accessible")
    assert not widget.backup_result.toPlainText() and widget.backup_result.isReadOnly()
    assert not widget.status_label.isVisible()  # No operation feedback invented for the initial tab.
    assert widget.backup_path_field.isReadOnly() and not widget.backup_password_field.text()
    assert widget.backup_password_field.echoMode() == QLineEdit.EchoMode.Password
    assert not widget.show_backup_password.isChecked() and not widget.backup_password_field.toolTip()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_file_dialog_filter_extension_cancel_and_replacement_states(page, archive, monkeypatch, locale, tmp_path):
    app, widget, _ = page
    destination, _, _, _ = archive
    translator = Translator(locale)
    widget.retranslate(translator)
    dialogs = []

    def choose(parent, title, initial, filter_):
        dialogs.append((title, initial, filter_))
        return str(destination), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    widget.choose_backup_button.click()
    assert dialogs == [(translator.text("backup.select"), "", "Backup (*.dmsbackup)")]
    assert widget.backup_path_field.text() == str(destination)
    assert widget.status_label.text() == translator.text("backup.status.validation_pending")
    assert widget.status_label.property("feedbackState") == "neutral" and widget.progress.value() == 0
    widget.backup_password_field.setText(PASSWORD)
    widget.validate_backup_button.click()
    finish(app, widget)
    result = widget._last_backup_validation
    report = widget.backup_result.toPlainText()
    status = widget.status_label.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    widget.choose_backup_button.click()
    assert widget._last_backup_validation is result and widget.backup_result.toPlainText() == report
    assert widget.status_label.text() == status and widget.progress.value() == 1
    replacement = str(tmp_path / "different.DMSBACKUP")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (replacement, ""))
    widget.choose_backup_button.click()
    assert widget.backup_path_field.text() == replacement  # No renaming or extension rewriting.
    assert widget._last_backup_validation is None and not widget.backup_result.toPlainText()
    assert widget.progress.value() == 0 and widget.status_label.property("feedbackState") == "neutral"


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("kind", ["valid", "empty_password", "wrong_password", "tampered", "invalid", "future_format", "future_app"])
def test_real_validation_decisions_content_and_no_environment_mutation(page, archive, monkeypatch, locale, kind, tmp_path):
    app, widget, paths = page
    destination, source, hmac, aes = archive
    widget.retranslate(Translator(locale))
    selected, password = destination, PASSWORD
    if kind == "empty_password":
        password = ""
    elif kind == "wrong_password":
        password = "synthetic incorrect password"
    elif kind in {"tampered", "invalid", "future_format"}:
        selected = tmp_path / f"{kind}.dmsbackup"
        data = bytearray(destination.read_bytes())
        if kind == "tampered":
            data[-20] ^= 1
        elif kind == "invalid":
            data = b"not a backup"
        else:
            data[len(MAGIC):len(MAGIC) + 2] = struct.pack(">H", 99)
        selected.write_bytes(data)
    elif kind == "future_app":
        selected = tmp_path / "future-application.dmsbackup"
        create_backup(selected, PASSWORD, PASSWORD, paths=source, hmac_key_provider=hmac,
                      vault_key_provider=aes, app_version="99.0.0")
    observed = (selected, source.vault_database_path, source.profiles_path, source.hmac_key_path,
                source.vault_key_path, paths.vault_database_path)
    before = {path: path.read_bytes() for path in observed}
    for name in ("restore_backup", "create_backup"):
        monkeypatch.setattr(worker_module, name, lambda *a, **k: pytest.fail("Validation must never restore or create"))
    monkeypatch.setattr(widget._diagnostics, "run", lambda **k: pytest.fail("Validation must not rerun diagnostics"))
    changed = QSignalSpy(widget.environment_changed)
    widget.backup_path_field.setText(str(selected))
    widget.backup_password_field.setText(password)
    widget.start_backup_validation()
    finish(app, widget)
    if kind in {"valid", "future_app"}:
        expected = validate_backup(selected, PASSWORD)
        assert widget._last_backup_validation == expected
        assert expected.is_compatible is (kind == "valid")
        assert widget.backup_result.toPlainText() == _render_backup(expected, Translator(locale))
        assert widget.progress.value() == 1  # Finished validation is not a claim of compatibility.
        assert widget.status_label.property("feedbackState") == ("success" if expected.is_compatible else "warning")
        identifier = "backup.status.validated" if expected.is_compatible else "backup.status.incompatible"
        assert widget.status_label.text() == Translator(locale).text(identifier)
    else:
        assert widget._last_backup_validation is None and widget.progress.value() == 0
        assert widget.status_label.property("feedbackState") == "error"
        assert widget.backup_result.toPlainText() == Translator(locale).text("maintenance.backup.result.invalid")
        if kind == "future_format":
            assert widget.status_label.text() == "A versão do backup não é suportada."
        else:
            assert widget.status_label.text() == Translator(locale).text("backup.error.validation")
    assert not changed.count()
    assert {path: path.read_bytes() for path in observed} == before
    assert widget.backup_password_field.text() == "" and not widget.show_backup_password.isChecked()
    visible = widget.backup_result.toPlainText() + widget.status_label.text()
    for secret in (PASSWORD, "Valor protegido", str(selected), str(source.directory), (b"H" * 32).hex(), (b"V" * 32).hex()):
        assert secret not in visible


@pytest.mark.parametrize("visible", [False, True])
def test_completed_retranslation_preserves_same_data_selection_focus_and_does_not_access_files(page, archive, monkeypatch, visible):
    app, widget, _ = page
    destination, _, _, _ = archive
    widget.backup_path_field.setText(str(destination))
    widget.backup_password_field.setText(PASSWORD)
    widget.start_backup_validation()
    finish(app, widget)
    result = widget._last_backup_validation
    widget.backup_password_field.setText("legitimate transient synthetic password")
    widget.show_backup_password.setChecked(visible)
    widget.backup_result.setTextCursor(widget.backup_result.document().find(result.application_version))
    widget.backup_password_field.setFocus()
    app.processEvents()
    identities = (widget.backup_path_field, widget.backup_password_field, widget.backup_result,
                  widget.sections.widget(1), widget.validate_backup_button)
    position = widget.backup_result.verticalScrollBar().value()
    text_events, tab_events, busy = (QSignalSpy(widget.backup_result.textChanged),
                                    QSignalSpy(widget.sections.currentChanged), QSignalSpy(widget.busy_changed))
    password_events = QSignalSpy(widget.backup_password_field.textChanged)
    app.clipboard().setText("Synthetic clipboard sentinel")
    translators = [Translator(locale) for locale in ("en", "en", "pt-BR", "en", "pt-BR")]

    def forbidden(*args, **kwargs):
        pytest.fail("Presentation must not perform backup, environment or filesystem operations")

    with monkeypatch.context() as patch:
        for name in ("validate_backup", "restore_backup", "create_backup"):
            patch.setattr(worker_module, name, forbidden)
        patch.setattr(widget._diagnostics, "run", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        for name in ("open", "stat", "iterdir", "rglob"):
            patch.setattr(Path, name, forbidden)
        for translator in translators:
            widget.retranslate(translator)
            assert widget._last_backup_validation is result
            assert widget.backup_result.toPlainText() == _render_backup(result, translator)
            assert widget.backup_result.textCursor().selectedText() == result.application_version
            assert widget.backup_result.verticalScrollBar().value() == position
            assert widget.backup_path_field.text() == str(destination) and widget.sections.currentIndex() == 1
            assert widget.backup_password_field.text() == "legitimate transient synthetic password"
            assert widget.backup_password_field.hasFocus() and not widget.backup_password_field.toolTip()
            assert widget.backup_password_field.echoMode() == (QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
            assert widget.status_label.property("feedbackState") == "success" and widget.progress.value() == 1
            assert identities == (widget.backup_path_field, widget.backup_password_field, widget.backup_result,
                                  widget.sections.widget(1), widget.validate_backup_button)
    assert not text_events.count() and not password_events.count() and not tab_events.count() and not busy.count()
    assert app.clipboard().text() == "Synthetic clipboard sentinel"


def test_english_and_portuguese_real_validation_return_identical_structured_results(page, archive):
    app, widget, _ = page
    destination, _, _, _ = archive
    results = []
    for locale in ("en", "pt-BR"):
        widget.retranslate(Translator(locale))
        widget.backup_path_field.setText(str(destination))
        widget.backup_password_field.setText(PASSWORD)
        widget.start_backup_validation()
        finish(app, widget)
        results.append(widget._last_backup_validation)
    assert results[0] == results[1] == validate_backup(destination, PASSWORD)


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("cancel", [False, True])
def test_active_real_worker_and_queued_completion_preserve_ownership_during_retranslation(page, archive, monkeypatch, locale, cancel):
    app, widget, _ = page
    destination, _, _, _ = archive
    widget.retranslate(Translator(locale))
    entered, proceed = Event(), Event()
    original = worker_module.validate_backup
    calls = []

    def synchronized(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert proceed.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker_module, "validate_backup", synchronized)
    widget.backup_path_field.setText(str(destination))
    widget.backup_password_field.setText(PASSWORD)
    widget.show_backup_password.setChecked(True)
    widget.start_backup_validation()
    worker = widget._worker
    try:
        pump_until(app, entered.is_set)
        for language in ("en", "pt-BR", locale):
            widget.retranslate(Translator(language))
            assert widget._worker is worker and worker.isRunning() and widget._backup_accept_result
            assert not worker.cancellation_requested and widget.progress.maximum() == 0
            assert not widget.sections.isEnabled() and widget.cancel_button.isVisible()
            assert widget.backup_password_field.text() == PASSWORD
            assert widget.backup_password_field.echoMode() == QLineEdit.EchoMode.Normal
        if cancel:
            widget.cancel_button.click()
            widget.retranslate(Translator(locale))
            assert worker.cancellation_requested and not widget.cancel_button.isEnabled()
    finally:
        proceed.set()
    assert worker.wait(10000)
    widget.retranslate(Translator(locale))
    assert widget._worker is worker and not widget.sections.isEnabled()  # Terminal signals are still queued.
    app.processEvents()
    assert widget._worker is None and widget.sections.isEnabled() and len(calls) == 1
    assert widget._last_backup_validation is not None if not cancel else widget._last_backup_validation is None
    assert widget.progress.value() == (0 if cancel else 1)
    assert widget.status_label.property("feedbackState") == ("warning" if cancel else "success")
    assert worker._password == "" and not widget.backup_password_field.text()
    assert not widget.show_backup_password.isChecked() and widget.cancel_button.isHidden()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_cancel_after_result_delivery_and_late_signals_cannot_revive_it(page, monkeypatch, locale):
    app, widget, _ = page
    widget.retranslate(Translator(locale))
    widget.backup_path_field.setText("synthetic.dmsbackup")
    monkeypatch.setattr(BackupValidationWorker, "start", lambda self: None)
    widget.start_backup_validation()
    old = widget._worker
    old.completed.emit(validation_result())
    assert widget._last_backup_validation is not None
    widget.cancel()
    widget.retranslate(Translator(locale))
    old.completed.emit(validation_result())
    old.finished.emit()
    assert widget.progress.value() == 0 and widget._last_backup_validation is None
    assert widget.status_label.property("feedbackState") == "warning"
    widget.start_backup_validation()
    current = widget._worker

    def late():
        old.completed.emit(validation_result())
        old.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
        old.cancelled.emit()
        old.finished.emit()

    thread = Thread(target=late)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    app.processEvents()
    assert widget._worker is current and widget.progress.maximum() == 0
    assert widget._last_backup_validation is None and not widget.backup_result.toPlainText()
    current.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    current.completed.emit(validation_result())
    current.finished.emit()
    widget.retranslate(Translator(locale))
    assert widget._last_backup_validation is None and widget.progress.value() == 0
    assert widget.status_label.property("feedbackState") == "error"


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_missing_input_and_freeform_service_errors_remain_safe_and_unparsed(page, monkeypatch, locale):
    _, widget, _ = page
    widget.retranslate(Translator(locale))
    widget.start_backup_validation()
    assert widget._worker is None
    assert widget.status_label.text() == Translator(locale).text("maintenance.backup.error.input")
    widget.backup_path_field.setText("synthetic.dmsbackup")
    monkeypatch.setattr(BackupValidationWorker, "start", lambda self: None)
    for error in (BackupCompatibilityError("Motivo seguro de incompatibilidade & <literal>"),
                  BackupError("Detalhe seguro do serviço {count}")):
        widget.start_backup_validation()
        worker = widget._worker
        worker.failed.emit(error)
        worker.finished.emit()
        for language in ("en", "pt-BR", locale):
            widget.retranslate(Translator(language))
            assert widget.status_label.text() == str(error)
            assert widget.status_label.textFormat() == Qt.TextFormat.PlainText
            assert widget.status_label.property("feedbackState") == "error" and widget.progress.value() == 0


def test_backup_state_and_05j1_report_survive_other_tabs_without_reinterpreting_freeform_feedback(page, archive):
    app, widget, _ = page
    destination, _, _, _ = archive
    widget.sections.setCurrentIndex(0)
    widget.start_diagnostic()
    wait(app, widget)
    diagnostic = widget._last_diagnostic
    canonical = safe_diagnostic_report(diagnostic)
    widget.sections.setCurrentIndex(1)
    widget.backup_path_field.setText(str(destination))
    widget.backup_password_field.setText(PASSWORD)
    widget.start_backup_validation()
    finish(app, widget)
    result = widget._last_backup_validation
    for index in (2, 3):
        widget.sections.setCurrentIndex(index)
        widget._set_status("Feedback literal da aba reservada", False, state="warning")
        widget.progress.setRange(0, 7)
        widget.progress.setValue(3)
        widget._remember_feedback()
        snapshot = widget._section_feedback[index]
        for language in ("en", "pt-BR", "en"):
            widget.retranslate(Translator(language))
            assert widget.sections.currentIndex() == index and widget._section_feedback[index] == snapshot
            assert widget.progress.value() == 3 and widget.progress.maximum() == 7
            assert widget.status_label.text() == snapshot.message
            assert widget.compact_button.text() == Translator(language).text("maintenance.compaction.start")
            assert widget.compaction_result.empty_text == Translator(language).text("maintenance.compaction.empty")
        widget.sections.setCurrentIndex(1)
        assert widget._last_backup_validation is result and widget.progress.value() == 1
        assert widget.status_label.text() == Translator("en").text("backup.status.validated")
    assert widget._last_diagnostic is diagnostic and widget._last_audit is diagnostic.audit
    widget.sections.setCurrentIndex(0)
    widget.copy_report_button.click()
    assert app.clipboard().text() == canonical


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_summary_fields_and_untrusted_metadata_are_plain_and_unchanged(locale):
    result = replace(validation_result(False), application_version="backup.create & <b>ação</b> {count}")
    text = _render_backup(result, Translator(locale))
    assert result.application_version in text and len(text.splitlines()) == 8
    assert result.created_at.astimezone().isoformat(timespec="seconds") in text
    assert Translator(locale).text("maintenance.backup.summary.compatible_no") in text


def test_main_window_explicit_translation_and_portuguese_startup_with_saved_english(tmp_path):
    create_application([])
    preferences = PreferencesService(tmp_path / "preferences.json",
                                     ui_languages_provider=lambda: pytest.fail("No global language activation"))
    assert preferences.save_language("en").error is None
    before = preferences.path.read_bytes()
    window = build_localized_window(tmp_path, preferences_service=preferences)
    widget = window.maintenance_widget
    assert widget.validate_backup_button.text() == "Validar backup"
    assert widget.backup_file_label.text() == "Arquivo de backup:"
    identity = widget.sections.widget(1)
    window.retranslate(Translator("en"))
    assert widget.sections.widget(1) is identity
    assert widget.validate_backup_button.text() == "Validate backup"
    assert widget.backup_path_field.accessibleName() == "Backup file for validation"
    assert preferences.path.read_bytes() == before
