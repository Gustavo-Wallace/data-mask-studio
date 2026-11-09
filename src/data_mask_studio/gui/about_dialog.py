from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from data_mask_studio.metadata import application_version
from data_mask_studio.gui.components.presentation import configure_dialog_layout
from data_mask_studio.gui.styles import widget_palette
from data_mask_studio.i18n import Translator
from data_mask_studio.vault.database import SCHEMA_VERSION


REPOSITORY_URL = "https://github.com/Gustavo-Wallace/data-mask-studio"
SECURITY_URL = f"{REPOSITORY_URL}/blob/main/SECURITY.md"
PRIVACY_URL = f"{REPOSITORY_URL}/blob/main/PRIVACY.md"
COMPATIBILITY_URL = f"{REPOSITORY_URL}/blob/main/COMPATIBILITY.md"
RELEASES_URL = f"{REPOSITORY_URL}/releases"
PUBLIC_URLS = frozenset(
    (REPOSITORY_URL, SECURITY_URL, PRIVACY_URL, COMPATIBILITY_URL, RELEASES_URL)
)


class AboutDialog(QDialog):
    """Apresenta somente informações públicas e estáveis da aplicação."""

    def __init__(
        self, parent: QWidget | None = None, *, translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("aboutDialog")
        self.setModal(True)
        self.setMinimumWidth(430)

        title = QLabel("Data Mask Studio")
        title.setObjectName("aboutTitle")
        details = QLabel()
        details.setObjectName("aboutDetails")
        links = QLabel()
        links.setObjectName("aboutLinks")
        links.setOpenExternalLinks(False)
        links.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        links.linkActivated.connect(self._open_external_link)
        self.title_label = title
        self.details_label = details
        self.links_label = links
        copyright_label = QLabel()
        copyright_label.setObjectName("aboutCopyright")
        warning = QLabel()
        warning.setObjectName("aboutSignatureNotice")
        warning.setWordWrap(True)
        self.copyright_label = copyright_label
        self.warning_label = warning

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.buttons = buttons

        layout = QVBoxLayout(self)
        configure_dialog_layout(layout)
        layout.addWidget(title)
        layout.addWidget(details)
        layout.addWidget(links)
        layout.addWidget(copyright_label)
        layout.addWidget(warning)
        layout.addWidget(buttons)
        translator = translator if translator is not None else Translator("pt-BR")
        # MainWindow creates this dialog lazily; acquire public metadata once.
        self._application_version = application_version()
        self.retranslate(translator)

    def retranslate(self, translator: Translator) -> None:
        """Update presentation only, preserving widgets and standard button roles."""
        self._translator = translator
        title = translator.text("about.title", application=self.title_label.text())
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.details_label.setText("<br>".join((
            translator.html("about.version", version=self._application_version),
            translator.html("about.schema", schema=SCHEMA_VERSION),
            translator.html("about.local_processing"),
            translator.html("about.no_telemetry"),
        )))
        self._links_body = (
            f'<a href="{REPOSITORY_URL}">{translator.html("about.repository")}</a> &middot; '
            f'<a href="{SECURITY_URL}">{translator.html("about.security")}</a><br>'
            f'<a href="{PRIVACY_URL}">{translator.html("about.privacy")}</a> &middot; '
            f'<a href="{COMPATIBILITY_URL}">{translator.html("about.compatibility")}</a> &middot; '
            f'<a href="{RELEASES_URL}">{translator.html("about.releases")}</a>'
        )
        self._render_links(preserve_selection=False)
        self.copyright_label.setText(
            "Copyright © 2026 Gustavo Wallace Macedo Santos<br>"
            + translator.html("about.license", license="GNU GPL v3.0 only")
        )
        self.warning_label.setText(translator.text("about.signature_notice"))
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            translator.text("about.close")
        )

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "_links_body"):
            self._render_links()

    def _render_links(self, *, preserve_selection: bool = True) -> None:
        # QLabel rich text does not inherit QSS link-color or QPalette.Link.
        # Reuse escaped, cached presentation only; never acquire metadata here.
        markup = f'<style>a {{ color: {widget_palette(self).link_text}; }}</style>{self._links_body}'
        if markup == self.links_label.text():
            return
        start, selected = self.links_label.selectionStart(), self.links_label.selectedText()
        had_cursor = self.links_label.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
        cursor = self.links_label.cursor()
        self.links_label.setText(markup)
        if preserve_selection and had_cursor:
            self.links_label.setCursor(cursor)
        if preserve_selection and start >= 0:
            # QLabel's keyboard-focused anchor is also its public selection.
            # Qt cursor offsets count UTF-16 code units, not Python code points.
            self.links_label.setSelection(start, len(selected.encode("utf-16-le")) // 2)

    @staticmethod
    def _open_external_link(url: str) -> None:
        """Abre apenas endereços públicos conhecidos usando a integração do Qt."""
        if url in PUBLIC_URLS:
            QDesktopServices.openUrl(QUrl(url))
