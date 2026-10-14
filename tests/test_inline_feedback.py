"""Explicit inline semantics, without screenshot or layout-pixel assertions."""

import os
import re
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.anonymization.models import AnonymizationResult, NormalizationFallback
from data_mask_studio.app import create_application
from data_mask_studio.backup.models import BackupCompatibility, BackupValidationResult
from data_mask_studio.batch.models import BatchFile, BatchFileStatus, BatchSummary
from data_mask_studio.batch_restoration.models import (
    BatchRestorationFile, BatchRestorationFileType, BatchRestorationStatus,
    BatchRestorationSummary,
)
from data_mask_studio.consultant import ConsultationResult, ConsultationStatus
from data_mask_studio.gui.components.presentation import set_feedback_state
from data_mask_studio.gui.styles import application_stylesheet
from data_mask_studio.gui.visual_tokens import COLORS
from data_mask_studio.html_restoration import (
    HTMLMissingCodePolicy, HTMLRestorationResult, HTMLRestorationSecurityError,
)
from data_mask_studio.integrity import AuditReport, CheckResult, IntegrityStatus
from data_mask_studio.maintenance.models import (
    CleanupResult, DiagnosticResult, EnvironmentStatistics, MaintenanceStatus,
)
from data_mask_studio.restoration import (
    MissingCodePolicy, RepresentationPolicy, RestorationResult, RestorationSecurityError,
)
from test_main_navigation import build_window


STATE_COLORS = {
    "neutral": COLORS["muted_text"], "success": COLORS["success_text"],
    "warning": COLORS["warning_text"], "error": COLORS["danger_text"],
}


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated"))
    app = create_application([])
    window = build_window(tmp_path)
    return app, window


def assert_feedback(label, state):
    label.ensurePolished()
    assert label.property("feedbackState") == state
    assert label.palette().color(QPalette.ColorRole.WindowText) == QColor(STATE_COLORS[state])
    assert label.styleSheet() == ""  # Color is supplied by the shared opt-in QSS.


def make_audit(tmp_path, status):
    now = datetime.now(timezone.utc)
    checks = tuple(CheckResult(name, status, 1, int(status is IntegrityStatus.FAILURE),
                               "Resultado agregado seguro.")
                   for name in ("Disponibilidade da chave HMAC", "Disponibilidade da chave AES"))
    return AuditReport(now, now, tmp_path / "vault.db", tmp_path / "profiles.json", 4, checks)


def test_helper_changes_only_semantic_color_and_repolishes_each_transition():
    create_application([])
    label = QLabel("Unchanged synthetic guidance")
    label.setWordWrap(True)
    label.setAccessibleName("Synthetic feedback")
    label.ensurePolished()
    original_font = label.font()
    original_size_hint = label.sizeHint()
    original_focus = label.focusPolicy()
    original_hidden = label.isHidden()
    for state in ("neutral", "success", "warning", "error", "neutral", "neutral"):
        set_feedback_state(label, state)
        assert_feedback(label, state)
        assert label.text() == "Unchanged synthetic guidance"
        assert label.accessibleName() == "Synthetic feedback" and label.wordWrap()
        assert label.font() == original_font and label.sizeHint() == original_size_hint
        assert label.focusPolicy() == original_focus and label.isHidden() == original_hidden


def test_qss_is_scoped_to_explicit_labels_and_preserves_ordinary_elements(page):
    _, window = page
    rules = [(selector.strip(), body.strip()) for selector, body in
             re.findall(r"([^{}]+)\{([^{}]*)\}", application_stylesheet())
             if "feedbackState" in selector]
    assert dict(rules) == {
        f'QLabel[feedbackState="{state}"]': f"color: {color};"
        for state, color in STATE_COLORS.items()
    }
    dialog = QDialog()
    message = QMessageBox(QMessageBox.Icon.Warning, "Synthetic", "Unchanged warning")
    ordinary = [window.anonymization_widget.file_name_label,
                window.restoration_widget.options_summary,
                window.anonymization_widget.config_table,
                window.anonymization_widget.generate_button,
                window.navigation.identity.name_label,
                window.page_shells[0].header.description_label, dialog, message]
    for widget in ordinary:
        widget.ensurePolished()
    palettes = [widget.palette() for widget in ordinary]
    for state in STATE_COLORS:
        set_feedback_state(window.restoration_widget.status_label, state)
    assert all(widget.property("feedbackState") is None for widget in ordinary)
    assert [widget.palette() for widget in ordinary] == palettes
    assert all(label.property("feedbackState") is None for label in message.findChildren(QLabel))


