import os

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

    window.close()
    application.quit()
