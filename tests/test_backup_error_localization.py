"""Typed backup feedback must not change security or recovery decisions."""

from dataclasses import FrozenInstanceError, asdict, replace
from io import BytesIO
from pathlib import Path
import pickle
import sqlite3
import struct
from types import SimpleNamespace
from threading import Thread

import pytest

from qt_lifecycle import qt_widget_lifecycle
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QLineEdit
from data_mask_studio.backup import (
    MAGIC, BackupCancelled, BackupCompatibilityError, BackupError, BackupExplanation,
    BackupReason, BackupValidationError, create_backup, restore_backup, validate_backup,
)
from data_mask_studio.backup import creator, restorer, snapshot, validator
from data_mask_studio.backup.crypto import validate_password, validate_scrypt_parameters, SCRYPT_PARAMETERS
from data_mask_studio.backup.format import read_prefix
from data_mask_studio.gui.backup_worker import BackupCreationWorker, BackupRestoreWorker, BackupValidationWorker
from data_mask_studio.gui.backup_error_presentation import backup_error_message_id
from data_mask_studio.i18n import Translator
from data_mask_studio.vault import VaultRepository, VaultCipher
from test_backup import prepare_environment, environment_paths, FakeProtector, HMAC_KEY, VAULT_KEY, FixedKeyProvider
from test_backup_interface import PASSWORD
from test_backup_localization import backup as backup_page, finish, approve, logical_environment
from test_maintenance_backup_localization import page as maintenance_page, finish as maintenance_finish


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.mark.parametrize("kind,expected", [
    ("short", "The backup password must contain at least 12 characters."),
    ("confirmation", "The password and confirmation do not match."),
])
def test_creation_password_errors_retranslate_without_starting_a_worker(backup_page, kind, expected):
    _, widget, _ = backup_page
    widget.create_password_field.setText("1234567" if kind == "short" else PASSWORD)
    widget.confirm_password_field.setText("mismatch")
    widget.start_creation()
    assert widget._worker is None
    widget.retranslate(Translator("en"))
    assert widget.create_status.text() == expected
    assert widget.create_status.property("feedbackState") == "error"


def test_real_unsupported_format_error_is_localized_in_backup_gui(backup_page, tmp_path):
    app, widget, _ = backup_page
    source = tmp_path / "unsupported.dmsbackup"
    source.write_bytes(struct.pack(">10sHI", MAGIC, 99, 1) + b"x")
    widget.restore_file_field.setText(str(source))
    widget.restore_password_field.setText(PASSWORD)
    widget.start_validation()
    finish(app, widget)
    widget.retranslate(Translator("en"))
    assert widget.restore_status.text() == "The backup version is not supported."
    assert not widget.restore_button.isEnabled()


def test_real_unsupported_format_error_is_localized_in_maintenance(maintenance_page, tmp_path):
    app, widget, _ = maintenance_page
    source = tmp_path / "unsupported.dmsbackup"
    source.write_bytes(struct.pack(">10sHI", MAGIC, 99, 1) + b"x")
    widget.backup_path_field.setText(str(source))
    widget.backup_password_field.setText(PASSWORD)
    widget.start_backup_validation()
    maintenance_finish(app, widget)
    widget.retranslate(Translator("en"))
    assert widget.status_label.text() == "The backup version is not supported."
    assert widget.progress.value() == 0


