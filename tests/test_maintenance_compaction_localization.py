"""Compaction presentation stays independent from engine/security decisions."""

from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
import sqlite3
from threading import Event, Thread

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QMessageBox, QTextEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.maintenance_worker import CompactionWorker
from data_mask_studio.gui.maintenance_widget import MaintenanceWidget
from data_mask_studio.i18n import Translator, load_catalogs
from data_mask_studio.integrity import AuditReport
from data_mask_studio.maintenance import CompactionResult, MaintenanceError
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.vault import VaultCipher, VaultRepository
from data_mask_studio.gui.maintenance_widget import _format_bytes, _render_compaction
from test_composite_integrity import setup as seed_composite, state as logical_state
from test_dialog_consistency import modal_reply
from test_maintenance_interface import wait
from test_navigation_localization import build_localized_window
from test_compaction_recovery import fail_after_real_replacement
from data_mask_studio.backup.snapshot import inspect_snapshot
from test_maintenance import prepare_environment, FixedKeyProvider, HMAC_KEY, VAULT_KEY


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.fixture
def page(tmp_path):
    app = create_application([])
    paths, repository, code = prepare_environment(tmp_path, bloat=True)
    widget = MaintenanceWidget(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY))
    widget.sections.setCurrentIndex(3)
    widget.resize(1000, 700)
    widget.show()
    app.processEvents()
    return app, widget, paths, repository, code


def result_for(paths):
    now = datetime.now(timezone.utc)
    audit = AuditReport(now, now, paths.vault_database_path, paths.profiles_path, 4, ())
    return CompactionResult(4096, 2048, 2048, audit)


def accept_and_pause(monkeypatch):
    # Covers both the original static dialog and explicit localized modal.
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(CompactionWorker, "start", lambda self: None)


def test_english_compaction_button_is_explicitly_localized(page):
    _, widget, _, _, _ = page
    widget.retranslate(Translator("en"))
    assert widget.compact_button.text() == "Compact vault"


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_failed_or_cancelled_retry_cannot_keep_old_success_or_finish_at_100(page, monkeypatch, terminal):
    _, widget, paths, _, _ = page
    accept_and_pause(monkeypatch)
    widget.start_compaction()
    old = widget._worker
    old.completed.emit(result_for(paths))
    old.finished.emit()
    assert widget.compaction_result.toPlainText() and widget.progress.value() == 1
    widget.start_compaction()
    worker = widget._worker
    if terminal == "failed":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    else:
        widget.cancel()
        worker.cancelled.emit()
    worker.finished.emit()
    assert widget._worker is None and widget.progress.value() == 0
    assert not widget.compaction_result.toPlainText()
    assert widget.status_label.property("feedbackState") == ("error" if terminal == "failed" else "warning")


