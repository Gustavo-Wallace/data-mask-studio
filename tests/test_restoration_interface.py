import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QFileDialog, QMessageBox

from data_mask_studio.app import create_application
from data_mask_studio.gui.restoration_widget import RestorationWidget
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

CODE = "CPF-ABCDEFGHI234"


def repository(tmp_path: Path) -> VaultRepository:
    result = VaultRepository(tmp_path / "vault.db", VaultCipher(b"G" * 32))
    with result.transaction() as transaction:
        transaction.upsert_batch(
            [MappingCandidate(CODE, "CPF", "123.456.789-00", "CPF")]
        )
    return result


def test_restoration_widget_loads_headers_and_analyzes_offscreen(tmp_path: Path) -> None:
    vault = repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Tipo\n{CODE};WEB\n", encoding="utf-8")
    application = create_application([])
    widget = RestorationWidget(lambda: vault)

    widget.load_csv(str(source))
    widget._checkboxes[0].setChecked(True)
    widget.start_analysis()
    worker = widget._worker

    assert widget.path_field.isReadOnly()
    assert widget.table.rowCount() == 2
    assert worker is not None
    assert worker.wait(5000)
    application.processEvents()
    assert "Encontrados no cofre: 1" in widget.summary.toPlainText()
    assert "sem alterar o cofre" in widget.status_label.text()
    assert not widget.isVisible()

    widget.close()
    application.quit()


def test_restoration_runs_in_worker_and_shows_safe_summary(tmp_path: Path) -> None:
    vault = repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Tipo\n{CODE};WEB\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    application = create_application([])
    widget = RestorationWidget(lambda: vault)
    widget.load_csv(str(source))
    widget._checkboxes[0].setChecked(True)

    widget.start_restoration(destination, overwrite=False)
    worker = widget._worker

    assert worker is not None
    assert worker.wait(5000)
    application.processEvents()
    assert destination.exists()
    assert "CSV restaurado gerado" in widget.status_label.text()
    assert "123.456.789-00" not in widget.summary.toPlainText()
    assert "Códigos restaurados: 1" in widget.summary.toPlainText()

    widget.close()
    application.quit()


def test_sensitive_warning_requires_explicit_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    vault = repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Tipo\n{CODE};WEB\n", encoding="utf-8")
    application = create_application([])
    widget = RestorationWidget(lambda: vault)
    widget.load_csv(str(source))
    widget._checkboxes[0].setChecked(True)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )

    widget._choose_output()

    assert widget._worker is None
    widget.close()
    application.quit()


def test_existing_output_requires_overwrite_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    vault = repository(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text(f"CPF;Tipo\n{CODE};WEB\n", encoding="utf-8")
    destination = tmp_path / "existing.csv"
    destination.write_text("unchanged", encoding="utf-8")
    application = create_application([])
    widget = RestorationWidget(lambda: vault)
    widget.load_csv(str(source))
    widget._checkboxes[0].setChecked(True)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(destination), "Arquivos CSV (*.csv)"),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    widget._choose_output()

    assert widget._worker is None
    assert destination.read_text(encoding="utf-8") == "unchanged"
    widget.close()
    application.quit()
