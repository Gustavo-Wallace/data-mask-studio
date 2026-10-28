"""Overview-only localization must not rerun or weaken authoritative diagnostics."""

from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLineEdit, QTextEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.maintenance_widget import MaintenanceWidget, _render_diagnostic
from data_mask_studio.gui.maintenance_worker import DiagnosticWorker
from data_mask_studio.i18n import Translator
from data_mask_studio.integrity import AuditReport, CheckResult, IntegrityStatus
from data_mask_studio.maintenance import MaintenanceError, MaintenanceStatus, safe_diagnostic_report
from data_mask_studio.preferences import PreferencesService
from test_composite_integrity import setup as seed_composite, CANONICAL, ORIGINAL
from test_maintenance import prepare_environment, FixedKeyProvider, HMAC_KEY, VAULT_KEY, SECRET_VALUE, CANONICAL_VALUE
from test_maintenance_interface import wait
from test_navigation_localization import build_localized_window


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.fixture
def overview(tmp_path):
    app = create_application([])
    paths, repository, code = prepare_environment(tmp_path)
    _, composite = seed_composite(paths.directory)
    widget = MaintenanceWidget(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY))
    widget.resize(1000, 700)
    widget.show()
    app.processEvents()
    return app, widget, paths, (code, composite.code)


def complete(app, widget):
    widget.start_diagnostic()
    wait(app, widget)
    assert widget._worker is None and widget._last_diagnostic is not None
    return widget._last_diagnostic


def assert_checks_visible(text, result, translator):
    previous = -1
    for check in result.audit.checks:
        line = translator.text("maintenance.integrity.check",
            status=translator.text(f"maintenance.integrity.{check.status.value}"),
            check_type=check.check_type, examined=check.examined, failures=check.failures, message=check.message)
        if check.identifiers:
            line += translator.text("maintenance.integrity.references", identifiers=", ".join(check.identifiers))
        position = text.index(line, previous + 1)
        assert position > previous
        previous = position
    assert translator.text("maintenance.integrity.checks") in text
    assert result.audit.started_at.astimezone().isoformat(timespec="seconds") in text
    assert result.audit.finished_at.astimezone().isoformat(timespec="seconds") in text


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_initial_structure_accessibility_and_reserved_tab_contents_remain_untouched(overview, locale):
    _, widget, _, _ = overview
    translator = Translator(locale)
    identities = tuple(widget.sections.widget(index) for index in range(4))
    other_texts = (widget.compact_button.text(), widget.compaction_result.empty_text)
    widget.retranslate(translator)
    assert [widget.sections.tabText(index) for index in range(4)] == [translator.text(identifier) for identifier in
        ("maintenance.tab.overview", "backup.validate", "maintenance.tab.temporaries", "maintenance.tab.compaction")]
    assert tuple(widget.sections.widget(index) for index in range(4)) == identities
    assert widget.refresh_button.text() == translator.text("maintenance.overview.refresh")
    assert widget.copy_report_button.text() == translator.text("maintenance.overview.copy")
    assert widget.copy_report_button.toolTip() == widget.copy_report_button.accessibleDescription() == (
        translator.text("maintenance.overview.copy_description"))
    assert widget.overview_output.empty_text == widget.overview_output.accessibleDescription() == (
        translator.text("maintenance.overview.empty"))
    assert widget.status_label.text() == translator.text("maintenance.overview.status.initial")
    assert widget.last_audit_label.text() == translator.text("maintenance.overview.audit.not_run")
    assert widget.status_label.property("feedbackState") == "neutral"
    assert widget.progress.value() == 0 and widget._last_diagnostic is None
    assert not widget.copy_report_button.isEnabled() and widget.cancel_button.isHidden()
    assert widget.overview_output.isReadOnly() and not widget.overview_output.toPlainText()
    assert widget.progress.accessibleName() == translator.text("maintenance.progress_accessible")
    assert widget.sections.accessibleName() == translator.text("maintenance.tabs_accessible")
    assert other_texts == (widget.compact_button.text(), widget.compaction_result.empty_text)


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("condition,status,state", [("healthy", MaintenanceStatus.HEALTHY, "success"),
    ("attention", MaintenanceStatus.ATTENTION, "warning"), ("scalar", MaintenanceStatus.FAILURE, "error"),
    ("composite", MaintenanceStatus.FAILURE, "error")])
