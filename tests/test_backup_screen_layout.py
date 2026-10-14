"""Backup/recovery presentation contracts; no screenshot assertions."""

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QGroupBox, QLabel, QLineEdit, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.backup import (
    BackupCompatibility, BackupCreationResult, BackupValidationResult, RestoreResult,
)
from data_mask_studio.gui.backup_worker import (
    BackupCreationWorker, BackupRestoreWorker, BackupValidationWorker,
)
from data_mask_studio.metadata import application_version
from test_backup_interface import PASSWORD, prepare_widget
from test_main_navigation import build_window


@pytest.fixture
def backup(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    widget, _ = prepare_widget(tmp_path)
    widget.destination_field.setText(str(tmp_path / "synthetic.dmsbackup"))
    widget.show()
    app.processEvents()
    return app, widget


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    window.show()
    window.set_current_page(window.page_index(window.backup_widget))
    window.resize(1280, 820)
    app.processEvents()
    return app, window


def validation_result(compatible=True):
    return BackupValidationResult(
        datetime.now(timezone.utc), application_version(), 1, 4, 1, 0, True,
        BackupCompatibility.COMPATIBLE if compatible else BackupCompatibility.INCOMPATIBLE,
    )


def test_initial_workflows_keep_hierarchy_controls_and_separate_feedback(backup):
    _, widget = backup
    create, restore = widget.findChildren(QGroupBox)
    assert [create.title(), restore.title()] == ["Criar backup", "Restaurar backup"]
    assert create.geometry().bottom() < restore.geometry().top()
    for group, controls in (
        (create, (widget.destination_field, widget.choose_destination_button,
                  widget.create_password_field, widget.confirm_password_field,
                  widget.show_create_password, widget.create_button,
                  widget.open_folder_button, widget.create_progress, widget.create_status)),
        (restore, (widget.restore_file_field, widget.choose_restore_button,
                   widget.restore_password_field, widget.show_restore_password,
                   widget.validate_backup_button, widget.restore_button,
                   widget.restore_summary, widget.restore_progress, widget.restore_status)),
    ):
        assert all(group.isAncestorOf(control) and control.isVisible() for control in controls)
    assert widget.create_button.property("role") == "primary"
    assert widget.validate_backup_button.property("role") == "primary"
    assert widget.restore_button.property("role") == "attention"
    assert widget.open_folder_button.property("role") is None
    assert widget.create_button.isEnabled() and not widget.open_folder_button.isEnabled()
    assert not widget.validate_backup_button.isEnabled() and not widget.restore_button.isEnabled()
    assert widget.destination_field.isReadOnly() and widget.restore_file_field.isReadOnly()
    assert widget.create_progress.value() == widget.restore_progress.value() == 0
    assert widget.create_status.text() == "Escolha o destino e informe uma senha."
    assert widget.restore_status.text() == "Selecione um arquivo .dmsbackup."
    assert widget.create_status.property("feedbackState") == widget.restore_status.property("feedbackState") == "neutral"
    assert widget.create_cancel_button.isHidden() and widget.restore_cancel_button.isHidden()
    assert widget.restore_summary.isReadOnly() and not widget.restore_summary.toPlainText()
    assert widget.restore_summary.empty_text == "Valide um backup para exibir o resumo técnico."
    recommendation = next(label for label in create.findChildren(QLabel)
                          if label.text().startswith("Use uma frase-senha"))
    assert recommendation.text() == "Use uma frase-senha longa, única e bem guardada."
    assert recommendation.wordWrap() and recommendation.property("feedbackState") == "neutral"


def test_summary_releases_empty_constraint_and_gets_spare_space(page):
    app, window = page
    widget = window.backup_widget
    create = widget.create_button.parentWidget()
    empty_height = widget.restore_summary.height()
    empty_maximum = widget.restore_summary.maximumHeight()
    create_height = create.height()
    assert widget.layout().stretch(1) == 0 and widget.layout().stretch(2) == 1
    widget._validation_completed(validation_result())
    app.processEvents()
    populated_height = widget.restore_summary.height()
    assert widget.restore_summary.maximumHeight() > empty_maximum
    assert populated_height > empty_height
    assert widget.layout().stretch(1) == 1 and widget.layout().stretch(2) == 0
    assert create.height() == create_height
    shell = window.page_shells[window.page_index(widget)]
    assert shell.scroll_area.verticalScrollBar().maximum() == 0
    window.resize(1280, 1000)
    app.processEvents()
    assert widget.restore_summary.height() > populated_height
    assert create.height() == create_height
    assert shell.scroll_area.verticalScrollBar().maximum() == 0
    widget.restore_summary.clear()
    app.processEvents()
    assert widget.restore_summary.maximumHeight() == empty_maximum
    assert widget.restore_summary.height() <= empty_maximum
    assert widget.layout().stretch(1) == 0 and widget.layout().stretch(2) == 1


@pytest.mark.parametrize("compatible", [False, True])
def test_selection_validation_and_invalidation_preserve_restore_safeguards(backup, monkeypatch, tmp_path, compatible):
    _, widget = backup
    source = str(tmp_path / "synthetic.dmsbackup")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (source, ""))
    widget.choose_restore_button.click()
    assert widget.restore_file_field.text() == source
    assert not widget.validate_backup_button.isEnabled() and not widget.restore_button.isEnabled()
    widget.restore_password_field.setText(PASSWORD)
    assert widget.validate_backup_button.isEnabled() and not widget.restore_button.isEnabled()
    result = validation_result(compatible)
    widget._validation_completed(result)
    assert widget.restore_button.isEnabled() is compatible
    assert widget.restore_summary.toPlainText() == "\n".join((
        f"Data do backup: {result.created_at.isoformat()}",
        f"Versão da aplicação: {result.application_version}",
        "Versão do formato: 1", "Mapeamentos: 1", "Perfis: 0", "Cofre presente: Sim",
        "Compatibilidade: " + ("Compatível" if compatible else "Incompatível"),
    ))
    assert widget.restore_status.property("feedbackState") == ("success" if compatible else "error")
    widget.restore_password_field.setText(PASSWORD + " different")
    assert widget._validated_result is None and not widget.restore_button.isEnabled()
    assert not widget.restore_summary.toPlainText()
    assert widget.restore_status.property("feedbackState") == "neutral"


