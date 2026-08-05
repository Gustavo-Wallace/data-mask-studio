import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.gui.column_configuration_table import ColumnConfigurationTable
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.gui.profile_controls import ProfileControls
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService


def test_main_window_composes_sidebar_pages_and_extracted_widgets(
    tmp_path: Path,
) -> None:
    application = create_application([])
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    window = MainWindow(profile_service=service)

    assert len(window.navigation.buttons) == 9
    assert window.page_widgets[0] is window.anonymization_widget
    assert isinstance(window.anonymization_widget, AnonymizationWidget)
    assert isinstance(window.config_table, ColumnConfigurationTable)
    assert isinstance(window.anonymization_widget.profile_controls, ProfileControls)
    assert window.centralWidget().objectName() == "mainWorkspace"
    assert window.page_stack.count() == 9
    assert "tabs" not in window.__dict__

    window.close()
    application.quit()


def test_profile_controls_refresh_individual_and_batch_integration(
    tmp_path: Path,
) -> None:
    application = create_application([])
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    window = MainWindow(profile_service=service)
    service.create(
        "Perfil integrado",
        [ColumnConfig("CPF", True, "CPF_ID", NormalizationRule.CPF)],
    )

    window.anonymization_widget.refresh_profiles()

    assert window.profile_combo.count() == 1
    assert window.batch_widget.profile_combo.count() == 1
    assert window.profile_combo.currentText() == "Perfil integrado"
    assert window.batch_widget.profile_combo.currentText() == "Perfil integrado"

    window.close()
    application.quit()


def test_individual_worker_exposes_main_signals_after_refactor() -> None:
    assert AnonymizationWorker.progress is not None
    assert AnonymizationWorker.completed is not None
    assert AnonymizationWorker.cancelled is not None
    assert AnonymizationWorker.failed is not None