@pytest.mark.parametrize("signal", ["phase", "completed"])
def test_retired_compaction_signal_cannot_change_new_operation(page, monkeypatch, signal):
    _, widget, paths, _, _ = page
    accept_and_pause(monkeypatch)
    widget.start_compaction()
    old = widget._worker
    old.completed.emit(result_for(paths))
    old.finished.emit()
    widget.start_compaction()
    current = widget._worker
    widget.compaction_result.clear()
    before = widget.status_label.text(), widget.cancel_button.isEnabled()
    if signal == "phase":
        old.phase_changed.emit("Retired phase must be ignored", False)
    else:
        old.completed.emit(result_for(paths))
    assert widget._worker is current
    assert (widget.status_label.text(), widget.cancel_button.isEnabled()) == before
    assert not widget.compaction_result.toPlainText()
    current.cancelled.emit()
    current.finished.emit()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_static_text_accessibility_catalog_parity_and_portuguese_global_buttons(page, locale):
    _, widget, _, _, _ = page
    translator = Translator(locale)
    widget.retranslate(translator)
    assert widget.compact_button.text() == widget.compact_button.accessibleName() == translator.text("maintenance.compaction.start")
    assert widget.compaction_info.text() == widget.compaction_info.accessibleName() == translator.text("maintenance.compaction.warning")
    assert ".dmsbackup" in widget.compaction_info.text()
    assert widget.compaction_info.property("feedbackState") == "warning"
    assert widget.compact_button.property("role") == "attention"
    assert widget.compaction_result.empty_text == widget.compaction_result.accessibleDescription() == translator.text("maintenance.compaction.empty")
    assert widget.compaction_result.accessibleName() == translator.text("maintenance.compaction.result_accessible")
    assert widget.compaction_result.isReadOnly() and not widget.compaction_result.toPlainText()
    assert widget._last_compaction is None and widget._worker is None
    assert widget.sections.currentIndex() == 3 and widget.compact_button.isEnabled()
    catalogs = load_catalogs()
    assert set(catalogs["en"]) == set(catalogs["pt-BR"])
    assert QCoreApplication.translate("QPlatformTheme", "&Yes") == "&Sim"


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("reply", ["yes", "no", "enter", "escape", "close"])
def test_real_confirmation_roles_default_escape_and_rejection_preserve_state(page, locale, reply, monkeypatch):
    app, widget, paths, _, _ = page
    translator = Translator(locale)
    widget.retranslate(translator)
    before = paths.vault_database_path.read_bytes(), paths.profiles_path.read_bytes()
    result = result_for(paths)
    widget._last_compaction = result
    widget.compaction_result.setPlainText(_render_compaction(result, translator))
    widget._set_compaction_status_id("maintenance.compaction.status.completed", state="success")
    old_feedback = widget.status_label.text(), widget.compaction_result.toPlainText()
    monkeypatch.setattr(CompactionWorker, "start", lambda self: None)
    seen = []

    def respond():
        box = app.activeModalWidget()
        seen.append(box)
        assert isinstance(box, QMessageBox)
        assert box.icon() == QMessageBox.Icon.Warning and box.textFormat() == Qt.TextFormat.PlainText
        assert box.windowTitle() == translator.text("maintenance.compaction.confirm.title")
        assert box.text() == translator.text("maintenance.compaction.confirm.text", size=_format_bytes(paths.vault_database_path.stat().st_size))
        assert box.standardButtons() == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        assert box.standardButton(box.defaultButton()) == box.standardButton(box.escapeButton()) == QMessageBox.StandardButton.No
        yes, no = box.button(QMessageBox.StandardButton.Yes), box.button(QMessageBox.StandardButton.No)
        assert box.buttonRole(yes) == QMessageBox.ButtonRole.YesRole and box.buttonRole(no) == QMessageBox.ButtonRole.NoRole
        assert yes.property("role") is None  # Original warning, not a newly restyled destructive button.
        assert yes.text() == translator.text("dialog.button.yes") and no.text() == translator.text("dialog.button.no")
        assert widget._worker is None and widget._last_compaction is result
        if reply in ("yes", "no"):
            (yes if reply == "yes" else no).click()
        elif reply == "close":
            box.close()
        else:
            QTest.keyClick(box, Qt.Key.Key_Return if reply == "enter" else Qt.Key.Key_Escape)

    with modal_reply(widget, respond):
        widget.start_compaction()
    assert len(seen) == 1
    if reply == "yes":
        worker = widget._worker
        assert worker is not None and widget._last_compaction is None and not widget.compaction_result.toPlainText()
        worker.cancelled.emit()
        worker.finished.emit()
    else:
        assert widget._worker is None and widget._last_compaction is result
        assert (widget.status_label.text(), widget.compaction_result.toPlainText()) == old_feedback
    assert (paths.vault_database_path.read_bytes(), paths.profiles_path.read_bytes()) == before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_compaction_preserves_scalar_composite_crypto_counters_profiles_and_safe_summary(page, locale, monkeypatch):
    app, widget, paths, repository, code = page
    _, composite = seed_composite(paths.directory)
    before = logical_state(repository)
    scalar = repository.get_decrypted_mapping(code)
    composite_before = repository.composite_repository().get_composite_mapping(composite.code)
    profiles = paths.profiles_path.read_bytes()
    widget.retranslate(Translator(locale))
    changes = QSignalSpy(widget.environment_changed)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.start_compaction()
    wait(app, widget)
    result = widget._last_compaction
    assert widget._worker is None and result is not None and result.audit.failure_count == 0
    assert result.size_after < result.size_before
    assert result.recovered_bytes == result.size_before - result.size_after
    assert logical_state(repository) == before
    reopened = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    assert reopened.get_decrypted_mapping(code) == scalar
    assert reopened.composite_repository().get_composite_mapping(composite.code) == composite_before
    assert paths.profiles_path.read_bytes() == profiles
    assert not paths.hmac_key_path.exists() and not paths.vault_key_path.exists()
    assert not list(paths.directory.glob("*.dmsbackup"))  # Backup remains recommended, not mandatory.
    assert not list(paths.directory.glob("dms-vault-compaction-*"))
    assert changes.count() == 1 and widget._last_audit is result.audit
    assert widget.status_label.text() == Translator(locale).text("maintenance.compaction.status.completed")
    assert widget.status_label.property("feedbackState") == "success" and widget.progress.value() == 1
    summary = widget.compaction_result.toPlainText()
    assert summary == _render_compaction(result, Translator(locale)) and len(summary.splitlines()) == 4
    for private in (str(paths.directory), code, composite.code, HMAC_KEY.hex(), VAULT_KEY.hex(), "123.456.789-00"):
        assert private not in summary
    widget.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert widget._last_compaction is result and logical_state(repository) == before and changes.count() == 1


