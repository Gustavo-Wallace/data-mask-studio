import sys
import ctypes
from pathlib import Path
from collections.abc import Sequence
from typing import cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.gui.dialog_localization import install_dialog_button_translations, localize_standard_buttons
from data_mask_studio.gui.styles import apply_application_theme
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.gui.theme_resolution import resolve_theme
from data_mask_studio.publication import PublicationError
from data_mask_studio.environment import EnvironmentError
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService

APP_USER_MODEL_ID = "com.gustavowallace.datamaskstudio"


def create_application(
    argv: Sequence[str] | None = None, *, defer_button_translation: bool = False,
) -> QApplication:
    """Cria o Qt; o bootstrap pode adiar botões até resolver o idioma."""
    _set_windows_app_user_model_id()
    existing_application = QApplication.instance()
    if existing_application is not None:
        application = cast(QApplication, existing_application)
        _configure_application(application, defer_button_translation=defer_button_translation)
        return application

    application = QApplication(list(argv) if argv is not None else sys.argv)
    _configure_application(application, defer_button_translation=defer_button_translation)
    return application


def _configure_application(application: QApplication, *, defer_button_translation: bool = False) -> None:
    application.setApplicationName("Data Mask Studio")
    if not defer_button_translation:
        install_dialog_button_translations(application)
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


def run(
    *, translator: Translator | None = None, preferences_service: PreferencesService | None = None,
) -> int:
    """Apply startup appearance; allow live language changes in normal sessions."""
    application = create_application(defer_button_translation=True)
    preferences = preferences_service if preferences_service is not None else PreferencesService()
    stored = preferences.load()
    colors = LIGHT_PALETTE if resolve_theme(stored.preferences.theme) == "light" else DARK_PALETTE
    # Apply the authoritative startup choice before constructing any windows,
    # never from Settings or OS change signals.
    apply_application_theme(application, colors)
    if translator is None:
        effective_translator = Translator(preferences.resolve_language(stored.preferences.language))
    else:
        effective_translator = translator
    install_dialog_button_translations(application, translator=effective_translator)
    try:
        window = MainWindow(
            translator=effective_translator, preferences_service=preferences,
            live_language_switching=translator is None,
        )
    except (PublicationError, EnvironmentError) as error:
        title_id = "startup.error.environment_title" if isinstance(error, EnvironmentError) else "startup.error.publication_title"
        title = effective_translator.text(title_id)
        # Service-owned safe details remain literal; never expose the exception cause.
        text = str(error)
        message = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.Ok)
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setAccessibleName(title)
        message.setAccessibleDescription(text)
        localize_standard_buttons(message, effective_translator)
        message.setDefaultButton(QMessageBox.StandardButton.Ok)
        message.setEscapeButton(QMessageBox.StandardButton.Ok)
        try:
            message.exec()
        finally:
            message.deleteLater()
        return 1
    window.show()
    return application.exec()
