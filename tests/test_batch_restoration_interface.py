import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QMessageBox

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.app import create_application
from data_mask_studio.batch_restoration import BatchRestorationStatus
from data_mask_studio.gui.batch_restoration_widget import BatchRestorationWidget
from data_mask_studio.gui.batch_restoration_worker import (
    BatchRestorationAnalysisWorker,
    BatchRestorationProcessingWorker,
)
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

KEY = b"V" * 32
CODE = TokenGenerator(b"H" * 32).generate("CPF", "12345678900")


def repository(tmp_path: Path) -> VaultRepository:
    result = VaultRepository(tmp_path / "vault.db", VaultCipher(KEY))
    with result.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    CODE,
                    "CPF",
                    "123.456.789-00",
                    "CPF",
                    canonical_value="12345678900",
                    normalization_rule=NormalizationRule.CPF,
                )
            ]
        )
    return result


def test_batch_restoration_widget_analyzes_and_restores_offscreen(
    tmp_path: Path, monkeypatch
) -> None:
    application = create_application([])
    vault = repository(tmp_path)
    source = tmp_path / "dados.csv"
    source.write_text(f"CPF,Nome\n{CODE},Ana\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    widget = BatchRestorationWidget(lambda: vault.as_read_only())

    assert widget.add_paths([source, source]) == 1
    widget.analyze_files()
    analysis_worker = widget._analysis_worker
    assert analysis_worker is not None and analysis_worker.wait(5000)
    application.processEvents()

    assert widget.files[0].status is BatchRestorationStatus.COMPATIBLE
    assert widget.file_table.item(0, 2).text() == "utf-8"
    widget.file_table.selectRow(0)
    widget.select_candidate_columns()
    assert widget.files[0].columns[0].selected
    assert not widget.files[0].columns[1].selected
    widget.output_field.setText(str(output))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    widget.start_restoration()
    processing_worker = widget._processing_worker
    assert processing_worker is not None and processing_worker.wait(5000)
    application.processEvents()

    assert widget.files[0].status is BatchRestorationStatus.COMPLETED
    assert widget.files[0].output_path.is_file()
    assert "123.456.789-00" in widget.files[0].output_path.read_text(
        encoding="utf-8-sig"
    )
    assert widget.open_output_button.isEnabled()
    assert "Concluídos: 1" in widget.summary_output.toPlainText()

    widget.close()
    application.quit()


def test_batch_restoration_worker_signals_are_available() -> None:
    assert BatchRestorationAnalysisWorker.file_changed is not None
    assert BatchRestorationAnalysisWorker.completed is not None
    assert BatchRestorationProcessingWorker.progress is not None
    assert BatchRestorationProcessingWorker.completed is not None
    assert BatchRestorationProcessingWorker.failed is not None


def test_main_window_contains_batch_restoration_tab_and_global_block(
    tmp_path: Path,
) -> None:
    application = create_application([])
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    window = MainWindow(profile_service=service)

    index = window.page_index(window.batch_restoration_widget)
    assert index >= 0
    assert window.navigation.buttons[index].text() == "Restauração em lote"

    window._batch_restoration_busy_changed(True)
    assert window.navigation.page_enabled(index)
    assert all(
        not window.navigation.page_enabled(other)
        for other in range(len(window.navigation.buttons))
        if other != index
    )
    window._batch_restoration_busy_changed(False)
    assert all(
        window.navigation.page_enabled(other)
        for other in range(len(window.navigation.buttons))
    )

    window.close()
    application.quit()
