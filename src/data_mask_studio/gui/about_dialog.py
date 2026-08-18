from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from data_mask_studio.metadata import application_version
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aboutDialog")
        self.setWindowTitle("Sobre o Data Mask Studio")
        self.setAccessibleName("Sobre o Data Mask Studio")
        self.setModal(True)
        self.setMinimumWidth(430)

        title = QLabel("Data Mask Studio")
        title.setObjectName("aboutTitle")
        details = QLabel(
            f"Versão {application_version()}<br>"
            f"Schema suportado: {SCHEMA_VERSION}<br>"
            "Processamento local<br>"
            "Sem telemetria"
        )
        details.setObjectName("aboutDetails")
        links = QLabel(
            f'<a href="{REPOSITORY_URL}">Repositório</a> &middot; '
            f'<a href="{SECURITY_URL}">Política de segurança</a><br>'
            f'<a href="{PRIVACY_URL}">Privacidade</a> &middot; '
            f'<a href="{COMPATIBILITY_URL}">Compatibilidade</a> &middot; '
            f'<a href="{RELEASES_URL}">Releases</a>'
        )
        links.setObjectName("aboutLinks")
        links.setOpenExternalLinks(False)
        links.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        links.linkActivated.connect(self._open_external_link)
        self.links_label = links
        copyright_label = QLabel(
            "Copyright © 2026 Gustavo Wallace Macedo Santos<br>"
            "Licenciado sob GNU GPL v3.0 only."
        )
        copyright_label.setObjectName("aboutCopyright")
        warning = QLabel(
            "Os executáveis distribuídos ainda não possuem assinatura digital."
        )
        warning.setObjectName("aboutSignatureNotice")
        warning.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(details)
        layout.addWidget(links)
        layout.addWidget(copyright_label)
        layout.addWidget(warning)
        layout.addWidget(buttons)

    @staticmethod
    def _open_external_link(url: str) -> None:
        """Abre apenas endereços públicos conhecidos usando a integração do Qt."""
        if url in PUBLIC_URLS:
            QDesktopServices.openUrl(QUrl(url))
