import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QFileDialog, QMessageBox

from data_mask_studio.app import create_application
from data_mask_studio.gui.html_restoration_widget import (
    HTMLAnalysisWorker,
    HTMLRestorationWidget,
    HTMLRestorationWorker,
)
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

CODE = "CPF-ABCDEFGHI234"


def make_repository(tmp_path: Path) -> VaultRepository:
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"I" * 32))
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [MappingCandidate(CODE, "CPF", "123.456.789-00", "CPF")]
        )
    return repository


def test_html_widget_analyzes_offscreen(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CODE}</p><span>{CODE}</span>", encoding="utf-8")
    application = create_application([])
    widget = HTMLRestorationWidget(lambda: repository)

    widget.load_html(str(source))
    widget.start_analysis()
    worker = widget._worker

    assert widget.path_field.isReadOnly()
    assert widget.encoding_label.text() == "utf-8"
    assert worker is not None
    assert worker.wait(5000)
    application.processEvents()
    assert "Códigos únicos encontrados: 1" in widget.summary.toPlainText()
    assert "Total de ocorrências: 2" in widget.summary.toPlainText()
    assert widget.progress_bar.value() == 100
    assert not widget.isVisible()
    widget.close()
    application.quit()


def test_selecting_another_html_resets_completed_analysis_progress(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    first.write_text(f"<p>{CODE}</p>", encoding="utf-8")
    second.write_text("<p>conteúdo sintético</p>", encoding="utf-8")
    application = create_application([])
    widget = HTMLRestorationWidget(lambda: repository)

    widget.load_html(str(first))
    widget.start_analysis()
    assert widget._worker is not None and widget._worker.wait(5000)
    application.processEvents()
    assert widget.progress_bar.value() == 100

    widget.load_html(str(second))

    assert widget.progress_bar.value() == 0
    assert widget.progress_bar.isHidden()
    assert widget.progress_label.text() == ""
    assert widget.progress_label.isHidden()
    assert "inspecionado com sucesso" in widget.status_label.text()
    widget.close()
    application.quit()


def test_new_analysis_and_restoration_do_not_inherit_completed_progress(
    tmp_path: Path, monkeypatch
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CODE}</p>", encoding="utf-8")
    application = create_application([])
    widget = HTMLRestorationWidget(lambda: repository)
    widget.load_html(str(source))
    widget.start_analysis()
    assert widget._worker is not None and widget._worker.wait(5000)
    application.processEvents()
    assert widget.progress_bar.value() == 100

    monkeypatch.setattr(HTMLAnalysisWorker, "start", lambda _self: None)
    widget.start_analysis()
    analysis_worker = widget._worker
    assert analysis_worker is not None
    assert widget.progress_bar.value() == 0
    assert widget.progress_label.text() == "0 ocorrências processadas"
    assert not widget.progress_bar.isHidden()
    widget._worker = None
    analysis_worker.deleteLater()

    widget.progress_bar.setValue(100)
    monkeypatch.setattr(HTMLRestorationWorker, "start", lambda _self: None)
    widget.start_restoration(tmp_path / "output.html", overwrite=False)
    restoration_worker = widget._worker
    assert restoration_worker is not None
    assert widget.progress_bar.value() == 0
    assert widget.progress_label.text() == "0 ocorrências processadas"
    assert "Gerando HTML restaurado" in widget.status_label.text()
    widget._worker = None
    restoration_worker.deleteLater()
    application.processEvents()
    widget.close()
    application.quit()


def test_cancelled_html_operation_clears_progress_state(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    application = create_application([])
    widget = HTMLRestorationWidget(lambda: repository)
    widget.progress_bar.setValue(100)
    widget.progress_bar.setVisible(True)
    widget.progress_label.setText("estado anterior")
    widget.progress_label.setVisible(True)

    widget._cancelled()

    assert widget.progress_bar.value() == 0
    assert widget.progress_bar.isHidden()
    assert widget.progress_label.text() == ""
    assert widget.progress_label.isHidden()
    assert "cancelada com segurança" in widget.status_label.text()
    widget.close()
    application.quit()


def test_html_widget_restores_in_worker_without_showing_values(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CODE}</p>", encoding="utf-8")
    destination = tmp_path / "output.html"
    application = create_application([])
    widget = HTMLRestorationWidget(lambda: repository)
    widget.load_html(str(source))

    widget.start_restoration(destination, overwrite=False)
    worker = widget._worker

    assert worker is not None
    assert worker.wait(5000)
    application.processEvents()
    assert destination.exists()
    assert "HTML restaurado gerado" in widget.status_label.text()
    assert "123.456.789-00" not in widget.summary.toPlainText()
    assert "Ocorrências restauradas: 1" in widget.summary.toPlainText()
    widget.close()
    application.quit()


def test_html_sensitive_warning_requires_explicit_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CODE}</p>", encoding="utf-8")
    application = create_application([])
    widget = HTMLRestorationWidget(lambda: repository)
    widget.load_html(str(source))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )

    widget._choose_output()

    assert widget._worker is None
    widget.close()
    application.quit()


def test_html_existing_output_requires_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CODE}</p>", encoding="utf-8")
    destination = tmp_path / "existing.html"
    destination.write_text("unchanged", encoding="utf-8")
    application = create_application([])
    widget = HTMLRestorationWidget(lambda: repository)
    widget.load_html(str(source))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(destination), "Arquivos HTML (*.html)"),
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