@pytest.mark.parametrize("state", list(STATE_COLORS))
def test_semantic_colors_are_distinct_and_readable_on_existing_dark_surfaces(state):
    def luminance(hex_color):
        color = QColor(hex_color)
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in channels]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))

    assert len(set(STATE_COLORS.values())) == 4
    for background in ("window", "base", "surface"):
        ratio = (luminance(STATE_COLORS[state]) + 0.05) / (luminance(COLORS[background]) + 0.05)
        assert ratio >= 4.5


def test_initial_operational_feedback_is_neutral_but_vault_warning_stays_prominent(page):
    _, window = page
    for widget in (window.anonymization_widget, window.batch_widget, window.restoration_widget,
                   window.html_restoration_widget, window.batch_restoration_widget,
                   window.consultant_widget, window.integrity_widget, window.maintenance_widget):
        assert_feedback(widget.status_label, "neutral")
    assert_feedback(window.backup_widget.create_status, "neutral")
    assert_feedback(window.backup_widget.restore_status, "neutral")
    warning = next(label for label in window.consultant_widget.findChildren(QLabel)
                   if label.text().startswith("Atenção: os resultados"))
    assert warning.wordWrap()
    assert warning.text() == ("Atenção: os resultados contêm dados sensíveis e devem permanecer "
                              "neste ambiente local.")
    assert_feedback(warning, "warning")
    recommendation = next(label for label in window.backup_widget.findChildren(QLabel)
                          if label.text().startswith("Use uma frase-senha"))
    assert_feedback(recommendation, "neutral")


@pytest.mark.parametrize("name", ["anonymization", "batch", "restoration", "html_restoration",
                                  "batch_restoration", "consultant", "backup", "integrity", "maintenance"])
def test_existing_page_status_helpers_share_states_and_error_overrides_success(page, name):
    _, window = page
    widget = getattr(window, f"{name}_widget")
    label = widget.create_status if name == "backup" else widget.status_label

    def update(state, error=False):
        if name == "backup":
            widget._status(label, "Synthetic feedback", error, state=state)
        elif name in ("anonymization", "batch", "restoration", "consultant"):
            widget._set_status("Synthetic feedback", is_error=error, state=state)
        else:
            widget._set_status("Synthetic feedback", error, state=state)

    for state in STATE_COLORS:
        update(state)
        assert label.text() == "Synthetic feedback"
        assert_feedback(label, state)
    update("success", error=True)
    assert_feedback(label, "error")


def test_anonymization_validation_success_edit_error_and_clear_states(page, tmp_path):
    _, window = page
    widget = window.anonymization_widget
    source = tmp_path / "source.csv"
    source.write_text("Nome,CPF\nSynthetic,123\n", encoding="utf-8")
    widget.load_csv(str(source))
    assert_feedback(widget.status_label, "success")
    widget._action_fields[0].setCurrentIndex(widget._action_fields[0].findData(ColumnAction.MASK.value))
    assert_feedback(widget.status_label, "neutral")
    widget.validate_current_configuration()
    assert_feedback(widget.status_label, "success")
    assert widget.generate_button.isEnabled()
    widget._prefix_fields[0].setText("1")
    widget.validate_current_configuration()
    assert_feedback(widget.status_label, "error")
    assert not widget.generate_button.isEnabled()
    widget.clear_selection()
    assert_feedback(widget.status_label, "neutral")
    assert widget.status_label.text() == "Seleção limpa."


def test_replaced_empty_headers_are_warnings_in_both_csv_pages(page, tmp_path):
    _, window = page
    source = tmp_path / "source.csv"
    source.write_text("Nome,,CPF\nSynthetic,x,123\n", encoding="utf-8")
    for widget in (window.anonymization_widget, window.restoration_widget):
        widget.load_csv(str(source))
        assert_feedback(widget.status_label, "warning")
        assert "cabeçalho vazio foi substituído" in widget.status_label.text()
        widget.clear_selection()
        assert_feedback(widget.status_label, "neutral")


@pytest.mark.parametrize("fallback", [False, True])
def test_anonymization_completion_retains_existing_fallback_notice_and_cancellation(page, tmp_path, fallback):
    _, window = page
    widget = window.anonymization_widget
    result = AnonymizationResult(tmp_path / "output.csv", 2, 0.1,
                                normalization_fallbacks=(NormalizationFallback("IP", 1),) if fallback else ())
    widget._processing_completed(result)
    assert_feedback(widget.status_label, "warning" if fallback else "success")
    assert "CSV anonimizado gerado com sucesso." in widget.status_label.text()
    assert ("processados por valor exato" in widget.status_label.text()) is fallback
    widget._processing_cancelled()
    assert_feedback(widget.status_label, "warning")
    assert widget.status_label.text() == "A geração do CSV foi cancelada."