def test_password_visibility_and_keyboard_order_remain_unchanged(backup):
    app, widget = backup
    fields = (widget.create_password_field, widget.confirm_password_field, widget.restore_password_field)
    for field in fields:
        field.setText(PASSWORD)
        assert field.echoMode() == QLineEdit.EchoMode.Password
    widget.show_create_password.setChecked(True)
    assert all(field.echoMode() == QLineEdit.EchoMode.Normal for field in fields[:2])
    assert fields[2].echoMode() == QLineEdit.EchoMode.Password
    widget.show_restore_password.setChecked(True)
    assert fields[2].echoMode() == QLineEdit.EchoMode.Normal
    widget.show_create_password.setChecked(False)
    widget.show_restore_password.setChecked(False)
    assert all(field.echoMode() == QLineEdit.EchoMode.Password and field.text() == PASSWORD for field in fields)
    widget.activateWindow()
    fields[0].setFocus()
    app.processEvents()
    for next_control in (fields[1], widget.show_create_password, widget.create_button):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert next_control.hasFocus()
    widget.restore_file_field.setText("synthetic.dmsbackup")
    widget._validation_completed(validation_result())
    fields[2].setFocus()
    for next_control in (widget.show_restore_password, widget.validate_backup_button,
                         widget.restore_button, widget.restore_summary):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert next_control.hasFocus()
    assert widget.stop_worker()
    assert all(field.text() == "" for field in fields)


