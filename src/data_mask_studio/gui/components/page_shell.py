from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class PageHeader(QFrame):
    """Cabeçalho compacto e consistente para uma página principal."""

    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageTitle")
        self.title_label.setAccessibleName(f"Título da página: {title}")
        self.description_label = QLabel(description)
        self.description_label.setObjectName("pageDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)


class PageShell(QWidget):
    """Mantém uma página existente viva em um viewport vertical adaptável."""

    def __init__(
        self,
        title: str,
        description: str,
        content: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageShell")
        self.header = PageHeader(title, description)
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
        layout.setSpacing(16)
        layout.addWidget(self.header)
        layout.addWidget(self.scroll_area, stretch=1)
