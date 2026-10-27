"""Explicit backup presentation over isolated encrypted synthetic environments."""

from dataclasses import replace
from pathlib import Path
import sqlite3
import struct
from threading import Event, Thread

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.backup import (MAGIC, BackupCompatibilityError, BackupError,
    BackupValidationError, create_backup, validate_backup)
from data_mask_studio.gui.backup_widget import BackupWidget, _render_validation
from data_mask_studio.gui import backup_worker as worker_module
from data_mask_studio.gui.backup_worker import BackupCreationWorker, BackupRestoreWorker, BackupValidationWorker
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.profiles import ProfileRepository
from data_mask_studio.security import LocalKeyProvider
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from data_mask_studio.vault.composite_models import CompositeMappingCandidate
from test_backup import prepare_environment, environment_paths, FakeProtector, HMAC_KEY, VAULT_KEY
from test_backup_interface import PASSWORD, prepare_widget
from test_backup_screen_layout import validation_result
from test_batch_profile_isolation import pump_until
from test_dialog_consistency import modal_reply
from test_navigation_localization import build_localized_window


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.fixture
def backup(tmp_path):
    app = create_application([])
    widget, paths = prepare_widget(tmp_path)
    widget.destination_field.setText(str(tmp_path / "synthetic.dmsbackup"))
    widget.show()
    app.processEvents()
    return app, widget, paths


@pytest.mark.parametrize("outcome", ["failure", "cancelled"])
def test_new_validation_cannot_leave_previous_success_summary_or_progress(backup, monkeypatch, outcome):
    _, widget, _ = backup
    widget.restore_file_field.setText("synthetic.dmsbackup")
    widget.restore_password_field.setText(PASSWORD)
    widget._validation_completed(validation_result())
    assert widget.restore_progress.value() == 1 and widget.restore_summary.toPlainText()
    monkeypatch.setattr(BackupValidationWorker, "start", lambda self: None)
    widget.start_validation()
    worker = widget._worker
    if outcome == "failure":
        worker.failed.emit(RuntimeError("SYNTHETIC_PRIVATE"))
    else:
        widget.cancel()
        worker.cancelled.emit()
    worker.finished.emit()
    assert widget._worker is None
    assert not widget.restore_summary.toPlainText()
    assert widget.restore_progress.maximum() == 1 and widget.restore_progress.value() == 0
    assert not widget.restore_button.isEnabled() and widget._validated_result is None
    assert widget.restore_status.property("feedbackState") == ("error" if outcome == "failure" else "warning")


def test_retired_worker_signals_cannot_replace_current_validation(backup, monkeypatch):
    _, widget, _ = backup
    widget.restore_file_field.setText("synthetic.dmsbackup")
    widget.restore_password_field.setText(PASSWORD)
    monkeypatch.setattr(BackupValidationWorker, "start", lambda self: None)
    widget.start_validation()
    old = widget._worker
    old.completed.emit(validation_result())
    old.finished.emit()
    widget.start_validation()
    current = widget._worker
    before = widget.restore_status.text()
    old.completed.emit(validation_result())
    old.failed.emit(RuntimeError("SYNTHETIC_PRIVATE"))
    old.cancelled.emit()
    old.finished.emit()
    assert widget._worker is current
    assert widget.restore_progress.maximum() == 0
    assert widget.restore_status.text() == before
    assert not widget.restore_summary.toPlainText() and not widget.restore_button.isEnabled()
    current.cancelled.emit()
    current.finished.emit()


def finish(app, widget):
    worker = widget._worker
    assert worker is not None and worker.wait(15000)
    app.processEvents()
    assert widget._worker is None
    assert getattr(worker, "_password", "") == "" and getattr(worker, "_confirmation", "") == ""


def approve(widget, callback=None):
    app = create_application([])

    def reply():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.StandardButton.No
        assert dialog.standardButton(dialog.escapeButton()) == QMessageBox.StandardButton.No
        if callback:
            callback(dialog)
        dialog.button(QMessageBox.StandardButton.Yes).click()

    return modal_reply(widget, reply)