@pytest.mark.parametrize("kind,worker_type", [
    ("create", BackupCreationWorker), ("validate", BackupValidationWorker), ("restore", BackupRestoreWorker),
])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_worker_feedback_stays_in_existing_workflow_groups(backup, monkeypatch, tmp_path, kind, worker_type, outcome):
    _, widget = backup
    monkeypatch.setattr(worker_type, "start", lambda self: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.create_password_field.setText(PASSWORD)
    widget.confirm_password_field.setText(PASSWORD)
    widget.restore_file_field.setText(str(tmp_path / "synthetic.dmsbackup"))
    widget.restore_password_field.setText(PASSWORD)
    if kind == "restore":
        widget._validation_completed(validation_result())
    status, progress, cancel = (
        (widget.create_status, widget.create_progress, widget.create_cancel_button)
        if kind == "create" else (widget.restore_status, widget.restore_progress, widget.restore_cancel_button)
    )
    other_status, other_progress = (
        (widget.restore_status, widget.restore_progress)
        if kind == "create" else (widget.create_status, widget.create_progress)
    )
    before = (other_status.text(), other_progress.minimum(), other_progress.maximum(), other_progress.value())
    button = {"create": widget.create_button, "validate": widget.validate_backup_button, "restore": widget.restore_button}[kind]
    button.click()
    worker = widget._worker
    assert isinstance(worker, worker_type)
    assert progress.isVisible() and progress.maximum() == 0 and cancel.isVisible() and cancel.isEnabled()
    assert status.property("feedbackState") == "neutral"
    assert before == (other_status.text(), other_progress.minimum(), other_progress.maximum(), other_progress.value())
    assert not widget.create_button.isEnabled() and not widget.validate_backup_button.isEnabled()
    if outcome == "success":
        result = {
            "create": BackupCreationResult(tmp_path / "synthetic.dmsbackup", datetime.now(timezone.utc), 128),
            "validate": validation_result(), "restore": RestoreResult(1, 0, True),
        }[kind]
        worker.completed.emit(result)
        assert progress.maximum() == progress.value() == 1
        assert status.property("feedbackState") == "success"
        assert other_status.text() == before[0]
        if kind == "create":
            assert widget.open_folder_button.isEnabled()
            assert widget.create_password_field.text() == widget.confirm_password_field.text() == ""
        elif kind == "validate":
            assert widget.restore_button.isEnabled() and widget.restore_summary.toPlainText()
        else:
            assert widget.restore_password_field.text() == "" and not widget.restore_button.isEnabled()
    elif outcome == "failure":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
        assert status.property("feedbackState") == "error"
        assert status.text() == "A operação de backup falhou."
        assert other_status.text() == before[0]
        assert not widget.restore_button.isEnabled()
    else:
        cancel.click()
        assert worker._cancellation.is_requested() and not cancel.isEnabled()
        worker.cancelled.emit()
        assert widget.create_status.property("feedbackState") == widget.restore_status.property("feedbackState") == "warning"
        assert all(field.text() == "" for field in (
            widget.create_password_field, widget.confirm_password_field, widget.restore_password_field,
        ))
    worker.finished.emit()
    assert widget._worker is None and widget.create_button.isEnabled()
    assert widget.create_cancel_button.isHidden() and widget.restore_cancel_button.isHidden()
    assert widget.create_progress.maximum() == widget.restore_progress.maximum() == 1


@pytest.mark.parametrize("size", [(960, 640), (1280, 820), "maximized"])
def test_summary_scroll_and_feedback_remain_reachable_at_supported_sizes(page, size):
    app, window = page
    widget = window.backup_widget
    shell = window.page_shells[window.page_index(widget)]
    if size == "maximized":
        window.showMaximized()
        assert window.isMaximized()
    else:
        window.resize(*size)
    for text in ("", "Synthetic summary line\n" * 120):
        widget.restore_summary.setPlainText(text)
        app.processEvents()
        assert shell.header.isVisible() and widget.restore_summary.isVisible()
        # Qt offscreen may maximize to a virtual screen narrower than the
        # supported minimum. At supported widths no horizontal scroll is needed.
        if window.width() >= window._MINIMUM_WIDTH:
            assert shell.scroll_area.horizontalScrollBar().maximum() == 0
        else:
            assert size == "maximized"
        assert widget.restore_summary.viewport().height() > 0
        if text:
            assert widget.restore_summary.verticalScrollBar().maximum() > 0
        for control in (widget.create_status, widget.restore_button, widget.restore_status):
            shell.scroll_area.ensureWidgetVisible(control)
            app.processEvents()
            top = control.mapTo(shell.scroll_area.viewport(), QPoint()).y()
            assert 0 <= top <= shell.scroll_area.viewport().height() - control.height()
