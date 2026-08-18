import sys
import ctypes
from pathlib import Path
from collections.abc import Sequence
from typing import cast

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.gui.styles import apply_application_theme

APP_USER_MODEL_ID = "com.gustavowallace.datamaskstudio"


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Cria e configura a aplicação Qt."""
    _set_windows_app_user_model_id()
    existing_application = QApplication.instance()
    if existing_application is not None:
        application = cast(QApplication, existing_application)
        _configure_application(application)
        return application

    application = QApplication(list(argv) if argv is not None else sys.argv)
    _configure_application(application)
    return application


def _configure_application(application: QApplication) -> None:
    application.setApplicationName("Data Mask Studio")
    apply_application_theme(application)
    icon_path = _application_icon_path()
    if icon_path is not None:
        application.setWindowIcon(QIcon(str(icon_path)))


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
        APP_USER_MODEL_ID
    )
    if result != 0:
        raise OSError(f"Não foi possível configurar o AppUserModelID ({result}).")


def _application_icon_path() -> Path | None:
    bundle_root = getattr(sys, "_MEIPASS", None)
    candidates = []
    if bundle_root is not None:
        candidates.append(
            Path(bundle_root) / "assets" / "branding" / "dms_icon.ico"
        )
    candidates.append(
        Path(__file__).resolve().parents[2] / "assets" / "branding" / "dms_icon.ico"
    )
    return next((path for path in candidates if path.is_file()), None)


def run() -> int:
    """Inicia a aplicação e executa o loop de eventos."""
    application = create_application()
    window = MainWindow()
    window.show()
    return application.exec()