def logical_environment(paths, protector):
    hmac = LocalKeyProvider(paths.directory, protector, key_file_name=paths.hmac_key_path.name).get_key()
    aes = LocalKeyProvider(paths.directory, protector, key_file_name=paths.vault_key_path.name).get_key()
    repository = VaultRepository(paths.vault_database_path, VaultCipher(aes), read_only=True)
    with sqlite3.connect(paths.vault_database_path) as connection:
        codes = [row[0] for row in connection.execute(
            "SELECT code FROM vault_mappings UNION ALL SELECT code FROM composite_mappings ORDER BY code")]
    return hmac, aes, tuple(repository.get_mapping_for_inspection(code) for code in codes), (
        paths.profiles_path.read_bytes() if paths.profiles_path.exists() else None)


@pytest.fixture
def source(tmp_path):
    paths, hmac, aes, protector = prepare_environment(tmp_path / "source")
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    value = 'São José & <b>Search</b> 😀 {count}'
    code = TokenGenerator(HMAC_KEY).generate("TEXT", value)
    with repository.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(code, "TEXT", value, "Cabeçalho & Search", 3)])
    values = (value, "English-looking original")
    composite = CompositeMappingCandidate(generate_composite_token(HMAC_KEY, "PAIR", values),
        "PAIR", values, values, (NormalizationRule.EXACT, NormalizationRule.EXACT), 2)
    repository.composite_repository().upsert_composite_mapping(composite)
    return paths, hmac, aes, protector


@pytest.fixture
def archive(source, tmp_path):
    paths, hmac, aes, _ = source
    path = tmp_path / "synthetic & ação.dmsbackup"
    create_backup(path, PASSWORD, PASSWORD, paths=paths, hmac_key_provider=hmac, vault_key_provider=aes)
    return path


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_initial_controls_instructions_accessibility_and_no_secret_metadata(backup, locale):
    _, widget, _ = backup
    translator = Translator(locale)
    widget.retranslate(translator)
    assert widget.create_group.title() == widget.create_button.text() == translator.text("backup.create")
    assert widget.restore_group.title() == translator.text("backup.restore")
    for button, identifier in ((widget.choose_destination_button, "backup.choose_destination"),
            (widget.choose_restore_button, "backup.select"), (widget.restore_button, "backup.restore_action"),
            (widget.validate_backup_button, "backup.validate"), (widget.open_folder_button, "backup.open_folder")):
        assert button.text() == translator.text(identifier)
    for label, identifier in (*widget._create_labels, *widget._restore_labels):
        assert label.text() == translator.text(identifier)
    assert widget.restore_summary.empty_text == widget.restore_summary.accessibleDescription() == (
        translator.text("backup.summary.empty"))
    assert widget.recommendation_label.text() == translator.text("backup.recommendation")
    assert widget.create_status.text() == translator.text("backup.create.initial")
    assert widget.restore_status.text() == translator.text("backup.restore.initial")
    assert widget.create_status.property("feedbackState") == widget.restore_status.property("feedbackState") == "neutral"
    assert widget.create_progress.value() == widget.restore_progress.value() == 0
    assert not widget.restore_button.isEnabled() and not widget.validate_backup_button.isEnabled()
    assert widget.create_button.isEnabled() and not widget.open_folder_button.isEnabled()
    for field in (widget.create_password_field, widget.confirm_password_field, widget.restore_password_field):
        assert field.accessibleName() and not field.text() and not field.toolTip()
        assert field.echoMode() == QLineEdit.EchoMode.Password
    assert widget.destination_field.isReadOnly() and widget.restore_file_field.isReadOnly()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_native_file_dialogs_keep_paths_filters_suffixes_and_cancelled_selection(backup, locale, monkeypatch, tmp_path):
    _, widget, _ = backup
    translator = Translator(locale)
    widget.retranslate(translator)
    observed = []
    destination = tmp_path / "ação & long synthetic filename.other"

    def save(parent, title, initial, filter_):
        observed.append((title, initial, filter_))
        return str(destination), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", save)
    previous = widget.destination_field.text()
    widget.choose_destination_button.click()
    assert observed == [(translator.text("backup.dialog.save"), previous, translator.text("backup.file_filter"))]
    assert widget.destination_field.text() == str(destination.with_suffix(".dmsbackup"))
    selected = str(tmp_path / "selected ação.DMSBACKUP")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (
        observed.append(args[1:]) or (selected, "")))
    widget.choose_restore_button.click()
    assert observed[-1] == (translator.text("backup.select"), "", translator.text("backup.file_filter"))
    widget.restore_password_field.setText(PASSWORD)
    result = validation_result()
    widget._validation_completed(result)
    summary = widget.restore_summary.toPlainText()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    widget.choose_restore_button.click()
    widget.choose_destination_button.click()
    assert widget.restore_file_field.text() == selected
    assert widget.destination_field.text() == str(destination.with_suffix(".dmsbackup"))
    assert widget._validated_result is result and widget.restore_summary.toPlainText() == summary


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("password,confirmation,accepted", [
    ("", "", False), ("x" * 11, "x" * 11, False), (" " * 12, " " * 12, False),
    ("x" * 12, "x" * 12, True), (PASSWORD, PASSWORD, True), (PASSWORD, PASSWORD + "x", False),
])
def test_password_policy_and_confirmation_are_unchanged(backup, locale, password, confirmation, accepted, monkeypatch):
    _, widget, _ = backup
    widget.retranslate(Translator(locale))
    monkeypatch.setattr(BackupCreationWorker, "start", lambda self: None)
    widget.create_password_field.setText(password)
    widget.confirm_password_field.setText(confirmation)
    widget.start_creation()
    assert (widget._worker is not None) is accepted
    if accepted:
        worker = widget._worker
        assert worker._password == password and worker._confirmation == confirmation
        worker.cancelled.emit()
        worker.finished.emit()
    else:
        assert widget.create_status.property("feedbackState") == "error"
        assert "12 caracteres" in widget.create_status.text() or "não são idênticas" in widget.create_status.text()
        assert not password or password not in widget.create_status.text()