@pytest.mark.parametrize("kind", ["csv", "html"])
@pytest.mark.parametrize("missing", [0, 1])
def test_restoration_completion_distinguishes_existing_missing_code_notices(page, tmp_path, kind, missing):
    _, window = page
    if kind == "csv":
        widget = window.restoration_widget
        result = RestorationResult(tmp_path / "output.csv", 2, 2 - missing, missing, 0, 0,
                                   0.1, MissingCodePolicy.KEEP, RepresentationPolicy.FIRST_ORIGINAL)
        expected = "CSV restaurado gerado com sucesso."
    else:
        widget = window.html_restoration_widget
        result = HTMLRestorationResult(tmp_path / "output.html", "utf-8", 2, 2 - missing,
                                       missing, 0.1, HTMLMissingCodePolicy.KEEP,
                                       RepresentationPolicy.FIRST_ORIGINAL)
        expected = "HTML restaurado gerado com sucesso."
    widget._restoration_completed(result)
    assert widget.status_label.text() == expected
    assert_feedback(widget.status_label, "warning" if missing else "success")
    widget._cancelled()
    assert_feedback(widget.status_label, "warning")


@pytest.mark.parametrize("kind", ["anonymization", "restoration", "html_restoration", "integrity", "maintenance"])
def test_unexpected_failures_keep_existing_safe_messages_and_error_presentation(page, kind):
    _, window = page
    widget = getattr(window, f"{kind}_widget")
    handler = widget._processing_failed if kind == "anonymization" else widget._failed
    handler(RuntimeError("SENSITIVE_SENTINEL traceback details"))
    assert_feedback(widget.status_label, "error")
    assert "SENSITIVE_SENTINEL" not in widget.status_label.text()
    assert "traceback" not in widget.status_label.text().lower()


@pytest.mark.parametrize("kind,error_type", [("restoration", RestorationSecurityError),
                                             ("html_restoration", HTMLRestorationSecurityError)])
def test_security_failures_remain_redacted(page, kind, error_type):
    _, window = page
    widget = getattr(window, f"{kind}_widget")
    widget._failed(error_type("SENSITIVE_SENTINEL"))
    assert_feedback(widget.status_label, "error")
    assert "SENSITIVE_SENTINEL" not in widget.status_label.text()
    assert "segurança" in widget.status_label.text()


@pytest.mark.parametrize("status,state", [(IntegrityStatus.INTACT, "success"),
                                         (IntegrityStatus.ATTENTION, "warning"),
                                         (IntegrityStatus.FAILURE, "error")])
def test_integrity_uses_existing_report_classification_and_resets_presentation(page, tmp_path, status, state):
    _, window = page
    widget = window.integrity_widget
    report = make_audit(tmp_path, status)
    widget._completed(report)
    assert_feedback(widget.status_label, state)
    assert widget.report_view.toPlainText() == report.to_safe_text()
    widget.clear_report()
    assert_feedback(widget.status_label, "neutral")
    widget._cancelled()
    assert_feedback(widget.status_label, "warning")


@pytest.mark.parametrize("status,state", [(MaintenanceStatus.HEALTHY, "success"),
                                         (MaintenanceStatus.ATTENTION, "warning"),
                                         (MaintenanceStatus.FAILURE, "error")])
def test_diagnostics_use_existing_status_without_turning_attention_green(page, tmp_path, status, state):
    _, window = page
    audit = make_audit(tmp_path, IntegrityStatus.INTACT)
    statistics = EnvironmentStatistics(4, 0, 0, 0, 0, 0, 0, (), None, None,
                                       False, False, False, 0, 1024)
    result = DiagnosticResult(audit.finished_at, status, statistics, audit)
    widget = window.maintenance_widget
    widget._diagnostic_completed(result)
    assert_feedback(widget.status_label, state)
    assert "Diagnóstico concluído:" in widget.status_label.text()
    widget._compaction_phase("Etapa segura em andamento", True)
    assert_feedback(widget.status_label, "neutral")
    assert widget.status_label.text() == "Etapa segura em andamento"
    widget._cancelled()
    assert_feedback(widget.status_label, "warning")


@pytest.mark.parametrize("compatible", [False, True])
def test_backup_validation_keeps_compatibility_rules_and_clear_resets_neutral(page, compatible):
    _, window = page
    result = BackupValidationResult(datetime.now(timezone.utc), "1.2.0", 1, 4, 1, 0, True,
        BackupCompatibility.COMPATIBLE if compatible else BackupCompatibility.INCOMPATIBLE)
    widget = window.backup_widget
    widget._validation_completed(result)
    assert_feedback(widget.restore_status, "success" if compatible else "error")
    assert widget.restore_button.isEnabled() is compatible
    assert (widget._validated_result is not None) is compatible
    widget._invalidate_validation()
    assert_feedback(widget.restore_status, "neutral")
    window.maintenance_widget._backup_completed(result)
    assert_feedback(window.maintenance_widget.status_label, "success" if compatible else "warning")
    widget._cancelled()
    assert_feedback(widget.create_status, "warning")
    assert_feedback(widget.restore_status, "warning")
    widget._failed(RuntimeError("SENSITIVE_SENTINEL"))
    assert_feedback(widget.restore_status, "error")
    assert "SENSITIVE_SENTINEL" not in widget.restore_status.text()