CASES = [
    (BackupReason.PASSWORD_TOO_SHORT, "A senha do backup deve possuir pelo menos 12 caracteres."),
    (BackupReason.PASSWORD_MISMATCH, "A senha e a confirmação não são idênticas."),
    (BackupReason.CRYPTO_PARAMETERS_INVALID, "Os parâmetros criptográficos do backup são inválidos."),
    (BackupReason.DESTINATION_EXTENSION, "Use a extensão .dmsbackup para o arquivo de backup."),
    (BackupReason.DESTINATION_FOLDER_MISSING, "A pasta de destino do backup não existe."),
    (BackupReason.DESTINATION_ALIAS, "O backup não pode substituir um arquivo do ambiente local."),
    (BackupReason.DESTINATION_EXISTS, "O arquivo de backup já existe."),
    (BackupReason.LOCAL_KEY_INVALID, "Uma chave local possui formato inválido."),
    (BackupReason.LOCAL_SCHEMA_INCOMPATIBLE, "A versão do cofre local não é compatível."),
    (BackupReason.CREATE_OUTPUT_FAILED, "Não foi possível criar o arquivo de backup."),
    (BackupReason.CREATE_FAILED, "Não foi possível criar o backup seguro."),
    (BackupReason.VAULT_MISSING, "O cofre local não foi encontrado."),
    (BackupReason.SNAPSHOT_FAILED, "Não foi possível criar o snapshot consistente do cofre."),
    (BackupReason.SNAPSHOT_CHECK_FAILED, "O snapshot do cofre não passou na verificação."),
    (BackupReason.SNAPSHOT_COUNTERS_INCONSISTENT, "O snapshot do cofre contém contagens inconsistentes."),
    (BackupReason.SNAPSHOT_INVALID, "O snapshot do cofre é inválido."),
    (BackupReason.FORMAT_UNSUPPORTED, "A versão do backup não é suportada."),
    (BackupReason.SCHEMA_INCOMPATIBLE, "O esquema do cofre não é compatível."),
    (BackupReason.APPLICATION_INCOMPATIBLE, "O backup foi criado por uma versão incompatível."),
    (BackupReason.RESTORE_REVIEW_REQUIRED, "A restauração falhou e o ambiente local exige revisão."),
    (BackupReason.RESTORE_ROLLED_BACK, "A restauração falhou e o ambiente anterior foi recuperado."),
    (BackupReason.RESTORE_FAILED, "Não foi possível restaurar o backup."),
    (BackupReason.RESTORED_KEYS_INVALID, "As chaves restauradas não puderam ser validadas."),
    (BackupReason.RESTORED_VAULT_INVALID, "O cofre restaurado não pôde ser validado."),
    (BackupReason.RESTORED_PROFILES_INVALID, "Os perfis restaurados não puderam ser validados."),
    (BackupReason.RESTORED_COMPOSITE_INVALID, "O cofre composto restaurado não pôde ser validado."),
]


