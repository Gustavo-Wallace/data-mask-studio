from PySide6.QtCore import QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle

from data_mask_studio.gui.components.empty_state_table import EmptyStateTable

PREFIX_PLACEHOLDER = "Disponível ao mascarar"


class ColumnConfigurationTable(EmptyStateTable):
    """Tabela visual da configuração das colunas do CSV."""

    def __init__(self, parent=None) -> None:
        super().__init__(
            0,
            5,
            "Selecione um CSV para configurar as colunas.",
            parent,
        )
        self.setHorizontalHeaderLabels(
            ["Ação", "Cabeçalho", "Nome de saída", "Prefixo", "Normalização"]
        )
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.sectionResized.connect(self._fixed_section_resized)
        self._resizing_flexible_sections = False
        self._layout_refresh_timer = QTimer(self)
        self._layout_refresh_timer.setSingleShot(True)
        self._layout_refresh_timer.timeout.connect(self._apply_deferred_column_layout)
        self._resize_flexible_sections()

    def refresh_column_layout(self) -> None:
        """Recalcula as seções após os widgets das células serem populados."""
        self._apply_content_sizes()
        if not self._layout_refresh_timer.isActive():
            self._layout_refresh_timer.start(0)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._resize_flexible_sections()

    def _fixed_section_resized(
        self, logical_index: int, _old_size: int, _new_size: int
    ) -> None:
        if logical_index in (0, 4):
            self._resize_flexible_sections()

    def _apply_deferred_column_layout(self) -> None:
        self.ensurePolished()
        self._apply_content_sizes()

    def _apply_content_sizes(self) -> None:
        self._resizing_flexible_sections = True
        try:
            self.resizeColumnToContents(0)
            self.resizeColumnToContents(4)
        finally:
            self._resizing_flexible_sections = False
        self._resize_flexible_sections()
        self.viewport().updateGeometry()
        self.viewport().update()

    def _resize_flexible_sections(self) -> None:
        if self._resizing_flexible_sections:
            return

        header = self.horizontalHeader()
        fixed_width = header.sectionSize(0) + header.sectionSize(4)
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
        output_minimum = max(
            header.sectionSizeHint(2),
            font_metrics.horizontalAdvance("Manter original") + text_padding + frame_width,
        )
        # Mantém os editores legíveis; em telas estreitas o Qt oferece scroll.
        minimum_width = header_minimum + output_minimum + prefix_minimum
        extra = max(0, available_width - minimum_width)
        output_width = output_minimum + round(extra * 0.2)
        remaining = max(available_width, minimum_width) - output_width
        header_width = max(header_minimum, round(remaining * 0.55))
        prefix_width = max(prefix_minimum, remaining - header_width)
        header_width = remaining - prefix_width

        self._resizing_flexible_sections = True
        try:
            header.resizeSection(1, header_width)
            header.resizeSection(2, output_width)
            header.resizeSection(3, prefix_width)
        finally:
            self._resizing_flexible_sections = False
