import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.integrity_widget import IntegrityWidget
from data_mask_studio.gui.integrity_worker import IntegrityWorker
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.profiles import ProfileRepository, ProfileService


class FixedKeyProvider:
    def __init__(self, key: bytes) -> None:
        self._key = key

    def get_key(self) -> bytes:
        return self._key


def paths_for(tmp_path: Path) -> EnvironmentPaths:
    local = tmp_path / "local"
    local.mkdir()
    return EnvironmentPaths(
        directory=local,
        hmac_key_path=local / "secret.key",
        vault_key_path=local / "vault_key.dpapi",
        vault_database_path=local / "vault.db",
        profiles_path=local / "profiles.json",
    )


def wait_for_worker(application: QApplication, widget: IntegrityWidget) -> None:
    worker = widget._worker
    assert worker is not None
    assert worker.wait(5000)
    application.processEvents()


def test_integrity_widget_runs_offscreen_and_copies_safe_report(
    tmp_path: Path,
) -> None:
    application = create_application([])
    widget = IntegrityWidget(
        paths_for(tmp_path), FixedKeyProvider(b"H" * 32), FixedKeyProvider(b"V" * 32)
    )

    widget.start_audit()
    wait_for_worker(application, widget)

    assert "Relatório de integridade" in widget.report_view.toPlainText()
    assert "ainda não executada" not in widget.last_check_label.text()
    assert widget.copy_button.isEnabled()
    assert widget.progress_bar.value() == 12
    widget.copy_button.click()
    assert QApplication.clipboard().text() == widget.report_view.toPlainText()

    widget.close()
    application.quit()


def test_integrity_worker_exposes_progress_and_cancellation_signals() -> None:
    assert IntegrityWorker.progress is not None
    assert IntegrityWorker.completed is not None
    assert IntegrityWorker.cancelled is not None
    assert IntegrityWorker.failed is not None


def test_integrity_busy_state_blocks_other_tabs(tmp_path: Path) -> None:
    application = create_application([])
    paths = paths_for(tmp_path)
    service = ProfileService(ProfileRepository(paths.profiles_path))
    window = MainWindow(
        key_provider=FixedKeyProvider(b"H" * 32),
        vault_key_provider=FixedKeyProvider(b"V" * 32),
        backup_paths=paths,
        profile_service=service,
    )

    window._integrity_busy_changed(True)
    integrity_index = window.tabs.indexOf(window.integrity_widget)
    assert window.tabs.isTabEnabled(integrity_index)
    assert all(
        not window.tabs.isTabEnabled(index)
        for index in range(window.tabs.count())
        if index != integrity_index
    )

    window._integrity_busy_changed(False)
    assert all(window.tabs.isTabEnabled(index) for index in range(window.tabs.count()))

    window.close()
    application.quit()