def test_real_complete_diagnostics_keep_all_checks_safe_counts_and_canonical_copy(overview, monkeypatch, locale, condition, status, state):
    app, widget, paths, codes = overview
    translator = Translator(locale)
    widget.retranslate(translator)
    if condition == "attention":
        (paths.directory / ".profiles.json.ab12_cd3.tmp").write_bytes(b"synthetic DMS-owned residue")
    elif condition in {"scalar", "composite"}:
        with sqlite3.connect(paths.vault_database_path) as connection:
            if condition == "scalar":
                connection.execute("UPDATE vault_mappings SET total_occurrences=9")
            else:
                connection.execute("UPDATE composite_mappings SET nonce=X'00'")
    before = paths.vault_database_path.read_bytes()
    monkeypatch.setattr(AuditReport, "to_safe_text", lambda _: pytest.fail("Unsafe path-bearing report must never be used"))
    app.clipboard().setText("Synthetic clipboard sentinel")
    result = complete(app, widget)
    assert result.status is status
    assert widget.status_label.property("feedbackState") == state
    assert widget.status_label.text() == translator.text("maintenance.overview.status.completed",
                                                       status=translator.text(f"maintenance.status.{status.value}"))
    assert widget.progress.value() == (0 if status is MaintenanceStatus.FAILURE else 1)
    text = widget.overview_output.toPlainText()
    assert_checks_visible(text, result, translator)
    if condition in {"scalar", "composite"}:
        assert any(check.identifiers for check in result.audit.checks)
    assert app.clipboard().text() == "Synthetic clipboard sentinel"
    assert widget.copy_report_button.isEnabled()  # A completed failing audit is still a valid diagnostic report.
    widget.copy_report_button.click()
    assert app.clipboard().text() == safe_diagnostic_report(result)
    for displayed in (text, app.clipboard().text()):
        for secret in (str(paths.directory), *codes, SECRET_VALUE, CANONICAL_VALUE, *CANONICAL, *ORIGINAL,
                       HMAC_KEY.hex(), VAULT_KEY.hex()):
            assert secret not in displayed
    assert paths.vault_database_path.read_bytes() == before


