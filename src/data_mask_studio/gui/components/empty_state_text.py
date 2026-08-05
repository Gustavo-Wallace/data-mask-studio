from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget


class _EmptyStateMixin:
    empty_text: str

    def _initialize_empty_state(self, empty_text: str) -> None:
        self.empty_text = empty_text
        self.setAccessibleDescription(empty_text)

    def _paint_empty_state(self) -> None:
        if self.toPlainText():
            return
        painter = QPainter(self.viewport())
        painter.setPen(QColor("#aebccc"))
        painter.drawText(
            self.viewport().rect().adjusted(20, 20, -20, -20),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self.empty_text,
        )


class EmptyStateTextEdit(_EmptyStateMixin, QTextEdit):
    def __init__(self, empty_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._initialize_empty_state(empty_text)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        self._paint_empty_state()


class EmptyStatePlainTextEdit(_EmptyStateMixin, QPlainTextEdit):
    def __init__(self, empty_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._initialize_empty_state(empty_text)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        self._paint_empty_state()