@pytest.mark.parametrize("compatible", [True, False])
@pytest.mark.parametrize("visible", [True, False])
def test_retranslate_preserves_validation_passwords_visibility_results_focus_and_connections(backup, compatible, visible, monkeypatch):
    app, widget, _ = backup
    widget.restore_file_field.setText("C:/Synthetic/ação & long backup.dmsbackup")
    for field in (widget.create_password_field, widget.confirm_password_field, widget.restore_password_field):
        field.setText(PASSWORD)
    widget.show_create_password.setChecked(visible)
    widget.show_restore_password.setChecked(visible)
    result = validation_result(compatible)
    widget._validation_completed(result)
    widget._last_backup_path = Path("C:/Synthetic/previous.dmsbackup")
    widget.open_folder_button.setEnabled(True)
    widget.restore_summary.setTextCursor(widget.restore_summary.document().find(result.application_version))
    widget.restore_password_field.setFocus()
    app.processEvents()
    before = (widget.destination_field.text(), widget.restore_file_field.text(), widget._last_backup_path,
              widget.create_progress.value(), widget.restore_progress.value(), widget.open_folder_button.isEnabled())
    field_events = [QSignalSpy(field.textChanged) for field in
                    (widget.create_password_field, widget.confirm_password_field, widget.restore_password_field)]
    report_events = QSignalSpy(widget.restore_summary.textChanged)
    busy = QSignalSpy(widget.busy_changed)
    app.clipboard().setText("Synthetic clipboard sentinel")
    for name in ("create_backup", "validate_backup", "restore_backup"):
        monkeypatch.setattr(worker_module, name, lambda *a, **k: pytest.fail("No operation during retranslation"))
    original_text = Translator.text

    def safe_text(translator, identifier, **parameters):
        assert all(PASSWORD not in str(value) for value in parameters.values())
        return original_text(translator, identifier, **parameters)

    monkeypatch.setattr(Translator, "text", safe_text)
    for locale in ("en", "en", "pt-BR", "en", "pt-BR"):
        widget.retranslate(Translator(locale))
        assert widget._displayed_validation is result
        assert widget._validated_result is (result if compatible else None)
        assert widget.restore_button.isEnabled() is compatible
        assert widget.restore_status.property("feedbackState") == ("success" if compatible else "error")
        assert widget.restore_summary.textCursor().selectedText() == result.application_version
        assert widget.restore_password_field.hasFocus()
        assert before == (widget.destination_field.text(), widget.restore_file_field.text(), widget._last_backup_path,
                          widget.create_progress.value(), widget.restore_progress.value(), widget.open_folder_button.isEnabled())
        for field in (widget.create_password_field, widget.confirm_password_field, widget.restore_password_field):
            assert field.text() == PASSWORD and not field.toolTip()
            assert field.echoMode() == (QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
            assert PASSWORD not in field.accessibleName() + field.accessibleDescription()
    assert not busy.count() and not report_events.count() and all(not event.count() for event in field_events)
    assert app.clipboard().text() == "Synthetic clipboard sentinel"
    toggles = QSignalSpy(widget.show_create_password.toggled)
    widget.show_create_password.setChecked(not visible)
    assert toggles.count() == 1
    assert all(field.text() == PASSWORD for field in (widget.create_password_field, widget.confirm_password_field))


def test_real_gui_creation_validation_and_recovery_are_equivalent_between_locales(source, tmp_path):
    paths, hmac, aes, protector = source
    app = create_application([])
    expected = logical_environment(paths, protector)
    archives = []
    validations = []
    for locale in ("en", "pt-BR"):
        widget = BackupWidget(paths, hmac, aes, protector, translator=Translator(locale))
        destination = tmp_path / f"created-{locale}.dmsbackup"
        widget.destination_field.setText(str(destination))
        widget.create_password_field.setText(PASSWORD)
        widget.confirm_password_field.setText(PASSWORD)
        widget.create_button.click()
        worker = widget._worker
        assert worker is not None and worker.wait(15000)
        # Completion is queued: retranslation must not retire the worker or enable controls.
        widget.retranslate(Translator(locale))
        assert widget._worker is worker and not widget.create_button.isEnabled()
        app.processEvents()
        assert widget._worker is None and destination.is_file()
        assert widget.create_status.property("feedbackState") == "success"
        assert widget.create_password_field.text() == widget.confirm_password_field.text() == ""
        archives.append(destination)
        widget.restore_file_field.setText(str(destination))
        widget.restore_password_field.setText(PASSWORD)
        widget.validate_backup_button.click()
        finish(app, widget)
        result = widget._validated_result
        assert result.is_compatible and widget.restore_button.isEnabled()
        assert result.mapping_count == 2 and result.profile_count == 1  # Existing scalar count contract.
        assert result.vault_schema_version == 4
        validations.append((result.application_version, result.format_version, result.vault_schema_version,
                            result.mapping_count, result.profile_count, result.vault_present, result.compatibility))
        assert logical_environment(paths, protector) == expected
    assert validations[0] == validations[1]
    assert archives[0].read_bytes() != archives[1].read_bytes()  # Independent random salt/nonce.
    for backup_path in archives:
        assert PASSWORD.encode() not in backup_path.read_bytes()
        for locale in ("en", "pt-BR"):
            target = environment_paths(tmp_path / f"restored-{backup_path.stem}-{locale}")
            target_protector = FakeProtector(locale.encode())
            receiver = BackupWidget(target,
                LocalKeyProvider(target.directory, target_protector),
                LocalKeyProvider(target.directory, target_protector, key_file_name="vault_key.dpapi"),
                target_protector, translator=Translator(locale))
            receiver.restore_file_field.setText(str(backup_path))
            receiver.restore_password_field.setText(PASSWORD)
            receiver.start_validation()
            finish(app, receiver)
            assert receiver.restore_status.property("feedbackState") == "success"
            notified = QSignalSpy(receiver.environment_restored)
            with approve(receiver):
                receiver.start_restore()
            finish(app, receiver)
            assert notified.count() == 1
            assert receiver.restore_status.property("feedbackState") == "success"
            assert receiver.restore_progress.value() == 1 and not receiver.restore_button.isEnabled()
            assert receiver.restore_password_field.text() == "" and receiver._validated_result is None
            assert logical_environment(target, target_protector) == expected


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("fault", ["password", "tampered", "invalid", "future_format", "future_app"])
def test_real_validation_failures_do_not_authorize_recovery_or_leak_secrets(backup, source, archive, locale, fault, tmp_path):
    app, widget, _ = backup
    paths, hmac, aes, _ = source
    widget.retranslate(Translator(locale))
    selected, password = archive, PASSWORD
    if fault == "password":
        password = "SYNTHETIC incorrect password"
    elif fault in {"tampered", "invalid", "future_format"}:
        selected = tmp_path / f"{fault}.dmsbackup"
        data = bytearray(archive.read_bytes())
        if fault == "tampered":
            data[-20] ^= 1
        elif fault == "future_format":
            data[len(MAGIC):len(MAGIC) + 2] = struct.pack(">H", 99)
        else:
            data = b"not a backup"
        selected.write_bytes(data)
    else:
        selected = tmp_path / "future-app.dmsbackup"
        create_backup(selected, PASSWORD, PASSWORD, paths=paths, hmac_key_provider=hmac,
                      vault_key_provider=aes, app_version="99.0.0")
    widget.restore_file_field.setText(str(selected))
    widget.restore_password_field.setText(password)
    widget._validation_completed(validation_result())
    widget.start_validation()
    finish(app, widget)
    assert widget._validated_result is None and not widget.restore_button.isEnabled()
    assert widget.restore_status.property("feedbackState") == "error"
    if fault == "future_app":
        assert widget._displayed_validation is not None and not widget._displayed_validation.is_compatible
        assert widget.restore_progress.value() == 1  # Validation finished; compatibility is explicitly error.
    else:
        assert not widget.restore_summary.toPlainText() and widget.restore_progress.value() == 0
    if fault in {"password", "tampered", "invalid"}:
        assert widget.restore_status.text() == Translator(locale).text("backup.error.validation")
    elif fault == "future_format":
        assert widget.restore_status.text() == "A versão do backup não é suportada."  # Free-form contract.
    assert password not in widget.restore_status.text() + widget.restore_summary.toPlainText()
    assert str(selected) not in widget.restore_status.text() + widget.restore_summary.toPlainText()
    widget.start_restore()
    assert widget._worker is None


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("operation", ["overwrite", "restore"])
@pytest.mark.parametrize("decision", ["no", "escape", "close"])
def test_native_destructive_confirmation_rejection_and_retranslation_preserve_semantics(backup, locale, operation, decision, tmp_path):
    app, widget, _ = backup
    translator = Translator(locale)
    widget.retranslate(translator)
    sentinel = tmp_path / "existing.dmsbackup"
    sentinel.write_bytes(b"synthetic existing backup")
    widget.destination_field.setText(str(sentinel))
    widget.create_password_field.setText(PASSWORD)
    widget.confirm_password_field.setText(PASSWORD)
    widget.restore_file_field.setText(str(sentinel))
    widget.restore_password_field.setText(PASSWORD)
    widget._validation_completed(validation_result())
    prefix = "backup.confirm.overwrite" if operation == "overwrite" else "backup.confirm.restore"

    def reject():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QMessageBox) and dialog.isModal()
        assert dialog.textFormat() == Qt.TextFormat.PlainText
        for language in (locale, "pt-BR" if locale == "en" else "en"):
            widget.retranslate(Translator(language))
            assert dialog.windowTitle() == Translator(language).text(prefix + ".title")
            assert dialog.text() == Translator(language).text(prefix + ".text")
            for button, identifier in ((QMessageBox.StandardButton.Yes, "yes"), (QMessageBox.StandardButton.No, "no")):
                assert dialog.button(button).text() == Translator(language).text("dialog.button." + identifier)
            assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.StandardButton.No
            assert dialog.standardButton(dialog.escapeButton()) == QMessageBox.StandardButton.No
            assert dialog.buttonRole(dialog.button(QMessageBox.StandardButton.Yes)) == QMessageBox.ButtonRole.YesRole
            assert dialog.button(QMessageBox.StandardButton.Yes).property("role") == "destructive"
        if decision == "no":
            dialog.button(QMessageBox.StandardButton.No).click()
        elif decision == "escape":
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        else:
            dialog.close()

    with modal_reply(widget, reject):
        (widget.start_creation if operation == "overwrite" else widget.start_restore)()
    assert widget._worker is None and sentinel.read_bytes() == b"synthetic existing backup"
    assert widget.create_password_field.text() == PASSWORD and widget.restore_password_field.text() == PASSWORD


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_real_recovery_reauthenticates_changed_archive_without_mutating_environment(backup, archive, locale):
    app, widget, paths = backup
    widget.retranslate(Translator(locale))
    before = logical_environment(paths, widget._protector)
    widget.restore_file_field.setText(str(archive))
    widget.restore_password_field.setText(PASSWORD)
    widget.start_validation()
    finish(app, widget)
    assert widget.restore_button.isEnabled()
    data = bytearray(archive.read_bytes())
    data[-20] ^= 1
    archive.write_bytes(data)
    restored = QSignalSpy(widget.environment_restored)
    with approve(widget):
        widget.start_restore()
    finish(app, widget)
    assert not restored.count() and widget._validated_result is None
    assert widget.restore_status.property("feedbackState") == "error"
    assert widget.restore_progress.value() == 0 and not widget.restore_summary.toPlainText()
    assert logical_environment(paths, widget._protector) == before
    # Existing GUI behavior retains this password on validation/recovery failure.
    assert widget.restore_password_field.text() == PASSWORD


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("kind,function", [("create", "create_backup"), ("validate", "validate_backup"), ("restore", "restore_backup")])
def test_real_workers_cancel_cooperatively_without_retranslation_side_effects(backup, archive, monkeypatch, locale, kind, function):
    app, widget, _ = backup
    widget.retranslate(Translator(locale))
    widget.create_password_field.setText(PASSWORD)
    widget.confirm_password_field.setText(PASSWORD)
    widget.restore_file_field.setText(str(archive))
    widget.restore_password_field.setText(PASSWORD)
    if kind == "restore":
        widget.start_validation()
        finish(app, widget)
    entered, proceed = Event(), Event()
    original = getattr(worker_module, function)
    calls = []

    def synchronized(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert proceed.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker_module, function, synchronized)
    try:
        if kind == "restore":
            with approve(widget):
                widget.start_restore()
        else:
            (widget.start_creation if kind == "create" else widget.start_validation)()
        worker = widget._worker
        pump_until(app, entered.is_set)
        before = (widget.create_password_field.text(), widget.confirm_password_field.text(), widget.restore_password_field.text())
        for language in ("en", "pt-BR", "en"):
            widget.retranslate(Translator(language))
            assert widget._worker is worker and worker.isRunning()
            assert not worker._cancellation.is_requested()
            assert not widget.create_button.isEnabled() and not widget.restore_button.isEnabled()
            assert before == (widget.create_password_field.text(), widget.confirm_password_field.text(), widget.restore_password_field.text())
        widget.cancel()
        assert worker._cancellation.is_requested()
        widget.retranslate(Translator(locale))
        assert not widget.create_cancel_button.isEnabled() and not widget.restore_cancel_button.isEnabled()
    finally:
        proceed.set()
    finish(app, widget)
    assert len(calls) == 1
    assert widget.create_status.property("feedbackState") == widget.restore_status.property("feedbackState") == "warning"
    assert widget.create_progress.value() == widget.restore_progress.value() == 0
    assert widget.create_cancel_button.isHidden() and widget.restore_cancel_button.isHidden()
    assert not widget.restore_button.isEnabled() and not widget._displayed_validation
    assert all(field.text() == "" for field in (widget.create_password_field, widget.confirm_password_field, widget.restore_password_field))
    assert not Path(widget.destination_field.text()).exists()
    assert not list(Path(widget.destination_field.text()).parent.glob(".dms-backup-*"))


