"""Localização somente das ações padrão usadas nos diálogos do DMS."""

from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication


_BUTTON_LABELS = {
    "&Yes": "&Sim",
    "&No": "&Não",
    "Cancel": "Cancelar",
    "Close": "Fechar",
    "OK": "OK",
}


class _DialogButtonTranslator(QTranslator):
    def isEmpty(self) -> bool:
        # O catálogo está explícito em Python, não em um arquivo .qm.
        return False

    def translate(
        self, context: str, sourceText: str,
        disambiguation: str | None = None, n: int = -1,
    ) -> str | None:
        if context == "QPlatformTheme":
            return _BUTTON_LABELS.get(sourceText)
        # None representa QString nula: o Qt mantém seu fallback normal.
        return None


def install_dialog_button_translations(application: QApplication) -> None:
    """Instala uma única vez, antes dos widgets, sem traduzir o restante do Qt."""
    if application.findChild(_DialogButtonTranslator) is not None:
        return
    # O parent mantém o tradutor vivo durante toda a vida da QApplication.
    translator = _DialogButtonTranslator(application)
    application.installTranslator(translator)
