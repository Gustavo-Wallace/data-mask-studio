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
    assert window.headers_list.count() == 2
    assert window.status_label.text() == "Cabeçalhos lidos com sucesso."

    window.clear_selection()

    assert window.path_field.text() == ""
    assert window.headers_list.count() == 0
    assert not window.clear_button.isEnabled()

    window.close()
    application.quit()
