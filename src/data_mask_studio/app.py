import sys
from collections.abc import Sequence
from typing import cast

from PySide6.QtWidgets import QApplication

from data_mask_studio.gui.main_window import MainWindow


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Cria e configura a aplicação Qt."""
    existing_application = QApplication.instance()
    if existing_application is not None:
        return cast(QApplication, existing_application)

    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("Data Mask Studio")
    return application


def run() -> int:
    """Inicia a aplicação e executa o loop de eventos."""
    application = create_application()
    window = MainWindow()
    window.show()
    return application.exec()