def fail_at_existing_source(root, reason, monkeypatch):
    """Real domain entry points; controlled faults only at the selected guard."""
    paths, hmac, aes, protector = prepare_environment(root)
    output = root / "archive.dmsbackup"

    def create():
        return create_backup(output, PASSWORD, PASSWORD, paths=paths,
            hmac_key_provider=hmac, vault_key_provider=aes)

    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE-FAULT-DETAIL")

    if reason is BackupReason.PASSWORD_TOO_SHORT:
        validate_password("x" * 11)
    elif reason is BackupReason.PASSWORD_MISMATCH:
        validate_password(PASSWORD, "mismatch")
    elif reason is BackupReason.CRYPTO_PARAMETERS_INVALID:
        validate_scrypt_parameters(replace(SCRYPT_PARAMETERS, n=2**16))
    elif reason is BackupReason.DESTINATION_EXTENSION:
        output = root / "wrong.zip"
        create()
    elif reason is BackupReason.DESTINATION_FOLDER_MISSING:
        output = root / "missing" / "out.dmsbackup"
        create()
    elif reason is BackupReason.DESTINATION_ALIAS:
        paths = replace(paths, profiles_path=output)
        create()
    elif reason is BackupReason.DESTINATION_EXISTS:
        output.write_bytes(b"PRESERVE")
        create()
    elif reason is BackupReason.LOCAL_KEY_INVALID:
        hmac = FixedKeyProvider(b"short")
        create()
    elif reason is BackupReason.LOCAL_SCHEMA_INCOMPATIBLE:
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("PRAGMA user_version = 99")
        create()
    elif reason is BackupReason.CREATE_OUTPUT_FAILED:
        def denied(*args, **kwargs):
            raise PermissionError("PRIVATE-OUTPUT-DETAIL")
        monkeypatch.setattr(creator, "publish", denied)
        create()
    elif reason is BackupReason.CREATE_FAILED:
        monkeypatch.setattr(hmac, "get_key", fail)
        create()
    elif reason is BackupReason.VAULT_MISSING:
        snapshot.create_sqlite_snapshot(root / "absent.db", root / "copy.db")
    elif reason in (BackupReason.SNAPSHOT_FAILED, BackupReason.SNAPSHOT_INVALID):
        invalid = root / "invalid.db"
        invalid.write_bytes(b"not a SQLite database")
        if reason is BackupReason.SNAPSHOT_FAILED:
            snapshot.create_sqlite_snapshot(invalid, root / "copy.db")
        else:
            snapshot.inspect_snapshot(invalid)
    elif reason is BackupReason.SNAPSHOT_CHECK_FAILED:
        connect = snapshot.sqlite3.connect

        class FailedIntegrity:
            def __init__(self, *args, **kwargs):
                self.connection = connect(*args, **kwargs)

            def execute(self, query, *args):
                if query == "PRAGMA integrity_check":
                    return SimpleNamespace(fetchone=lambda: ("synthetic structural failure",))
                return self.connection.execute(query, *args)

            def close(self):
                self.connection.close()

        monkeypatch.setattr(snapshot.sqlite3, "connect", FailedIntegrity)
        snapshot.inspect_snapshot(paths.vault_database_path)
    elif reason is BackupReason.SNAPSHOT_COUNTERS_INCONSISTENT:
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("UPDATE vault_mappings SET total_occurrences = total_occurrences + 1")
        snapshot.inspect_snapshot(paths.vault_database_path)
    elif reason is BackupReason.FORMAT_UNSUPPORTED:
        read_prefix(BytesIO(struct.pack(">10sHI", MAGIC, 99, 1) + b"x"))
    elif reason is BackupReason.SCHEMA_INCOMPATIBLE:
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("PRAGMA user_version = 99")
        monkeypatch.setattr(creator, "SCHEMA_VERSION", 99)
        create()
        validate_backup(output, PASSWORD)
    elif reason is BackupReason.APPLICATION_INCOMPATIBLE:
        create_backup(output, PASSWORD, PASSWORD, paths=paths,
            hmac_key_provider=hmac, vault_key_provider=aes, app_version="99.0.0")
        restore_backup(output, PASSWORD, paths=environment_paths(root / "target"), protector=FakeProtector(b"NEW"))
    elif reason in (BackupReason.RESTORED_KEYS_INVALID, BackupReason.RESTORED_VAULT_INVALID,
                    BackupReason.RESTORED_PROFILES_INVALID, BackupReason.RESTORED_COMPOSITE_INVALID):
        if reason is BackupReason.RESTORED_COMPOSITE_INVALID:
            from data_mask_studio.normalization import NormalizationRule
            from data_mask_studio.processing.composite_identity import generate_composite_token
            from data_mask_studio.vault.composite_models import CompositeMappingCandidate
            repo = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
            values = ("SYNTHETIC", "SECOND")
            composite = repo.composite_repository()
            composite.upsert_composite_mapping(CompositeMappingCandidate(
                generate_composite_token(HMAC_KEY, "PAIR", values), "PAIR", values, values,
                (NormalizationRule.EXACT, NormalizationRule.EXACT)))
            monkeypatch.setattr(type(composite), "get_composite_mapping", lambda *args: None)
        restorer._verify_restored_environment(paths, protector,
            b"?" * 32 if reason is BackupReason.RESTORED_KEYS_INVALID else HMAC_KEY,
            VAULT_KEY, 2 if reason is BackupReason.RESTORED_VAULT_INVALID else 1,
            2 if reason is BackupReason.RESTORED_PROFILES_INVALID else 1)
    else:
        create()
        target, _, _, target_protector = prepare_environment(root / "target")
        if reason is BackupReason.RESTORE_FAILED:
            monkeypatch.setattr(target_protector, "protect", fail)
        else:
            def failed_swap(*args):
                raise OSError("PRIVATE-SWAP-DETAIL")
            monkeypatch.setattr(restorer, "_replace_file", failed_swap)
            if reason is BackupReason.RESTORE_REVIEW_REQUIRED:
                recover = restorer.recover_restore
                attempts = 0

                def interrupted_recovery(*args):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 2:
                        raise RuntimeError("PRIVATE-ROLLBACK-DETAIL")
                    return recover(*args)

                monkeypatch.setattr(restorer, "recover_restore", interrupted_recovery)
        restore_backup(output, PASSWORD, paths=target, protector=target_protector)
    pytest.fail("Expected the original failure branch")