@pytest.mark.parametrize("kind", ["mask", "restore"])
@pytest.mark.parametrize("outcome,state", [("completed", "success"), ("partial", "warning"),
                                          ("failed", "error")])
def test_batch_completion_keeps_counters_and_distinguishes_partial_results(page, tmp_path, kind, outcome, state):
    _, window = page
    failed, partial = int(outcome == "failed"), int(outcome == "partial")
    if kind == "mask":
        widget = window.batch_widget
        summary = BatchSummary(3, 3, 3 - failed - partial, 0, failed, partial, 2, 0, 0, 0.1, tmp_path)
        expected = "Processamento em lote encerrado."
    else:
        widget = window.batch_restoration_widget
        summary = BatchRestorationSummary(3, 3 - failed - partial, failed, 0, partial, 2, 0,
                                           tmp_path, 0.1, cancelled=bool(partial))
        expected = "Lote cancelado com segurança." if partial else "Lote concluído."
    widget.overall_progress.setRange(0, 3)
    widget._processing_completed(summary)
    assert widget.status_label.text() == expected
    assert_feedback(widget.status_label, state)
    assert widget.overall_progress.value() == (3 - partial if kind == "restore" else 3)
    assert widget.open_output_button.isEnabled() == (summary.completed_files > 0)
    widget.clear_files()
    assert_feedback(widget.status_label, "neutral")


def test_batch_validations_distinguish_review_partial_success_and_failure(page, tmp_path):
    _, window = page
    mask, restore = window.batch_widget, window.batch_restoration_widget
    mask.files = [BatchFile(tmp_path / "a.csv", BatchFileStatus.COMPATIBLE),
                  BatchFile(tmp_path / "b.csv", BatchFileStatus.INCOMPATIBLE)]
    restore.files = [BatchRestorationFile(tmp_path / "a.csv", BatchRestorationFileType.CSV,
                                          BatchRestorationStatus.COMPATIBLE),
                     BatchRestorationFile(tmp_path / "b.csv", BatchRestorationFileType.CSV,
                                          BatchRestorationStatus.REVIEW_REQUIRED)]
    for widget, handler, compatible, incompatible in (
        (mask, mask._validation_completed, BatchFileStatus.COMPATIBLE, BatchFileStatus.INCOMPATIBLE),
        (restore, restore._analysis_completed, BatchRestorationStatus.COMPATIBLE,
         BatchRestorationStatus.INCOMPATIBLE),
    ):
        handler()
        assert_feedback(widget.status_label, "warning")
        widget.files[1].status = compatible
        handler()
        assert_feedback(widget.status_label, "success")
        for item in widget.files:
            item.status = incompatible
        handler()
        assert_feedback(widget.status_label, "error")


def test_maintenance_cleanup_preserves_report_text_and_warning_for_preserved_files(page):
    _, window = page
    widget = window.maintenance_widget
    for result, state in ((CleanupResult(1, 0, 0, 0), "success"),
                          (CleanupResult(1, 1, 0, 0), "warning"),
                          (CleanupResult(0, 0, 1, 0), "error")):
        widget._cleanup_completed(result)
        assert_feedback(widget.status_label, state)
        assert widget.status_label.text() == (f"Limpeza concluída: {result.removed} removido(s), "
                                             f"{result.preserved} preservado(s), {result.failed} falha(s).")


@pytest.mark.parametrize("status,state", [(ConsultationStatus.NOT_FOUND, "warning"),
                                         (ConsultationStatus.RECOVERY_FAILED, "error")])
def test_consultant_missing_or_unsafe_results_do_not_look_like_success(page, monkeypatch, status, state):
    _, window = page
    widget = window.consultant_widget
    monkeypatch.setattr(widget._service, "consult", lambda _: [
        ConsultationResult("NOME-ABCDEFGHI234", status, message="Resultado seguro.")])
    widget.consult()
    assert_feedback(widget.status_label, state)
    assert widget.status_label.text() == "Consulta concluída: 0 de 1 códigos encontrados."
    widget.clear_consultation()
    assert_feedback(widget.status_label, "neutral")
    assert widget.status_label.text() == ""