def test_retranslate_cached_result_in_place_preserves_focus_selection_scroll_and_performs_no_io(page, monkeypatch):
    app, widget, paths, _, _ = page
    result = result_for(paths)
    accept_and_pause(monkeypatch)
    widget.start_compaction()
    worker = widget._worker
    worker.completed.emit(result)
    worker.finished.emit()
    output = widget.compaction_result
    output.setFixedHeight(50)  # Force scrolling for four real structured lines.
    output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
    output.setFixedWidth(160)
    output.setFocus()
    cursor = output.textCursor()
    cursor.setPosition(output.document().findBlockByNumber(0).position() + output.toPlainText().splitlines()[0].index("4.0"))
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)
    app.processEvents()
    output.verticalScrollBar().setValue(1)
    output.horizontalScrollBar().setValue(1)
    scroll = output.verticalScrollBar().value(), output.horizontalScrollBar().value()
    assert all(value > 0 for value in scroll)
    selected = output.textCursor().selectedText()
    assert selected == "4.0 KiB"
    identities = widget.sections.widget(3), widget.compact_button, widget.compaction_info, output
    spies = [QSignalSpy(output.textChanged), QSignalSpy(widget.environment_changed),
             QSignalSpy(widget.busy_changed), QSignalSpy(widget.sections.currentChanged)]
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation may not initiate operations or filesystem reads")

    with monkeypatch.context() as guard:
        guard.setattr(widget, "start_compaction", forbidden)
        guard.setattr(widget._compactor, "compact", forbidden)
        guard.setattr(widget._diagnostics, "run", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        guard.setattr(QMessageBox, "exec", forbidden)
        for name in ("open", "stat", "iterdir", "unlink", "rglob"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            widget.retranslate(translator)
            app.processEvents()
            assert identities == (widget.sections.widget(3), widget.compact_button, widget.compaction_info, widget.compaction_result)
            assert widget._last_compaction is result and widget._last_audit is result.audit
            assert output.toPlainText() == _render_compaction(result, translator)
            assert output.textCursor().selectedText() == selected
            assert (output.verticalScrollBar().value(), output.horizontalScrollBar().value()) == scroll
            assert app.focusWidget() is output and widget.sections.currentIndex() == 3
            assert widget.progress.value() == 1 and widget.status_label.property("feedbackState") == "success"
    assert all(spy.count() == 0 for spy in spies)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("condition", ["missing", "busy", "invalid_database"])
def test_preflight_rejection_and_safe_engine_failure_never_bypass_checks(page, locale, condition, monkeypatch):
    app, widget, paths, _, _ = page
    confirmation = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: confirmation.append(True) or QMessageBox.StandardButton.Yes)
    if condition == "busy":
        monkeypatch.setattr(widget, "_prepare_operation", lambda: False)
    else:
        # Independent path: never truncate/delete the healthy fixture's live WAL DB.
        paths = replace(paths, vault_database_path=paths.directory / "synthetic-preflight.db")
        if condition == "invalid_database":
            paths.vault_database_path.write_bytes(b"synthetic invalid SQLite file")
        widget = MaintenanceWidget(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY))
        widget.sections.setCurrentIndex(3)
    widget.retranslate(Translator(locale))
    widget.start_compaction()
    if condition == "invalid_database":
        assert confirmation == [True]
        wait(app, widget)
        assert paths.vault_database_path.read_bytes() == b"synthetic invalid SQLite file"
    else:
        assert not confirmation
        identifier = "maintenance.compaction.error.no_vault" if condition == "missing" else "maintenance.overview.error.busy"
        assert widget.status_label.text() == Translator(locale).text(identifier)
    assert widget._worker is None and widget._last_compaction is None
    assert widget.status_label.property("feedbackState") == "error" and widget.progress.value() == 0


