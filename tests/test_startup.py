import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data_mask_studio.app import create_application
from data_mask_studio.gui.main_window import MainWindow


def test_application_and_main_window_startup() -> None:
    application = create_application([])
    window = MainWindow()

    assert application.applicationName() == "Data Mask Studio"
    assert window.windowTitle() == "Data Mask Studio"
    assert window.size().width() == 900
    assert window.size().height() == 600
    assert window.path_field.isReadOnly()
    assert window.select_button.text() == "Selecionar CSV"
    assert window.clear_button.text() == "Limpar seleção"
    assert not window.isVisible()

    window.close()
    application.quit()


def test_window_displays_and_clears_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "people.csv"
    csv_path.write_text("name,age\nAna,30\n", encoding="utf-8")
    application = create_application([])
    window = MainWindow()

    window.load_csv(str(csv_path))

    assert window.file_name_label.text() == "people.csv"
    assert window.path_field.text() == str(csv_path.resolve())
    assert window.column_count_label.text() == "2"
    assert window.config_table.rowCount() == 2
    assert window.config_table.item(0, 1).text() == "name"
    assert window.config_table.item(1, 1).text() == "age"
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
    window = MainWindow()
    window.load_csv(str(csv_path))

    window.select_all_button.click()

    assert window._prefix_fields[0].text() == "NOME_COMPLETO"
    assert window._prefix_fields[1].text() == "CPF_ID"
    assert window.selected_count_label.text() == "2 de 2 colunas selecionadas"

    window.validate_button.click()

    assert window.status_label.text() == "Configuração válida para 2 colunas."

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
    window = MainWindow()
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
