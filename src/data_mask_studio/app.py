import sys
from pathlib import Path
from collections.abc import Sequence
from typing import cast

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from data_mask_studio.gui.main_window import MainWindow


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Cria e configura a aplicação Qt."""
    existing_application = QApplication.instance()
    if existing_application is not None:
        return cast(QApplication, existing_application)

    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("Data Mask Studio")
    icon_path = _application_icon_path()
    if icon_path is not None:
        application.setWindowIcon(QIcon(str(icon_path)))
    return application


def _application_icon_path() -> Path | None:
    bundle_root = getattr(sys, "_MEIPASS", None)
    candidates = []
    if bundle_root is not None:
        candidates.append(Path(bundle_root) / "assets" / "data-mask-studio.ico")
    candidates.append(
        Path(__file__).resolve().parents[2]
        / "packaging"
        / "windows"
        / "assets"
        / "data-mask-studio.ico"
    )
    return next((path for path in candidates if path.is_file()), None)


def run() -> int:
    """Inicia a aplicação e executa o loop de eventos."""
    application = create_application()
    window = MainWindow()
    window.show()
    return application.exec()