@pytest.mark.parametrize("phase", [0, 1, 3])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_cooperative_cancellation_before_replacement_preserves_database(page, monkeypatch, phase, locale):
    app, widget, paths, repository, _ = page
    before = logical_state(repository)
    profiles = paths.profiles_path.read_bytes()
    widget.retranslate(Translator(locale))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    start = CompactionWorker.start
    reached = []

    def start_with_cancellation(worker):
        def cancel_at_phase(stage, allowed):
            reached.append((stage, allowed))
            if len(reached) - 1 == phase:
                assert allowed
                worker.request_cancel()
        worker.phase_changed.connect(cancel_at_phase, Qt.ConnectionType.DirectConnection)
        start(worker)

    monkeypatch.setattr(CompactionWorker, "start", start_with_cancellation)
    widget.start_compaction()
    wait(app, widget)
    assert len(reached) > phase
    assert widget._worker is None and widget._last_compaction is None
    assert not widget.compaction_result.toPlainText() and widget.progress.value() == 0
    assert widget.status_label.property("feedbackState") == "warning"
    assert widget.status_label.text() == Translator(locale).text("restoration.csv.status.cancelled")
    assert logical_state(repository) == before and paths.profiles_path.read_bytes() == profiles
    assert not list(paths.directory.glob("dms-vault-compaction-*"))


@pytest.mark.parametrize("allowed", [True, False])
def test_active_real_worker_raw_phase_cancel_policy_and_retranslation_are_unchanged(page, monkeypatch, allowed):
    app, widget, paths, _, _ = page
    entered, release = Event(), Event()
    calls = []
    result = result_for(paths)
    raw_phase = "Etapa livre do serviço & <literal> {count}"

    def paused(*, should_cancel, progress_callback):
        calls.append(True)
        progress_callback(raw_phase, allowed)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(widget._compactor, "compact", paused)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.start_compaction()
    worker = widget._worker
    try:
        assert entered.wait(5)
        app.processEvents()
        assert worker.isRunning() and widget.cancel_button.isEnabled() is allowed
        for locale in ("en", "pt-BR", "en"):
            widget.retranslate(Translator(locale))
            assert widget._worker is worker and worker.isRunning() and not worker._cancellation.is_set()
            assert not widget.sections.isEnabled() and widget.cancel_button.isEnabled() is allowed
            assert widget.progress.maximum() == 0 and widget.status_label.text() == raw_phase
        if allowed:
            widget.cancel()
            widget.retranslate(Translator("en"))
            assert worker._cancellation.is_set() and not widget.cancel_button.isEnabled()
            assert widget.status_label.text() == Translator("en").text("masking.status.cancelling")
    finally:
        release.set()
        assert worker.wait(10000)
        app.processEvents()
    # A completed service result remains authoritative even if cancellation was requested.
    assert widget._last_compaction is result and widget.progress.value() == 1 and calls == [True]


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_terminal_state_and_queued_old_signals_cannot_restore_success(page, monkeypatch, terminal):
    app, widget, paths, _, _ = page
    accept_and_pause(monkeypatch)
    widget.start_compaction()
    old = widget._worker
    (old.failed.emit(RuntimeError("PRIVATE_SENTINEL")) if terminal == "failed" else old.cancelled.emit())
    old.completed.emit(result_for(paths))
    old.phase_changed.emit("Late phase", False)
    assert widget._last_compaction is None and widget.progress.value() == 0
    old.finished.emit()
    widget.start_compaction()
    current = widget._worker
    before = widget.status_label.text()
    changes = QSignalSpy(widget.environment_changed)

    def late():
        old.completed.emit(result_for(paths))
        old.phase_changed.emit("Retired phase", False)
        old.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
        old.cancelled.emit()
        old.finished.emit()

    thread = Thread(target=late)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    app.processEvents()
    assert widget._worker is current and widget._last_compaction is None and changes.count() == 0
    assert widget.status_label.text() == before and widget.progress.maximum() == 0
    current.completed.emit(result_for(paths))
    current.finished.emit()
    assert widget._last_compaction is not None and changes.count() == 1 and widget.progress.value() == 1


