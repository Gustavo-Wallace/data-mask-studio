from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.i18n import Translator


class PageHeader(QFrame):
    """Cabeçalho compacto e consistente para uma página principal."""

    def __init__(
        self, title: str, description: str, parent: QWidget | None = None,
        *, translator: Translator | None = None,
        title_message_id: str | None = None,
        description_message_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        self._title = title
        self._description = description
        self._title_message_id = title_message_id
        self._description_message_id = description_message_id
        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageTitle")
        self.description_label = QLabel(description)
        self.description_label.setObjectName("pageDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS["header_text_spacing"])
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        self.retranslate(translator or Translator("pt-BR"))

    def retranslate(self, translator: Translator) -> None:
        title = (
            translator.text(self._title_message_id)
            if self._title_message_id else self._title
        )
        description = (
            translator.text(self._description_message_id)
            if self._description_message_id else self._description
        )
        self.title_label.setText(title)
        self.title_label.setAccessibleName(translator.text(
            "navigation.page_title_accessible", title=title,
        ))
        self.description_label.setText(description)


class PageShell(QWidget):
    """Mantém uma página existente viva em um viewport vertical adaptável."""

    def __init__(
        self,
        title: str,
        description: str,
        content: QWidget,
        parent: QWidget | None = None,
        *,
        translator: Translator | None = None,
        title_message_id: str | None = None,
        description_message_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageShell")
        self.header = PageHeader(
            title, description, translator=translator,
            title_message_id=title_message_id,
            description_message_id=description_message_id,
        )
        self.content = content
        self.content.setProperty("pageContent", True)
        content_layout = content.layout()
        if content_layout is not None:
            content_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("pageScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setWidget(content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(METRICS["header_content_spacing"])
        layout.addWidget(self.header)
        layout.addWidget(self.scroll_area, stretch=1)

    def retranslate(self, translator: Translator) -> None:
        self.header.retranslate(translator)
