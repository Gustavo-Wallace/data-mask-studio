import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository


class FixedKeyProvider:
    def get_key(self) -> bytes:
        return b"I" * 32


def test_batch_tab_validates_and_processes_offscreen(tmp_path: Path) -> None:
    profile_service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile_service.create(
        "Perfil em lote",
        [ColumnConfig("Nome", True, "NOME", NormalizationRule.EXACT)],
    )
    source = tmp_path / "data.csv"
    source.write_text("Nome,Extra\nAna,x\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    application = create_application([])
    window = MainWindow(
        key_provider=FixedKeyProvider(),
        vault_repository_factory=lambda: VaultRepository(
            tmp_path / "vault.db", VaultCipher(b"J" * 32)
        ),
        profile_service=profile_service,
    )
    batch = window.batch_widget

    assert window.tabs.tabText(1) == "Anonimização em lote"
    assert batch.add_paths([source, source]) == 1
    assert batch.file_table.rowCount() == 1
    batch.validate_files()
    validation_worker = batch._validation_worker
    assert validation_worker is not None and validation_worker.wait(5000)
    application.processEvents()

    assert batch.files[0].status.value == "compatible"
    batch.output_field.setText(str(output))
    batch.start_processing()
    processing_worker = batch._processing_worker
    assert processing_worker is not None and processing_worker.wait(5000)
    application.processEvents()

    assert batch.files[0].status.value == "completed"
    assert (output / "data_anonimizado.csv").exists()
    assert "Arquivos concluídos: 1" in batch.summary_output.toPlainText()
    assert batch.open_output_button.isEnabled()

    window.close()
    application.quit()


def test_changing_profile_invalidates_batch_validation(tmp_path: Path) -> None:
    profile_service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile_service.create(
        "Perfil A",
        [ColumnConfig("Nome", True, "NOME", NormalizationRule.EXACT)],
    )
    profile_service.create(
        "Perfil B",
        [ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF)],
    )
    source = tmp_path / "data.csv"
    source.write_text("Nome,CPF\nAna,12345678900\n", encoding="utf-8")
    application = create_application([])
    window = MainWindow(profile_service=profile_service)
    batch = window.batch_widget
    batch.add_paths([source])
    batch.validate_files()
    worker = batch._validation_worker
    assert worker is not None and worker.wait(5000)
    application.processEvents()
    assert batch.files[0].status.value == "compatible"

    batch.profile_combo.setCurrentIndex(1)

    assert batch.files[0].status.value == "pending"
    assert not batch.start_button.isEnabled()

    window.close()
    application.quit()


def test_batch_interface_reports_aggregated_fallback_without_value(
    tmp_path: Path,
) -> None:
    sensitive_value = "private-invalid-ip"
    profile_service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile_service.create(
        "IPs",
        [ColumnConfig("IP", True, "IP", NormalizationRule.IP_ADDRESS)],
    )
    source = tmp_path / "addresses.csv"
    source.write_text(
        f"IP,Extra\n192.0.2.1,a\n{sensitive_value},b\n198.51.100.2,c\n",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    output.mkdir()
    application = create_application([])
    window = MainWindow(
        key_provider=FixedKeyProvider(),
        vault_repository_factory=lambda: VaultRepository(
            tmp_path / "vault.db", VaultCipher(b"J" * 32)
        ),
        profile_service=profile_service,
    )
    batch = window.batch_widget
    batch.add_paths([source])
    batch.validate_files()
    assert batch._validation_worker is not None
    assert batch._validation_worker.wait(5000)
    application.processEvents()
    batch.output_field.setText(str(output))
    batch.start_processing()
    assert batch._processing_worker is not None
    assert batch._processing_worker.wait(5000)
    application.processEvents()

    summary = batch.summary_output.toPlainText()
    assert batch.files[0].status.value == "completed"
    assert "IP: 1 fallback(s)" in summary
    assert sensitive_value not in summary
    assert (output / "addresses_anonimizado.csv").exists()

    window.close()
    application.quit()