def test_safe_freeform_errors_and_arbitrary_phase_text_are_never_parsed_as_translation_ids(page, monkeypatch):
    _, widget, _, _, _ = page
    accept_and_pause(monkeypatch)
    widget.start_compaction()
    worker = widget._worker
    message = "Falha segura: pasta dms-vault-compaction-SYNTHETIC (snapshot: vault-snapshot.db)."
    worker.failed.emit(MaintenanceError(message))
    worker.finished.emit()
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert widget.status_label.text() == message
        assert widget.status_label.property("feedbackState") == "error" and widget.progress.value() == 0


def test_hidden_compaction_feedback_result_and_other_tab_results_survive_retranslation(page, monkeypatch):
    _, widget, paths, _, _ = page
    accept_and_pause(monkeypatch)
    widget.start_compaction()
    worker = widget._worker
    result = result_for(paths)
    worker.completed.emit(result)
    worker.finished.emit()
    for tab in (0, 1, 2):
        widget.sections.setCurrentIndex(tab)
        widget._set_status("Literal feedback for another operation", False, state="warning")
        widget.progress.setRange(0, 8)
        widget.progress.setValue(3)
        widget._remember_feedback()
        saved = widget._section_feedback[tab]
        widget.retranslate(Translator("en"))
        assert widget._section_feedback[tab] == saved and widget.progress.value() == 3
        assert widget.status_label.text() == saved.message and widget.sections.currentIndex() == tab
        assert widget._last_compaction is result
        widget.sections.setCurrentIndex(3)
        assert widget.status_label.text() == Translator("en").text("maintenance.compaction.status.completed")
        assert widget.progress.value() == 1 and widget.compaction_result.toPlainText() == _render_compaction(result, Translator("en"))


def test_main_window_saved_english_starts_portuguese_and_explicit_retranslation_keeps_button_identity(tmp_path):
    create_application([])
    preferences = PreferencesService(tmp_path / "preferences.json",
        ui_languages_provider=lambda: pytest.fail("No global activation"))
    assert preferences.save_language("en").error is None
    stored = preferences.path.read_bytes()
    window = build_localized_window(tmp_path, preferences_service=preferences)
    widget = window.maintenance_widget
    button, tab = widget.compact_button, widget.sections.widget(3)
    assert button.text() == "Compactar cofre"
    window.retranslate(Translator("en"))
    assert widget.compact_button is button and widget.sections.widget(3) is tab
    assert button.text() == "Compact vault" and widget._worker is None
    assert preferences.path.read_bytes() == stored


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("rollback_fails", [False, True])
def test_real_post_replacement_failure_keeps_rollback_and_recovery_evidence_contracts(page, monkeypatch, locale, rollback_fails):
    app, widget, paths, repository, code = page
    expected = repository.get_decrypted_mapping(code)
    profiles = paths.profiles_path.read_bytes()
    sentinel = paths.directory / "other-app.tmp"
    sentinel.write_bytes(b"unrelated synthetic sentinel")
    observed, _ = fail_after_real_replacement(monkeypatch,
        OSError("PRIVATE_COMPACTION_SENTINEL"), fail_rollback=rollback_fails)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.retranslate(Translator(locale))
    changes = QSignalSpy(widget.environment_changed)
    widget.start_compaction()
    wait(app, widget)
    assert observed == {"replacement": True, "rollback": True, "snapshot_valid": True}
    assert widget._worker is None and widget._last_compaction is None
    assert not widget.compaction_result.toPlainText() and widget.progress.value() == 0
    assert widget.status_label.property("feedbackState") == "error" and changes.count() == 0
    message = widget.status_label.text()
    assert "PRIVATE_COMPACTION_SENTINEL" not in message and str(paths.directory) not in message
    evidence = list(paths.directory.glob("dms-vault-compaction-*"))
    if rollback_fails:
        assert len(evidence) == 1 and evidence[0].name in message
        snapshot = evidence[0] / "vault-snapshot.db"
        assert inspect_snapshot(snapshot) == (4, 2)
        assert VaultRepository(snapshot, VaultCipher(VAULT_KEY)).get_decrypted_mapping(code) == expected
        assert "snapshot: vault-snapshot.db" in message
    else:
        assert evidence == [] and repository.get_decrypted_mapping(code) == expected
    widget.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert widget.status_label.text() == message
    assert sentinel.read_bytes() == b"unrelated synthetic sentinel" and paths.profiles_path.read_bytes() == profiles
