from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.detection import (
    ColumnSuggestion,
    ConfidenceLevel,
    SuggestedType,
)
from data_mask_studio.gui.components.empty_state_table import EmptyStateTable
from data_mask_studio.i18n import Translator
from data_mask_studio.gui.components.presentation import (
    configure_dialog_layout, set_button_role, set_feedback_state,
)


class DetectionDialog(QDialog):
    """Apresenta somente resultados agregados para revisão do usuário."""

    individual_requested = Signal(int)
    bulk_requested = Signal(object)
    suggestions_cleared = Signal()

    def __init__(
        self,
        suggestions: tuple[ColumnSuggestion, ...],
        rows_analyzed: int,
        parent: QWidget | None = None,
        *, translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator("pt-BR")
        self._rows_analyzed = rows_analyzed
        available = self.screen().availableGeometry()
        self.resize(min(1100, available.width()), min(520, available.height()))
        self._suggestions = suggestions
        self._acceptance_fields: list[QCheckBox] = []

        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        set_feedback_state(self.explanation, "neutral")

        self.table = EmptyStateTable(0, 9, "")
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 3, 4, 5, 6, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self._populate_table()

        self.apply_high_button = QPushButton()
        self.apply_high_button.clicked.connect(self._request_high_confidence)
        set_button_role(self.apply_high_button, "primary")
        self.apply_accepted_button = QPushButton()
        self.apply_accepted_button.clicked.connect(self._request_accepted)
        self.clear_button = QPushButton()
        self.clear_button.clicked.connect(self._clear_suggestions)
        self.close_button = QPushButton()
        self.close_button.clicked.connect(self.close)

        actions = QHBoxLayout()
        self._actions_layout = actions
        actions.addWidget(self.apply_high_button)
        actions.addWidget(self.apply_accepted_button)
        actions.addStretch()
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.clear_button)
        footer.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        configure_dialog_layout(layout)
        layout.addWidget(self.explanation)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(actions)
        layout.addLayout(footer)
        self.retranslate(self._translator)
        # Os textos das ações não devem impor uma largura maior que a tela.
        if layout.minimumSize().width() > available.width():
            actions.setDirection(QBoxLayout.Direction.TopToBottom)

    def retranslate(self, translator: Translator) -> None:
        """Translate typed presentation, never classifications, reasons or selections."""
        self._translator = translator
        self.setWindowTitle(translator.text("detection.title"))
        self.explanation.setText(translator.text("detection.instructions", count=self._rows_analyzed))
        self.table.setHorizontalHeaderLabels([
            translator.text(message_id) for message_id in (
                "detection.table.accept", "masking.table.header", "detection.table.type",
                "masking.action.mask", "masking.table.prefix", "masking.table.normalization",
                "detection.table.confidence", "detection.table.reason", "masking.table.action",
            )
        ])
        self.table.empty_text = translator.text("detection.empty")
        self.table.setAccessibleDescription(self.table.empty_text)
        for button, message_id in (
            (self.apply_high_button, "detection.apply_high"),
            (self.apply_accepted_button, "detection.apply_accepted"),
            (self.clear_button, "detection.clear"),
            (self.close_button, "dialog.button.close"),
        ):
            button.setText(translator.text(message_id))
        for row, suggestion in enumerate(self._suggestions):
            values = {
                2: translator.text(f"detection.type.{suggestion.suggested_type.value}"),
                3: translator.text("detection.yes" if suggestion.anonymize else "detection.no"),
                5: translator.text(f"masking.normalization.{suggestion.normalization_rule.value}"),
                6: translator.text(f"detection.confidence.{suggestion.confidence.value}"),
            }
            for column, value in values.items():
                self.table.item(row, column).setText(value)
            self._acceptance_fields[row].setAccessibleName(translator.text(
                "detection.accept_accessible", header=suggestion.header,
            ))
            button = self.table.cellWidget(row, 8)
            button.setText(translator.text("detection.apply"))
            button.setAccessibleName(translator.text("detection.apply_accessible", header=suggestion.header))
        self.table.viewport().update()
        self._actions_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if self.layout().minimumSize().width() > self.screen().availableGeometry().width()
            else QBoxLayout.Direction.LeftToRight
        )

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._suggestions))
        for row, suggestion in enumerate(self._suggestions):
            acceptance = QCheckBox()
            acceptance.setEnabled(suggestion.suggested_type is not SuggestedType.UNKNOWN)
            acceptance_container = QWidget()
            acceptance_layout = QHBoxLayout(acceptance_container)
            acceptance_layout.setContentsMargins(0, 0, 0, 0)
            acceptance_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            acceptance_layout.addWidget(acceptance)
            self.table.setCellWidget(row, 0, acceptance_container)
            self._acceptance_fields.append(acceptance)

            values = (
                suggestion.header,
                "",
                "",
                suggestion.prefix or "—",
                "",
                "",
                suggestion.reason,
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 7:
                    item.setToolTip(value)
                self.table.setItem(row, column, item)

            apply_button = QPushButton()
            apply_button.setEnabled(
                suggestion.suggested_type is not SuggestedType.UNKNOWN
            )
            apply_button.clicked.connect(
                lambda _checked=False, current_row=row: self.individual_requested.emit(
                    current_row
                )
            )
            self.table.setCellWidget(row, 8, apply_button)

    def _request_high_confidence(self) -> None:
        indexes = tuple(
            index
            for index, suggestion in enumerate(self._suggestions)
            if suggestion.confidence is ConfidenceLevel.HIGH
            and suggestion.suggested_type is not SuggestedType.UNKNOWN
        )
        if indexes:
            self.bulk_requested.emit(indexes)

    def _request_accepted(self) -> None:
        indexes = tuple(
            index
            for index, field in enumerate(self._acceptance_fields)
            if field.isChecked()
        )
        if indexes:
            self.bulk_requested.emit(indexes)

    def _clear_suggestions(self) -> None:
        self.suggestions_cleared.emit()
        self.close()
