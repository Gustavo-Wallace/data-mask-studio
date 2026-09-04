import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QMessageBox

from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.detection import ConfidenceLevel, SuggestedType
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.gui.detection_worker import DetectionWorker
from data_mask_studio.profiles import ProfileRepository, ProfileService


def make_widget(tmp_path: Path) -> AnonymizationWidget:
    return AnonymizationWidget(
        profile_service=ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    )


def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "people.csv"
    path.write_text(
        "cpf,email,observacao\n"
        "123.456.789-00,ana@example.com,Cliente ativo\n"
        "987.654.321-00,bia@example.com,Sem observações\n",
        encoding="utf-8",
    )
    return path


def run_analysis(widget: AnonymizationWidget, application) -> None:
    widget.analyze_columns()
    worker = widget._detection_worker
    assert worker is not None and worker.wait(5000)
    application.processEvents()


def test_detection_dialog_shows_only_aggregated_suggestions_offscreen(
    tmp_path: Path,
) -> None:
    application = create_application([])
    widget = make_widget(tmp_path)
    widget.load_csv(str(sample_csv(tmp_path)))

    run_analysis(widget, application)

    dialog = widget._detection_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.table.rowCount() == 3
    assert widget._suggestions[0].suggested_type is SuggestedType.CPF
    assert widget._suggestions[0].confidence is ConfidenceLevel.HIGH
    visible_text = " ".join(
        dialog.table.item(row, column).text()
        for row in range(dialog.table.rowCount())
        for column in range(dialog.table.columnCount())
        if dialog.table.item(row, column) is not None
    )
    assert "123.456.789-00" not in visible_text
    assert "ana@example.com" not in visible_text

    widget.close()
    application.quit()


def test_individual_and_accepted_suggestions_are_applied_explicitly(
    tmp_path: Path, monkeypatch
) -> None:
    application = create_application([])
    widget = make_widget(tmp_path)
    widget.load_csv(str(sample_csv(tmp_path)))
    run_analysis(widget, application)
    dialog = widget._detection_dialog
    assert dialog is not None

    dialog.table.cellWidget(0, 8).click()

    assert widget._column_configs[0].anonymize
    assert widget._column_configs[0].prefix == "CPF"
    assert not widget._column_configs[1].anonymize

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog._acceptance_fields[1].setChecked(True)
    dialog.apply_accepted_button.click()

    assert widget._column_configs[1].anonymize
    assert widget._column_configs[1].prefix == "EMAIL"

    widget.close()
    application.quit()


def test_high_confidence_bulk_application_requires_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    application = create_application([])
    widget = make_widget(tmp_path)
    widget.load_csv(str(sample_csv(tmp_path)))
    run_analysis(widget, application)
    dialog = widget._detection_dialog
    assert dialog is not None
    confirmations: list[str] = []

    def confirm(_parent, _title, message, *_args, **_kwargs):
        confirmations.append(message)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    dialog.apply_high_button.click()

    assert confirmations
    assert widget._column_configs[0].anonymize
    assert widget._column_configs[1].anonymize
    assert not widget._column_configs[2].anonymize

    widget.close()
    application.quit()


def test_manual_configuration_is_preserved_without_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    application = create_application([])
    widget = make_widget(tmp_path)
    widget.load_csv(str(sample_csv(tmp_path)))
    widget._action_fields[0].setCurrentIndex(
        widget._action_fields[0].findData(ColumnAction.MASK.value)
    )
    widget._prefix_fields[0].setText("MANUAL")
    run_analysis(widget, application)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    widget.apply_detection_suggestion(0)

    assert widget._column_configs[0].prefix == "MANUAL"
    assert 0 in widget._manually_changed_rows

    widget.close()
    application.quit()


def test_clearing_suggestions_preserves_current_configuration(tmp_path: Path) -> None:
    application = create_application([])
    widget = make_widget(tmp_path)
    widget.load_csv(str(sample_csv(tmp_path)))
    widget._action_fields[0].setCurrentIndex(
        widget._action_fields[0].findData(ColumnAction.MASK.value)
    )
    widget._prefix_fields[0].setText("MEU_CPF")
    run_analysis(widget, application)

    widget.clear_detection_suggestions()

    assert widget._suggestions == ()
    assert widget._column_configs[0].prefix == "MEU_CPF"

    widget.close()
    application.quit()


def test_detection_worker_emits_cancelled_signal(tmp_path: Path) -> None:
    application = create_application([])
    inspection = inspect_csv(sample_csv(tmp_path))
    worker = DetectionWorker(inspection)
    cancelled: list[bool] = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.request_cancel()

    worker.start()
    assert worker.wait(5000)
    application.processEvents()

    assert cancelled == [True]
    application.quit()


def test_datetime_suggestion_is_displayed_safely_offscreen(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    sensitive_sample = "31/07/2026 23:59"
    path.write_text(
        f"Data/Hora (BRT),evento\n{sensitive_sample},login\n",
        encoding="utf-8",
    )
    application = create_application([])
    widget = make_widget(tmp_path)
    widget.load_csv(str(path))

    run_analysis(widget, application)

    dialog = widget._detection_dialog
    assert dialog is not None
    suggestion = widget._suggestions[0]
    assert suggestion.suggested_type is SuggestedType.DATETIME
    assert dialog.table.item(0, 2).text() == "Data/Hora"
    assert dialog.table.item(0, 3).text() == "Não"
    assert dialog.table.item(0, 4).text() == "—"
    assert dialog.table.item(0, 5).text() == "Valor exato"
    assert dialog.table.item(0, 6).text() == "Alta"
    reason_item = dialog.table.item(0, 7)
    assert reason_item.toolTip() == reason_item.text()
    visible = " ".join(
        dialog.table.item(0, column).text()
        for column in range(dialog.table.columnCount())
        if dialog.table.item(0, column) is not None
    )
    assert sensitive_sample not in visible

    widget.close()
    application.quit()
