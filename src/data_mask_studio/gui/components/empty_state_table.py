from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QTableWidget, QWidget

from data_mask_studio.gui.styles import widget_palette


class EmptyStateTable(QTableWidget):
    """Tabela que comunica um estado vazio sem criar linhas artificiais."""

    def __init__(
        self,
        rows: int,
        columns: int,
        empty_text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(rows, columns, parent)
        self.empty_text = empty_text
        self.setAccessibleDescription(empty_text)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        if self._has_visible_rows():
            return
        painter = QPainter(self.viewport())
        painter.setPen(QColor(widget_palette(self).muted_text))
        painter.drawText(
            self.viewport().rect().adjusted(20, 20, -20, -20),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self.empty_text,
        )

    def _has_visible_rows(self) -> bool:
        return self.rowCount() != 0