def test_completed_retranslation_preserves_objects_all_checks_selection_scroll_and_issues_no_io(overview, monkeypatch):
    app, widget, paths, _ = overview
    with sqlite3.connect(paths.vault_database_path) as connection:
        connection.execute("UPDATE vault_mappings SET total_occurrences=9")
    result = complete(app, widget)
    widget.overview_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
    cursor = widget.overview_output.document().find("mapping#1")
    assert not cursor.isNull()
    widget.overview_output.setTextCursor(cursor)
    widget.overview_output.setFocus()
    app.processEvents()
    vertical, horizontal = widget.overview_output.verticalScrollBar(), widget.overview_output.horizontalScrollBar()
    vertical.setValue(min(3, vertical.maximum()))
    horizontal.setValue(min(2, horizontal.maximum()))
    scroll = (vertical.value(), horizontal.value())
    before = (paths.vault_database_path.read_bytes(), paths.profiles_path.read_bytes())
    identities = tuple(widget.findChildren(QLineEdit)) + (widget.overview_output, widget.progress, widget.sections)
    report_events, tab_events, busy = (QSignalSpy(widget.overview_output.textChanged),
                                      QSignalSpy(widget.sections.currentChanged), QSignalSpy(widget.busy_changed))
    app.clipboard().setText("Synthetic clipboard sentinel")
    translators = [Translator(locale) for locale in ("en", "en", "pt-BR", "en", "pt-BR")]

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not perform diagnostics, database or filesystem work")

    with monkeypatch.context() as patch:
        for name in ("run", "_statistics"):
            patch.setattr(widget._diagnostics, name, forbidden)
        patch.setattr(widget._diagnostics._auditor, "run", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        for name in ("open", "stat", "iterdir", "rglob"):
            patch.setattr(Path, name, forbidden)
        for translator in translators:
            widget.retranslate(translator)
            assert widget._last_diagnostic is result and widget._last_audit is result.audit
            assert widget._last_diagnostic.statistics is result.statistics
            assert widget._last_diagnostic.audit.checks is result.audit.checks
            assert_checks_visible(widget.overview_output.toPlainText(), result, translator)
            assert widget.overview_output.textCursor().selectedText() == "mapping#1"
            assert widget.overview_output.hasFocus() and widget.sections.currentIndex() == 0
            assert (vertical.value(), horizontal.value()) == scroll
            assert widget.progress.value() == 0 and widget.progress.maximum() == 1
            assert widget.status_label.property("feedbackState") == "error" and widget.copy_report_button.isEnabled()
            assert identities == tuple(widget.findChildren(QLineEdit)) + (widget.overview_output, widget.progress, widget.sections)
    assert (paths.vault_database_path.read_bytes(), paths.profiles_path.read_bytes()) == before
    assert app.clipboard().text() == "Synthetic clipboard sentinel"
    assert not report_events.count() and not tab_events.count() and not busy.count()
    canonical = safe_diagnostic_report(result)
    calls = []
    monkeypatch.setattr("data_mask_studio.gui.maintenance_widget.safe_diagnostic_report",
                        lambda current: calls.append(current) or canonical)
    widget.copy_report_button.click()
    assert calls == [result] and app.clipboard().text() == canonical


@pytest.mark.parametrize("tab", [1, 2, 3])
@pytest.mark.parametrize("invalidated", [False, True])
def test_other_tab_state_is_unchanged_and_overview_feedback_is_translated_while_hidden(overview, tab, invalidated):
    app, widget, _, _ = overview
    result = complete(app, widget)
    widget.sections.setCurrentIndex(tab)
    widget.backup_path_field.setText("C:/Synthetic/ação & backup.dmsbackup")
    widget.backup_password_field.setText("synthetic maintenance password")
    widget.show_backup_password.setChecked(True)
    widget.backup_result.setPlainText("Texto literal do serviço <b>& Search</b>")
    widget.compaction_result.setPlainText("Resultado literal de compactação & Search")
    widget.progress.setRange(0, 12)
    widget.progress.setValue(4)
    widget._set_status("Feedback específico da outra aba & <literal>", False, state="warning")
    focused = (widget.validate_backup_button, widget.locate_button, widget.compact_button)[tab - 1]
    focused.setFocus()
    app.processEvents()
    snapshot = (widget._section_feedback[tab], widget.backup_path_field.text(), widget.backup_password_field.text(),
                widget.backup_password_field.echoMode(), widget.backup_result.toPlainText(), widget.compaction_result.toPlainText())
    if invalidated:
        widget.invalidate_diagnostic()
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert widget.sections.currentIndex() == tab and focused.hasFocus()
        assert (widget._section_feedback[tab], widget.backup_path_field.text(), widget.backup_password_field.text(),
                widget.backup_password_field.echoMode(), widget.backup_result.toPlainText(), widget.compaction_result.toPlainText()) == snapshot
        assert widget.progress.value() == 4 and widget.progress.maximum() == 12
        assert widget.status_label.text() == snapshot[0].message and widget.status_label.property("feedbackState") == "warning"
    widget.sections.setCurrentIndex(0)
    assert widget._last_diagnostic is (None if invalidated else result)
    identifier = "maintenance.overview.status.invalidated" if invalidated else "maintenance.overview.status.completed"
    parameters = {} if invalidated else {"status": "Healthy"}
    assert widget.status_label.text() == Translator("en").text(identifier, **parameters)
    assert widget.copy_report_button.isEnabled() is not invalidated


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("outcome", ["failed", "typed_error", "cancelled", "invalidated"])
def test_unsuccessful_second_run_never_restores_previous_success_or_copy_permission(overview, monkeypatch, locale, outcome):
    app, widget, _, _ = overview
    previous = complete(app, widget)
    widget.retranslate(Translator(locale))
    monkeypatch.setattr(DiagnosticWorker, "start", lambda self: None)
    widget.start_diagnostic()
    worker = widget._worker
    assert widget._last_diagnostic is None and not widget.copy_report_button.isEnabled()
    worker.progress.emit(13, 16)
    for language in ("en", "pt-BR", locale):
        widget.retranslate(Translator(language))
        assert widget._worker is worker and widget._diagnostic_accept_result
        assert widget.progress.value() == 13 and widget.progress.maximum() == 16
        assert not widget.sections.isEnabled() and widget.cancel_button.isVisible()
    if outcome == "failed":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    elif outcome == "typed_error":
        worker.failed.emit(MaintenanceError("Mensagem segura literal & <serviço>"))
    elif outcome == "cancelled":
        widget.cancel_button.click()
        widget.retranslate(Translator(locale))
        assert worker.cancellation_requested and not widget.cancel_button.isEnabled()
        worker.completed.emit(previous)
        worker.cancelled.emit()
    else:
        widget.invalidate_diagnostic()
        worker.progress.emit(16, 16)
        worker.completed.emit(previous)
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    worker.finished.emit()
    app.clipboard().setText("Synthetic clipboard sentinel")
    for language in ("en", "pt-BR", locale):
        widget.retranslate(Translator(language))
        assert widget._worker is None and not widget._diagnostic_accept_result
        assert widget._last_diagnostic is None and widget._last_audit is None
        assert not widget.overview_output.toPlainText() and not widget.copy_report_button.isEnabled()
        assert widget.progress.value() == 0 and widget.progress.maximum() == 1
        assert widget.sections.isEnabled() and widget.cancel_button.isHidden()
        assert "PRIVATE_SENTINEL" not in widget.status_label.text()
        assert widget.status_label.property("feedbackState") == ("error" if outcome in {"failed", "typed_error"} else "warning")
        if outcome == "typed_error":
            assert widget.status_label.text() == "Mensagem segura literal & <serviço>"
        widget.copy_report()
        assert app.clipboard().text() == "Synthetic clipboard sentinel"


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_real_queued_completion_stays_invalid_after_retranslation(overview, locale):
    app, widget, _, _ = overview
    widget.retranslate(Translator(locale))
    widget.start_diagnostic()
    worker = widget._worker
    assert worker.wait(10000)
    widget.invalidate_diagnostic()
    widget.retranslate(Translator("pt-BR" if locale == "en" else "en"))
    assert widget._worker is worker and not widget._diagnostic_accept_result
    app.processEvents()
    assert widget._worker is None and widget._last_diagnostic is None
    assert widget.progress.value() == 0 and not widget.copy_report_button.isEnabled()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_real_statistics_cancellation_and_failure_remain_fail_closed(overview, monkeypatch, locale):
    app, widget, _, _ = overview
    widget.retranslate(Translator(locale))
    start = DiagnosticWorker.start
    reached = []

    def start_and_cancel(worker):
        def cancel_in_statistics(done, total):
            if done == 13:
                reached.append((done, total))
                worker.request_cancel()
        worker.progress.connect(cancel_in_statistics, Qt.ConnectionType.DirectConnection)
        start(worker)

    with monkeypatch.context() as patch:
        patch.setattr(DiagnosticWorker, "start", start_and_cancel)
        widget.start_diagnostic()
        wait(app, widget)
    assert reached == [(13, 16)]
    assert widget._last_diagnostic is None and widget.progress.value() == 0
    assert widget.status_label.property("feedbackState") == "warning" and not widget.copy_report_button.isEnabled()

    def unavailable_statistics(*args, **kwargs):
        raise OSError("PRIVATE_SENTINEL")

    monkeypatch.setattr("data_mask_studio.maintenance.diagnostics.create_sqlite_snapshot", unavailable_statistics)
    result = complete(app, widget)
    assert result.audit.status is IntegrityStatus.INTACT and result.status is MaintenanceStatus.FAILURE
    assert widget.status_label.property("feedbackState") == "error" and widget.progress.value() == 0
    for language in ("en", "pt-BR"):
        widget.retranslate(Translator(language))
        for message in result.statistics.errors:
            assert message in widget.overview_output.toPlainText()
        assert "PRIVATE_SENTINEL" not in widget.overview_output.toPlainText()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_technical_identifiers_messages_and_timestamps_remain_literal_plain_text(overview, locale):
    app, widget, _, _ = overview
    result = complete(app, widget)
    check = CheckResult("maintenance.overview.refresh", IntegrityStatus.ATTENTION, 1234, 7,
                        'Descrição & <b>Search</b> {count} 😀', ("record#99", "item-é#7"))
    audit = replace(result.audit, checks=result.audit.checks + (check,))
    statistics = replace(result.statistics, first_entry="maintenance.value.none",
                         normalization_distribution=(("masking.normalization.exact", 42),),
                         errors=('Mensagem segura & <literal> {status}',))
    amended = replace(result, audit=audit, statistics=statistics, status=MaintenanceStatus.ATTENTION)
    rendered = _render_diagnostic(amended, Translator(locale))
    for value in (check.check_type, check.message, *check.identifiers, statistics.first_entry,
                  "masking.normalization.exact: 42", *statistics.errors):
        assert value in rendered
    widget.overview_output.setPlainText(rendered)
    assert widget.overview_output.toPlainText() == rendered
    assert "<b>Search</b>" in widget.overview_output.toPlainText()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_explicit_main_window_injection_and_shared_page_header(tmp_path, locale):
    app = create_application([])
    translator = Translator(locale)
    window = build_localized_window(tmp_path, translator)
    widget = window.maintenance_widget
    window.show()
    window.set_current_page(window.page_index(widget))
    app.processEvents()
    shell = window.page_shells[window.page_index(widget)]
    assert widget.refresh_button.text() == translator.text("maintenance.overview.refresh")
    assert shell.header.title_label.text() == translator.text("navigation.maintenance")
    assert shell.header.description_label.text() == translator.text("navigation.description.maintenance")
    assert window.page_stack.count() == 8


def test_normal_startup_remains_portuguese_with_saved_english(tmp_path):
    create_application([])
    service = PreferencesService(tmp_path / "preferences.json",
                                 ui_languages_provider=lambda: pytest.fail("No global language activation"))
    assert service.save_language("en").error is None
    before = service.path.read_bytes()
    window = build_localized_window(tmp_path, preferences_service=service)
    assert window.maintenance_widget.refresh_button.text() == "Atualizar diagnóstico"
    assert window.maintenance_widget.sections.tabText(0) == "Visão geral"
    assert service.path.read_bytes() == before