def test_queued_retired_signals_cannot_overwrite_active_operation(backup, monkeypatch):
    app, widget, _ = backup
    widget.restore_file_field.setText("synthetic.dmsbackup")
    widget.restore_password_field.setText(PASSWORD)
    monkeypatch.setattr(BackupValidationWorker, "start", lambda self: None)
    widget.start_validation()
    old = widget._worker
    old.completed.emit(validation_result())
    old.finished.emit()
    widget.start_validation()
    current = widget._worker

    def late():
        old.completed.emit(validation_result())
        old.failed.emit(RuntimeError("SYNTHETIC_PRIVATE"))
        old.cancelled.emit()
        old.finished.emit()

    thread = Thread(target=late)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    app.processEvents()
    assert widget._worker is current and widget.restore_progress.maximum() == 0
    assert not widget.restore_button.isEnabled() and not widget.restore_summary.toPlainText()
    current.cancelled.emit()
    current.finished.emit()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_literal_service_errors_and_unknown_exception_redaction_survive_retranslation(backup, locale):
    _, widget, _ = backup
    widget.retranslate(Translator(locale))
    for error in (BackupError("Detalhe seguro de publicação & <literal>"),
                  BackupCompatibilityError("Detalhe seguro de compatibilidade")):
        widget._failed(error)
        widget.retranslate(Translator("pt-BR" if locale == "en" else "en"))
        assert widget.restore_status.text() == str(error)
        assert widget.restore_status.property("feedbackState") == "error"
        assert widget.restore_status.textFormat() == Qt.TextFormat.PlainText
    widget._failed(RuntimeError("SYNTHETIC_PRIVATE password key nonce path"))
    widget.retranslate(Translator(locale))
    assert widget.restore_status.text() == Translator(locale).text("backup.error.unexpected")
    assert "SYNTHETIC_PRIVATE" not in widget.restore_status.text()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_safe_summary_preserves_only_existing_metadata_and_literal_values(locale):
    result = replace(validation_result(False), application_version='<tag>& {count} versão sintética',
                     mapping_count=123456, profile_count=17, vault_present=False)
    text = _render_validation(result, Translator(locale))
    assert result.application_version in text and result.created_at.isoformat() in text
    assert "123456" in text and "17" in text and len(text.splitlines()) == 7
    assert Translator(locale).text("batch.file_status.incompatible") in text
    assert "vault_schema_version" not in text and "password" not in text and "nonce" not in text


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_gui_preconditions_remain_authoritative_and_output_reference_is_not_translated(backup, locale, monkeypatch, tmp_path):
    _, widget, _ = backup
    widget.retranslate(Translator(locale))
    widget.create_password_field.setText(PASSWORD)
    widget.confirm_password_field.setText(PASSWORD)
    widget.destination_field.clear()
    widget.start_creation()
    assert widget._worker is None
    assert widget.create_status.text() == Translator(locale).text("backup.error.destination")
    widget.start_validation()
    assert widget.restore_status.text() == Translator(locale).text("backup.error.select")
    widget.restore_file_field.setText(str(tmp_path / "synthetic.dmsbackup"))
    widget.restore_password_field.setText(PASSWORD)
    widget._validation_completed(validation_result())
    widget._prepare_restore = lambda: False
    monkeypatch.setattr(widget, "_confirm", lambda *args: pytest.fail("Busy environment cannot reach confirmation"))
    widget.start_restore()
    assert widget._worker is None
    assert widget.restore_status.text() == Translator(locale).text("backup.error.busy")
    from data_mask_studio.gui.backup_widget import QDesktopServices
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())))
    reference = tmp_path / "not-a-translation-id & ação.dmsbackup"
    widget._last_backup_path = reference
    widget.open_folder_button.setEnabled(True)
    widget.retranslate(Translator("pt-BR" if locale == "en" else "en"))
    widget.open_folder_button.click()
    assert opened == [reference.parent] and widget._last_backup_path is reference


def test_main_window_injects_translator_but_normal_startup_ignores_saved_english(tmp_path):
    app = create_application([])
    preferences = PreferencesService(tmp_path / "preferences.json",
                                    ui_languages_provider=lambda: pytest.fail("No global activation"))
    assert preferences.save_language("en").error is None
    before = preferences.path.read_bytes()
    window = build_localized_window(tmp_path, preferences_service=preferences)
    assert window.backup_widget.create_button.text() == "Criar backup"
    assert window.page_stack.count() == 8
    window.show()
    widget = window.backup_widget
    window.set_current_page(window.page_index(widget))
    widget.create_password_field.setText(PASSWORD)
    widget.create_password_field.setFocus()
    app.processEvents()
    window.retranslate(Translator("en"))
    assert window.backup_widget is widget and widget.create_button.text() == "Create backup"
    assert widget.create_password_field.text() == PASSWORD and widget.create_password_field.hasFocus()
    assert widget.destination_field.accessibleName() == "New backup destination"
    shell = window.page_shells[window.page_index(widget)]
    assert shell.header.title_label.text() == "Backup and recovery"
    assert shell.header.description_label.text() == Translator("en").text("navigation.description.backup")
    assert preferences.path.read_bytes() == before
