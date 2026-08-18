import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QCheckBox, QLineEdit

from data_mask_studio.app import create_application
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.gui.styles import (
    APPLICATION_THEME_NAME,
    BASE_COLOR,
    TEXT_COLOR,
    WINDOW_COLOR,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository


class FixedKeyProvider:
    def get_key(self) -> bytes:
        return b"T" * 32


def profile_service(tmp_path: Path) -> ProfileService:
    return ProfileService(ProfileRepository(tmp_path / "profiles.json"))


def test_application_and_main_window_startup(tmp_path: Path) -> None:
    application = create_application([])
    window = MainWindow(profile_service=profile_service(tmp_path))

    assert application.applicationName() == "Data Mask Studio"
    assert window.windowTitle() == "Data Mask Studio"
    available = window.screen().availableGeometry()
    assert window.size().width() == min(1280, available.width())
    assert window.size().height() == min(820, available.height())
    assert window.path_field.isReadOnly()
    assert window.select_button.text() == "Selecionar CSV"
    assert window.clear_button.text() == "Limpar seleção"
    assert [button.text() for button in window.navigation.buttons] == [
        "Anonimizar CSV",
        "Anonimização em lote",
        "Restaurar CSV",
        "Restaurar HTML",
        "Restauração em lote",
        "Consultar cofre",
        "Backup e recuperação",
        "Integridade",
        "Cofre e manutenção",
    ]
    assert window.restoration_widget.path_field.isReadOnly()
    assert not window.restoration_widget.generate_button.isEnabled()
    assert not window.html_restoration_widget.generate_button.isEnabled()
    assert not window.backup_widget.restore_button.isEnabled()
    assert (
        window.backup_widget.create_password_field.echoMode()
        is QLineEdit.EchoMode.Password
    )
    assert not window.consultant_widget.copy_button.isEnabled()
    assert not window.integrity_widget.copy_button.isEnabled()
    assert not window.isVisible()

    window.close()
    application.quit()


def test_application_theme_overrides_system_palette_and_styles_all_windows() -> None:
    application = create_application([])
    simulated_light_palette = QPalette()
    simulated_light_palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    simulated_light_palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    application.setPalette(simulated_light_palette)

    configured_application = create_application([])
    palette = configured_application.palette()

    assert (
        configured_application.property("dataMaskStudioTheme")
        == APPLICATION_THEME_NAME
    )
    assert palette.color(QPalette.ColorRole.Window) == QColor(WINDOW_COLOR)
    assert palette.color(QPalette.ColorRole.WindowText) == QColor(TEXT_COLOR)
    assert palette.color(QPalette.ColorRole.Base) == QColor(BASE_COLOR)
    assert "QDialog" in configured_application.styleSheet()
    assert "QToolTip" in configured_application.styleSheet()
    assert "QScrollBar:vertical" in configured_application.styleSheet()


def test_checkbox_states_have_distinct_visual_indicators() -> None:
    application = create_application([])
    checkbox = QCheckBox("Coluna")
    checkbox.resize(120, 32)
    checkbox.show()
    application.processEvents()

    checkbox.setChecked(False)
    unchecked = checkbox.grab().toImage()
    checkbox.setChecked(True)
    checked = checkbox.grab().toImage()
    checkbox.setEnabled(False)
    disabled_checked = checkbox.grab().toImage()

    assert checked != unchecked
    assert disabled_checked != checked
    assert application.property("dataMaskStudioTheme") == APPLICATION_THEME_NAME

    checkbox.close()
    application.processEvents()


def test_window_displays_and_clears_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "people.csv"
    csv_path.write_text("name,age\nAna,30\n", encoding="utf-8")
    application = create_application([])
    window = MainWindow(profile_service=profile_service(tmp_path))

    window.load_csv(str(csv_path))

    assert window.file_name_label.text() == "people.csv"
    assert window.path_field.text() == str(csv_path.resolve())
    assert window.column_count_label.text() == "2"
    assert window.config_table.rowCount() == 2
    assert window.config_table.item(0, 1).text() == "name"
    assert window.config_table.item(1, 1).text() == "age"
    assert window.config_table.columnCount() == 5
    assert window._normalization_fields[0].currentText() == "Valor exato"
    assert not window._normalization_fields[0].isEnabled()
    assert window.status_label.text() == "Cabeçalhos lidos com sucesso."

    window.clear_button.click()

    assert window.path_field.text() == ""
    assert window.config_table.rowCount() == 0
    assert not window.clear_button.isEnabled()

    window.close()
    application.quit()


def test_window_configures_and_validates_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "people.csv"
    csv_path.write_text("Nome Completo,CPF/ID\nAna,123\n", encoding="utf-8")
    application = create_application([])
    window = MainWindow(profile_service=profile_service(tmp_path))
    window.load_csv(str(csv_path))

    window.select_all_button.click()

    assert window._prefix_fields[0].text() == "NOME_COMPLETO"
    assert window._prefix_fields[1].text() == "CPF_ID"
    assert window.selected_count_label.text() == "2 de 2 colunas selecionadas"
    assert window._normalization_fields[0].isEnabled()
    cpf_index = window._normalization_fields[0].findData(NormalizationRule.CPF.value)
    window._normalization_fields[0].setCurrentIndex(cpf_index)
    assert window._column_configs[0].normalization_rule is NormalizationRule.CPF
    window._normalization_fields[0].setCurrentIndex(0)

    window.validate_button.click()

    assert window.status_label.text() == "Configuração válida para 2 colunas."
    assert window.generate_button.isEnabled()

    window._prefix_fields[0].setText("INVALID-prefix")

    assert not window.generate_button.isEnabled()

    window._prefix_fields[0].setText("NOME_COMPLETO")
    window.validate_button.click()

    window.unselect_all_button.click()
    window.validate_button.click()

    assert "ao menos uma coluna" in window.status_label.text()

    window.close()
    application.quit()


def test_loading_another_csv_discards_column_configuration(tmp_path: Path) -> None:
    first_csv = tmp_path / "first.csv"
    first_csv.write_text("Nome,CPF\nAna,123\n", encoding="utf-8")
    second_csv = tmp_path / "second.csv"
    second_csv.write_text("Cidade,Estado\nRecife,PE\n", encoding="utf-8")
    application = create_application([])
    window = MainWindow(profile_service=profile_service(tmp_path))
    window.load_csv(str(first_csv))
    window.select_all_button.click()
    window._prefix_fields[0].setText("PESSOA")

    window.load_csv(str(second_csv))

    assert window.config_table.rowCount() == 2
    assert window.config_table.item(0, 1).text() == "Cidade"
    assert window.config_table.item(1, 1).text() == "Estado"
    assert all(not checkbox.isChecked() for checkbox in window._checkboxes)
    assert all(not field.isEnabled() for field in window._prefix_fields)
    assert all(field.text() == "" for field in window._prefix_fields)

    window.close()
    application.quit()


def test_window_generates_csv_in_background(tmp_path: Path) -> None:
    csv_path = tmp_path / "people.csv"
    csv_path.write_text("name,age\nAna,30\nBruna,40\n", encoding="utf-8")
    output_path = tmp_path / "people_anonymized.csv"
    application = create_application([])
    window = MainWindow(
        key_provider=FixedKeyProvider(),
        vault_repository_factory=lambda: VaultRepository(
            tmp_path / "vault.db", VaultCipher(b"V" * 32)
        ),
        profile_service=profile_service(tmp_path),
    )
    window.load_csv(str(csv_path))
    window._checkboxes[0].setChecked(True)
    window.validate_button.click()

    window._start_processing(output_path, overwrite=False)
    worker = window._worker

    assert worker is not None
    assert worker.wait(5000)
    application.processEvents()

    assert output_path.exists()
    assert "gerado com sucesso" in window.status_label.text()
    assert str(output_path) in window.output_path_label.text()
    assert window.processed_count_label.text() == "2 registros processados"
    assert not window.open_folder_button.isHidden()
    assert "2 novos mapeamentos" in window.status_label.text()
    assert "cofre local foi atualizado" in window.status_label.text()

    window.close()
    application.quit()


def test_window_reports_structured_normalization_fallback_without_value(
    tmp_path: Path,
) -> None:
    sensitive_value = "unknown"
    csv_path = tmp_path / "addresses.csv"
    csv_path.write_text(
        f"IP,Extra\n192.0.2.1,a\n{sensitive_value},b\n198.51.100.2,c\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "addresses_anonymized.csv"
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    application = create_application([])
    window = MainWindow(
        key_provider=FixedKeyProvider(),
        vault_repository_factory=lambda: repository,
        profile_service=profile_service(tmp_path),
    )
    window.load_csv(str(csv_path))
    window._checkboxes[0].setChecked(True)
    normalization = window._normalization_fields[0]
    normalization.setCurrentIndex(
        normalization.findData(NormalizationRule.IP_ADDRESS.value)
    )
    window.validate_button.click()

    window._start_processing(output_path, overwrite=False)
    worker = window._worker
    assert worker is not None and worker.wait(5000)
    application.processEvents()

    status = window.status_label.text()
    assert output_path.exists()
    assert "anonimizados por valor exato" in status
    assert "IP: 1 fallback(s)" in status
    assert sensitive_value not in status

    window.close()
    application.quit()
