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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.detection import (
    ColumnSuggestion,
    ConfidenceLevel,
    SuggestedType,
)
from data_mask_studio.detection.models import CONFIDENCE_LABELS, TYPE_LABELS
from data_mask_studio.normalization import normalization_label
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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sugestões de colunas sensíveis")
        available = self.screen().availableGeometry()
        self.resize(min(1100, available.width()), min(520, available.height()))
        self._suggestions = suggestions
        self._acceptance_fields: list[QCheckBox] = []

        explanation = QLabel(
            f"Foram analisadas até {rows_analyzed} linhas. "
            "Revise as sugestões; nenhuma alteração é aplicada automaticamente."
        )
        explanation.setWordWrap(True)
        set_feedback_state(explanation, "neutral")

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Aceitar",
                "Cabeçalho",
                "Tipo sugerido",
                "Anonimizar",
                "Prefixo",
                "Normalização",
                "Confiança",
                "Justificativa",
                "Ação",
            ]
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 3, 4, 5, 6, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self._populate_table()

        self.apply_high_button = QPushButton("Aplicar sugestões de confiança alta")
        self.apply_high_button.clicked.connect(self._request_high_confidence)
        set_button_role(self.apply_high_button, "primary")
        self.apply_accepted_button = QPushButton("Aplicar sugestões aceitas")
        self.apply_accepted_button.clicked.connect(self._request_accepted)
        self.clear_button = QPushButton("Limpar sugestões")
        self.clear_button.clicked.connect(self._clear_suggestions)
        self.close_button = QPushButton("Fechar")
        self.close_button.clicked.connect(self.close)

        actions = QHBoxLayout()
        actions.addWidget(self.apply_high_button)
        actions.addWidget(self.apply_accepted_button)
        actions.addStretch()
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.clear_button)
        footer.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        configure_dialog_layout(layout)
        layout.addWidget(explanation)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(actions)
        layout.addLayout(footer)
        # Os textos das ações não devem impor uma largura maior que a tela.
        if layout.minimumSize().width() > available.width():
            actions.setDirection(QBoxLayout.Direction.TopToBottom)

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
                TYPE_LABELS[suggestion.suggested_type],
                "Sim" if suggestion.anonymize else "Não",
                suggestion.prefix or "—",
                normalization_label(suggestion.normalization_rule),
                CONFIDENCE_LABELS[suggestion.confidence],
                suggestion.reason,
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 7:
                    item.setToolTip(value)
                self.table.setItem(row, column, item)

            apply_button = QPushButton("Aplicar")
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
