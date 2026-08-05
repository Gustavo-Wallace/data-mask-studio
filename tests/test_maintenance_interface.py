import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QMessageBox

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths, create_backup
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.gui.maintenance_widget import MaintenanceWidget
from data_mask_studio.gui.maintenance_worker import (
    CompactionWorker,
    DiagnosticWorker,
    TemporaryCleanupWorker,
    TemporaryScanWorker,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

HMAC_KEY = b"H" * 32
VAULT_KEY = b"V" * 32
PASSWORD = "frase-senha longa de manutencao"
ORIGINAL = "123.456.789-00"
CANONICAL = "12345678900"


class FixedKeyProvider:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def get_key(self) -> bytes:
        return self.key


def paths_for(tmp_path: Path) -> EnvironmentPaths:
    directory = tmp_path / "local"
    directory.mkdir()
    return EnvironmentPaths(
        directory,
        directory / "secret.key",
        directory / "vault_key.dpapi",
        directory / "vault.db",
        directory / "profiles.json",
    )


def prepare(tmp_path: Path) -> tuple[EnvironmentPaths, str]:
    paths = paths_for(tmp_path)
    code = TokenGenerator(HMAC_KEY).generate("CPF", CANONICAL)
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    code,
                    "CPF",
                    ORIGINAL,
                    "CPF",
                    canonical_value=CANONICAL,
                    normalization_rule=NormalizationRule.CPF,
                )
            ]
        )
    return paths, code


def widget_for(paths: EnvironmentPaths) -> MaintenanceWidget:
    return MaintenanceWidget(
        paths,
        FixedKeyProvider(HMAC_KEY),
        FixedKeyProvider(VAULT_KEY),
    )


def wait(application, widget: MaintenanceWidget) -> None:
    worker = widget._worker
    assert worker is not None
    assert worker.wait(10000)
    application.processEvents()


def test_maintenance_widget_diagnostic_is_aggregated_and_safe(tmp_path: Path) -> None:
    application = create_application([])
    paths, code = prepare(tmp_path)
    widget = widget_for(paths)

    widget.start_diagnostic()
    wait(application, widget)

    text = widget.overview_output.toPlainText()
    assert "Estado geral: Saudável" in text
    assert "Mapeamentos: 1" in text
    assert ORIGINAL not in text
    assert CANONICAL not in text
    assert code not in text
    assert widget.copy_report_button.isEnabled()
    widget.copy_report_button.click()
    clipboard = application.clipboard().text()
    assert ORIGINAL not in clipboard and code not in clipboard
    widget.close()
    application.quit()


def test_backup_validation_clears_password_without_restoring(tmp_path: Path) -> None:
    application = create_application([])
    paths, _ = prepare(tmp_path)
    backup = tmp_path / "environment.dmsbackup"
    create_backup(
        backup,
        PASSWORD,
        PASSWORD,
        paths=paths,
        hmac_key_provider=FixedKeyProvider(HMAC_KEY),
        vault_key_provider=FixedKeyProvider(VAULT_KEY),
        app_version="0.8.0",
    )
    before = paths.vault_database_path.read_bytes()
    widget = widget_for(paths)
    widget.backup_path_field.setText(str(backup))
    widget.backup_password_field.setText(PASSWORD)

    widget.start_backup_validation()
    wait(application, widget)

    assert "Arquivo válido: sim" in widget.backup_result.toPlainText()
    assert widget.backup_password_field.text() == ""
    assert paths.vault_database_path.read_bytes() == before
    widget.close()
    application.quit()


def test_compaction_confirmation_runs_offscreen_and_emits_change(
    tmp_path: Path, monkeypatch
) -> None:
    application = create_application([])
    paths, _ = prepare(tmp_path)
    widget = widget_for(paths)
    changed: list[bool] = []
    widget.environment_changed.connect(lambda: changed.append(True))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    widget.start_compaction()
    wait(application, widget)

    assert changed == [True]
    assert "Tamanho anterior" in widget.compaction_result.toPlainText()
    assert VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).count() == 1
    widget.close()
    application.quit()


def test_workers_expose_required_signals() -> None:
    assert DiagnosticWorker.completed is not None
    assert DiagnosticWorker.progress is not None
    assert TemporaryScanWorker.completed is not None
    assert TemporaryCleanupWorker.completed is not None
    assert CompactionWorker.phase_changed is not None
    assert CompactionWorker.completed is not None


def test_main_window_has_maintenance_tab_and_blocks_other_operations(
    tmp_path: Path,
) -> None:
    application = create_application([])
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    window = MainWindow(profile_service=service)

    index = window.page_index(window.maintenance_widget)
    assert window.navigation.buttons[index].text() == "Cofre e manutenção"
    window._maintenance_busy_changed(True)
    assert window.navigation.page_enabled(index)
    assert all(
        not window.navigation.page_enabled(other)
        for other in range(len(window.navigation.buttons))
        if other != index
    )
    window._maintenance_busy_changed(False)
    assert all(
        window.navigation.page_enabled(other)
        for other in range(len(window.navigation.buttons))
    )
    window.close()
    application.quit()
