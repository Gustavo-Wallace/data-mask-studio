"""Localização somente das ações padrão usadas nos diálogos do DMS."""

from PySide6.QtCore import QObject, QTranslator
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from data_mask_studio.i18n import Translator


_QT_BUTTON_MESSAGE_IDS = {
    "&Yes": "dialog.button.yes",
    "&No": "dialog.button.no",
    "Cancel": "dialog.button.cancel",
    "Close": "dialog.button.close",
    "OK": "dialog.button.ok",
    # Current Qt uses Retry; retain the mnemonic form for platform variants.
    "Retry": "dialog.button.retry",
    "&Retry": "dialog.button.retry",
}

_STANDARD_BUTTON_MESSAGE_IDS = (
    ("Yes", "dialog.button.yes"),
    ("No", "dialog.button.no"),
    ("Cancel", "dialog.button.cancel"),
    ("Close", "dialog.button.close"),
    ("Ok", "dialog.button.ok"),
    ("Retry", "dialog.button.retry"),
)


class _DialogButtonTranslator(QTranslator):
    def __init__(self, parent: QObject | None = None, *, translator: Translator | None = None) -> None:
        super().__init__(parent)
        # Normal startup remains Portuguese, independent of stored preferences.
        self._translator = translator if translator is not None else Translator("pt-BR")

    def isEmpty(self) -> bool:
        # Qt calls translate(); the application owns the versioned JSON catalogs.
        return False

    def translate(
        self, context: str, sourceText: str,
        disambiguation: str | None = None, n: int = -1,
    ) -> str | None:
        if context == "QPlatformTheme" and sourceText in _QT_BUTTON_MESSAGE_IDS:
            return self._translator.text(_QT_BUTTON_MESSAGE_IDS[sourceText])
        # None representa QString nula: o Qt mantém seu fallback normal.
        return None


def localize_standard_buttons(dialog: QMessageBox | QDialogButtonBox, translator: Translator) -> None:
    """Relabel existing semantic buttons locally, without changing Qt's locale.

    Labels only: roles, return values, ownership, connections, default and Escape
    selection remain owned by the original Qt dialog/button box.
    """
    if not isinstance(dialog, (QMessageBox, QDialogButtonBox)):
        raise TypeError("Expected a Qt message box or dialog button box.")
    for name, message_id in _STANDARD_BUTTON_MESSAGE_IDS:
        button = dialog.button(getattr(type(dialog).StandardButton, name))
        if button is not None:
            button.setText(translator.text(message_id))


def install_dialog_button_translations(application: QApplication) -> None:
    """Instala uma única vez, antes dos widgets, sem traduzir o restante do Qt."""
    if application.findChild(_DialogButtonTranslator) is not None:
        return
    # O parent mantém o tradutor vivo durante toda a vida da QApplication.
    translator = _DialogButtonTranslator(application)
    application.installTranslator(translator)
