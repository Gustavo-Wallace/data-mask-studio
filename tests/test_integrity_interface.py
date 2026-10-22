import os
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qt_lifecycle import qt_widget_lifecycle

from PySide6.QtWidgets import QApplication

from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.maintenance_widget import MaintenanceWidget
from data_mask_studio.gui.maintenance_worker import DiagnosticWorker
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.integrity import IntegrityStatus
from data_mask_studio.maintenance import safe_diagnostic_report
from data_mask_studio.profiles import ProfileRepository, ProfileService
from test_integrity import ORIGINAL, CANONICAL, prepare_valid_environment


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


def wait_for_worker(application: QApplication, widget: MaintenanceWidget) -> None:
    worker = widget._worker
    assert worker is not None
    assert worker.wait(5000)
    application.processEvents()


def test_integrity_diagnostic_runs_in_maintenance_and_copies_safe_report(
    tmp_path: Path,
) -> None:
    application = create_application([])
    paths, _, code = prepare_valid_environment(tmp_path)
    widget = MaintenanceWidget(
        paths, FixedKeyProvider(b"H" * 32), FixedKeyProvider(b"V" * 32)
    )

    widget.start_diagnostic()
    wait_for_worker(application, widget)

    result = widget._last_diagnostic
    assert result.audit.status is IntegrityStatus.INTACT
    assert "Integridade: ÍNTEGRO" in widget.overview_output.toPlainText()
    assert "ainda não executada" not in widget.last_audit_label.text()
    assert widget.copy_report_button.isEnabled()
    assert widget.progress.value() == widget.progress.maximum() == 1
    widget.copy_report_button.click()
    assert QApplication.clipboard().text() == safe_diagnostic_report(result)
    for check in result.audit.checks:
        assert check.check_type in widget.overview_output.toPlainText()
        assert check.check_type in QApplication.clipboard().text()
    for private in (ORIGINAL, CANONICAL, code, str(paths.directory)):
        assert private not in widget.overview_output.toPlainText()
        assert private not in QApplication.clipboard().text()

    widget.close()
    application.quit()


def test_diagnostic_worker_exposes_integrity_progress_and_cancellation_signals() -> None:
    assert DiagnosticWorker.progress is not None
    assert DiagnosticWorker.completed is not None
    assert DiagnosticWorker.cancelled is not None
    assert DiagnosticWorker.failed is not None


def test_integrity_diagnostic_busy_state_blocks_other_pages(tmp_path: Path, monkeypatch) -> None:
    application = create_application([])
    paths = paths_for(tmp_path)
    service = ProfileService(ProfileRepository(paths.profiles_path))
    window = MainWindow(
        key_provider=FixedKeyProvider(b"H" * 32),
        vault_key_provider=FixedKeyProvider(b"V" * 32),
        backup_paths=paths,
        profile_service=service,
    )

    widget = window.maintenance_widget
    entered, release = Event(), Event()
    original = widget._diagnostics.run

    def paused_diagnostic(**kwargs):
        entered.set()
        assert release.wait(10), "Test did not release the diagnostic"
        return original(**kwargs)

    monkeypatch.setattr(widget._diagnostics, "run", paused_diagnostic)
    window.set_current_page(window.page_index(widget))
    widget.start_diagnostic()
    worker = widget._worker
    try:
        assert entered.wait(5)
        assert window.navigation.page_enabled(window.page_index(widget))
        assert all(
            not window.navigation.page_enabled(index)
            for index in range(len(window.navigation.buttons))
            if index != window.page_index(widget)
        )
        assert not window._prepare_restore()
        assert not window._prepare_batch_restoration()
        window.set_current_page(0)
        assert window.current_page_index() == window.page_index(widget)
    finally:
        release.set()
        assert worker.wait(10000)
        application.processEvents()

    assert all(
        window.navigation.page_enabled(index)
        for index in range(len(window.navigation.buttons))
    )

    window.close()
    application.quit()
