"""Sizing local da tabela composta, usando o tema e os controles existentes."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyledItemDelegate, QTableWidget


class _TextPaddingDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        padding = option.fontMetrics.horizontalAdvance(" ")
        option.rect.adjust(padding, 0, -padding, 0)
        super().paint(painter, option, index)


class CompositeTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, 5, parent)
        self.setHorizontalHeaderLabels(["Ação", "Cabeçalho de saída", "Componentes", "Prefixo", "Ações"])
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.verticalHeader().hide()
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in (0, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2):
            self.setItemDelegateForColumn(column, _TextPaddingDelegate(self))
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.timeout.connect(self.refresh_layout)
        self._width_reference = None

    def set_width_reference(self, table):
        """Acompanha o sizing da tabela física, sem duplicar sua distribuição."""
        self._width_reference = table
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().sectionResized.connect(self._reference_resized)
        self._refresh.start(0)

    def _reference_resized(self, column, _old, _new):
        if column in (2, 3):
            self._refresh.start(0)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh.start(0)

    def refresh_layout(self):
        self.ensurePolished()
        header = self.horizontalHeader()
        metrics = self.fontMetrics()
        # Um cabeçalho enorme não deve consumir a área dos componentes.
        header.resizeSection(1, max(header.sectionSizeHint(1), metrics.horizontalAdvance("M" * 16)))
        for column in (0, 3, 4):
            self.resizeColumnToContents(column)
        if self._width_reference is not None:
            header.resizeSection(1, self._width_reference.columnWidth(2))
            header.resizeSection(3, self._width_reference.columnWidth(3))
        row_height = self.verticalHeader().defaultSectionSize()
        for row in range(self.rowCount()):
            for column in (0, 3, 4):
                control = self.cellWidget(row, column)
                if control:
                    row_height = max(row_height, control.minimumSizeHint().height() + 1,
                                     control.sizeHint().height() + 1)
        for row in range(self.rowCount()):
            self.setRowHeight(row, row_height)
        self.setMaximumHeight(header.height() + min(3, max(1, self.rowCount())) * row_height
                              + self.horizontalScrollBar().sizeHint().height() + 2 * self.frameWidth())
