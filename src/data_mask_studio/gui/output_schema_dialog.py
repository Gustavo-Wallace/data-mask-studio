"""Read-only presentation of an already validated, value-free processing plan."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.gui.composite_dialog import source_label
from data_mask_studio.gui.components.presentation import (
    TruncatedTextToolTipDelegate, configure_dialog_layout, configure_table,
)
from data_mask_studio.i18n import Translator
from data_mask_studio.processing.models import BoundCompositeColumn, ProcessingPlan


class OutputSchemaDialog(QDialog):
    def __init__(self, plan: ProcessingPlan, parent=None, *, translator: Translator) -> None:
        super().__init__(parent)
        self._plan = plan
        # Emitted columns follow the planner verbatim; excluded sources have no
        # output position or header, and are listed separately at the end.
        self._rows = tuple(enumerate(plan.outputs, start=1)) + tuple(
            (None, column) for column in plan.physical_columns
            if column.action is ColumnAction.EXCLUDE
        )
        self.count_label = QLabel()
        self.note_label = QLabel()
        for label in (self.count_label, self.note_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
        self.table = QTableWidget(len(self._rows), 4)
        configure_table(self.table)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        for column in (0, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
            self.table.setItemDelegateForColumn(column, TruncatedTextToolTipDelegate(self.table))
        for row in range(len(self._rows)):
            for column in range(4):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, column, item)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setDefault(True)
        layout = QVBoxLayout(self)
        configure_dialog_layout(layout)
        layout.addWidget(self.count_label)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.note_label)
        layout.addWidget(self.buttons)
        self.retranslate(translator)
        available = self.screen().availableGeometry()
        self.resize(min(760, available.width()), min(420, available.height()))

    def retranslate(self, translator: Translator) -> None:
        """Re-render only this immutable schema; never inspect or plan again."""
        self.setWindowTitle(translator.text("masking.schema.title"))
        self.setAccessibleName(self.windowTitle())
        self.table.setAccessibleName(self.windowTitle())
        self.count_label.setText(translator.text("masking.schema.count", count=len(self._plan.outputs)))
        self.note_label.setText(translator.text("masking.schema.note"))
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(translator.text("dialog.button.close"))
        self.table.setHorizontalHeaderLabels([
            translator.text(identifier) for identifier in (
                "masking.schema.position", "masking.schema.sources",
                "masking.table.output_header", "masking.table.action",
            )
        ])

        def reference(column):
            return translator.text("masking.schema.source", position=column.input_index + 1,
                                   header=source_label(column.reference, translator))

        for row, (position, column) in enumerate(self._rows):
            source = (
                translator.text("masking.schema.composite", sources=" + ".join(
                    reference(component) for component in column.components
                )) if isinstance(column, BoundCompositeColumn) else reference(column)
            )
            values = (
                str(position) if position is not None else "—", source,
                column.output_name if position is not None else "—",
                translator.text(f"masking.action.{column.action.value}"),
            )
            for index, text in enumerate(values):
                self.table.item(row, index).setText(text)