@pytest.mark.parametrize("reason,legacy", CASES, ids=[reason.value for reason, _ in CASES])
def test_source_reasons_preserve_legacy_classes_messages_and_safe_parameters(
    backup_page, tmp_path, monkeypatch, reason, legacy,
):
    _, widget, _ = backup_page
    root = tmp_path / "source-case"
    with pytest.raises(BackupError) as raised:
        fail_at_existing_source(root, reason, monkeypatch)
    error = raised.value
    compatible = reason in (BackupReason.FORMAT_UNSUPPORTED, BackupReason.SCHEMA_INCOMPATIBLE,
                            BackupReason.APPLICATION_INCOMPATIBLE)
    assert type(error) is (BackupCompatibilityError if compatible else BackupError)
    assert error.args == (legacy,) and str(error) == legacy
    assert asdict(error.explanation) == {"reason": reason}
    assert not hasattr(error.explanation, "__dict__")
    widget._failed(error)
    for locale in ("pt-BR", "en", "pt-BR"):
        widget.retranslate(Translator(locale))
        assert widget.restore_status.text() == Translator(locale).text("backup.error." + reason.value)
        assert widget.restore_status.property("feedbackState") == "error"
        assert "PRIVATE-" not in widget.restore_status.text() and PASSWORD not in widget.restore_status.text()
    assert not list(root.glob(".*.tmp"))


@pytest.mark.parametrize("error_type", [BackupError, BackupCompatibilityError, BackupValidationError, BackupCancelled])
def test_metadata_is_optional_frozen_and_preserves_exception_round_trip(error_type):
    legacy = error_type("safe", 7)
    assert legacy.args == ("safe", 7) and legacy.explanation is None
    assert str(legacy) == str(RuntimeError("safe", 7))
    explanation = BackupExplanation(BackupReason.CREATE_OUTPUT_FAILED)
    with pytest.raises(FrozenInstanceError):
        explanation.reason = BackupReason.RESTORE_FAILED
    error = error_type("safe", explanation=explanation)
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is error_type and restored.args == error.args
    assert restored.explanation == explanation


@pytest.mark.parametrize("explanation", [None, "unclassified", BackupExplanation("future_reason")])
def test_unknown_reason_keeps_literal_fallback_without_inspecting_the_message(backup_page, explanation):
    _, widget, _ = backup_page
    # Even a known legacy string must not acquire an inferred reason.
    error = BackupError("A versão do backup não é suportada.", explanation=explanation)
    assert backup_error_message_id(error) is None
    widget._failed(error)
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert widget.restore_status.text() == str(error)
        assert widget.restore_status not in widget._status_messages


@pytest.mark.parametrize("worker_type", [BackupCreationWorker, BackupValidationWorker, BackupRestoreWorker])
def test_authentication_error_is_always_generic_even_with_optional_metadata(backup_page, monkeypatch, worker_type):
    _, widget, _ = backup_page
    # Use the actual worker type to exercise the GUI's existing target selection.
    if worker_type is BackupCreationWorker:
        widget.create_password_field.setText(PASSWORD)
        widget.confirm_password_field.setText(PASSWORD)
        monkeypatch.setattr(worker_type, "start", lambda _: None)
        widget.start_creation()
    elif worker_type is BackupValidationWorker:
        widget.restore_file_field.setText("synthetic.dmsbackup")
        widget.restore_password_field.setText(PASSWORD)
        monkeypatch.setattr(BackupValidationWorker, "start", lambda _: None)
        widget.start_validation()
    else:
        worker = BackupRestoreWorker("synthetic.dmsbackup", PASSWORD, widget._paths, widget._protector)
        widget._begin(worker, creating=False)
        worker.failed.connect(widget._failed)
        worker.finished.connect(widget._finished)
    worker = widget._worker
    error = BackupValidationError("PRIVATE-AUTHENTICATION-DETAIL", explanation=BackupExplanation(
        BackupReason.LOCAL_KEY_INVALID))
    worker.failed.emit(error)
    worker.finished.emit()
    target = widget.create_status if worker_type is BackupCreationWorker else widget.restore_status
    for locale in ("en", "pt-BR"):
        widget.retranslate(Translator(locale))
        assert target.text() == Translator(locale).text("backup.error.validation")
        assert target.property("feedbackState") == "error" and "PRIVATE" not in target.text()


