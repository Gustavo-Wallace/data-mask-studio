from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle

from data_mask_studio.gui.components.empty_state_table import EmptyStateTable

PREFIX_PLACEHOLDER = "Disponível ao mascarar"
HEADER_SPACE_SHARE = 0.62


class ColumnConfigurationTable(EmptyStateTable):
    """Tabela visual da configuração das colunas do CSV."""

    def __init__(self, parent=None) -> None:
        super().__init__(
            0,
            4,
            "Selecione um CSV para configurar as colunas.",
            parent,
        )
        self.setHorizontalHeaderLabels(
            ["Ação", "Cabeçalho", "Prefixo", "Normalização"]
        )
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.sectionResized.connect(self._fixed_section_resized)
        self._resizing_flexible_sections = False
        self._resize_flexible_sections()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._resize_flexible_sections()

    def _fixed_section_resized(
        self, logical_index: int, _old_size: int, _new_size: int
    ) -> None:
        if logical_index in (0, 3):
            self._resize_flexible_sections()

    def _resize_flexible_sections(self) -> None:
        if self._resizing_flexible_sections:
            return

        header = self.horizontalHeader()
        fixed_width = header.sectionSize(0) + header.sectionSize(3)
        available_width = self.viewport().width() - fixed_width
        font_metrics = self.fontMetrics()
        text_padding = font_metrics.horizontalAdvance("MM")
        frame_width = 2 * self.style().pixelMetric(
            QStyle.PixelMetric.PM_DefaultFrameWidth
        )
        header_minimum = header.sectionSizeHint(1)
        prefix_minimum = (
            font_metrics.horizontalAdvance(PREFIX_PLACEHOLDER)
            + text_padding
            + frame_width
        )
        flexible_width = max(
            available_width,
            header_minimum + prefix_minimum,
        )
        header_width = max(
            header_minimum,
            round(flexible_width * HEADER_SPACE_SHARE),
        )
        prefix_width = flexible_width - header_width
        if prefix_width < prefix_minimum:
            prefix_width = prefix_minimum
            header_width = flexible_width - prefix_width

        self._resizing_flexible_sections = True
        try:
            header.resizeSection(1, header_width)
            header.resizeSection(2, prefix_width)
        finally:
            self._resizing_flexible_sections = False