@pytest.mark.parametrize("screen", ["backup", "maintenance"])
@pytest.mark.parametrize("visible", [False, True])
def test_classified_error_retranslation_preserves_state_without_io_or_worker_execution(
    request, monkeypatch, screen, visible,
):
    app, widget, _ = request.getfixturevalue("backup_page" if screen == "backup" else "maintenance_page")
    monkeypatch.setattr(BackupValidationWorker, "start", lambda _: None)
    path = widget.restore_file_field if screen == "backup" else widget.backup_path_field
    password = widget.restore_password_field if screen == "backup" else widget.backup_password_field
    show = widget.show_restore_password if screen == "backup" else widget.show_backup_password
    start = widget.start_validation if screen == "backup" else widget.start_backup_validation
    path.setText("C:/Synthetic/backup & ação.dmsbackup")
    password.setText(PASSWORD)
    start()
    worker = widget._worker
    worker.failed.emit(BackupCompatibilityError("legacy", explanation=BackupExplanation(
        BackupReason.SCHEMA_INCOMPATIBLE)))
    worker.finished.emit()
    # Existing maintenance lifecycle permits a new transient password after failure.
    password.setText(PASSWORD)
    show.setChecked(visible)
    password.setFocus()
    app.processEvents()
    label = widget.restore_status if screen == "backup" else widget.status_label
    progress = widget.restore_progress if screen == "backup" else widget.progress
    result = widget.restore_summary if screen == "backup" else widget.backup_result
    # In the backup page an actual password edit invalidates feedback by design;
    # apply the same classified terminal error after setting credentials.
    if screen == "backup":
        widget._failed(BackupCompatibilityError("legacy", explanation=BackupExplanation(
            BackupReason.SCHEMA_INCOMPATIBLE)))
    cursor = result.textCursor()
    end = result.document().characterCount() - 1
    cursor.setPosition(max(0, end - 4))
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    result.setTextCursor(cursor)

    def cursor_offsets():
        # Existing summaries preserve positions relative to each block's end,
        # so translated labels with different lengths keep the same context.
        cursor = result.textCursor()
        positions = []
        for position in (cursor.anchor(), cursor.position()):
            cursor.setPosition(position)
            block = cursor.block()
            positions.append((block.blockNumber(), block.length() - 1 - cursor.positionInBlock()))
        return tuple(positions)

    def state():
        return (widget._worker, path.text(), password.text(), password.echoMode(),
            progress.minimum(), progress.maximum(), progress.value(), app.focusWidget(),
            result.verticalScrollBar().value(), result.horizontalScrollBar().value(),
            cursor_offsets(),
            label.property("feedbackState"))

    before = state()
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not access files, credentials or backup services")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        for operation in ("create_backup", "validate_backup", "restore_backup"):
            patch.setattr("data_mask_studio.gui.backup_worker." + operation, forbidden)
        patch.setattr(BackupValidationWorker, "start", forbidden)
        for translator in translators:
            widget.retranslate(translator)
            assert state() == before
            assert label.text() == translator.text("backup.error.schema_incompatible")
            assert password.echoMode() == (QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)


@pytest.mark.parametrize("screen", ["backup", "maintenance"])
def test_queued_retired_typed_failure_cannot_replace_new_operation(request, monkeypatch, screen):
    app, widget, _ = request.getfixturevalue("backup_page" if screen == "backup" else "maintenance_page")
    monkeypatch.setattr(BackupValidationWorker, "start", lambda _: None)
    path = widget.restore_file_field if screen == "backup" else widget.backup_path_field
    password = widget.restore_password_field if screen == "backup" else widget.backup_password_field
    start = widget.start_validation if screen == "backup" else widget.start_backup_validation
    path.setText("synthetic.dmsbackup")
    password.setText(PASSWORD)
    start()
    old = widget._worker
    old.cancelled.emit()
    old.finished.emit()
    password.setText(PASSWORD)
    start()
    current = widget._worker
    label = widget.restore_status if screen == "backup" else widget.status_label
    widget.retranslate(Translator("en"))
    before = label.text()

    def late():
        old.failed.emit(BackupCompatibilityError("old error", explanation=BackupExplanation(
            BackupReason.FORMAT_UNSUPPORTED)))
        old.finished.emit()

    thread = Thread(target=late)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    app.processEvents()
    assert widget._worker is current and label.text() == before
    current.cancelled.emit()
    current.finished.emit()
