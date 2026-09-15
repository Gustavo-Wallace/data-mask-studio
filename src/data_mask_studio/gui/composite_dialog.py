"""Editor de configuração; nenhuma leitura de valores ou operação criptográfica."""
from collections.abc import Callable
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from data_mask_studio.csv_tools.models import CSVInspectionResult
from data_mask_studio.csv_tools.source_binding import SourceBindingError, bind_source, source_ref_at
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.normalization import NORMALIZATION_OPTIONS, NormalizationRule
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.processing.planner import PlanningError


def source_label(reference) -> str:
    suffix = " (gerado)" if reference.is_synthetic else (
        f" ({reference.occurrence + 1})" if reference.occurrence is not None else ""
    )
    return reference.header + suffix


class ComponentRow(QWidget):
    def __init__(self, inspection, component=None, parent=None):
        super().__init__(parent)
        self.source = ScrollSafeComboBox()
        self.source.setAccessibleName("Coluna fonte")
        self.source.setMinimumContentsLength(12)
        self.source.setSizeAdjustPolicy(ScrollSafeComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for index in range(len(inspection.headers)):
            reference = source_ref_at(inspection, index)
            self.source.addItem(source_label(reference), reference)
        self.normalization = ScrollSafeComboBox()
        self.normalization.setAccessibleName("Normalização do componente")
        for rule, label in NORMALIZATION_OPTIONS:
            self.normalization.addItem(label, rule)
        if component is not None:
            try:
                index = bind_source(component.reference, inspection)
            except SourceBindingError:
                self.source.addItem(source_label(component.reference) + " (indisponível)", component.reference)
                index = self.source.count() - 1
            self.source.setCurrentIndex(index)
            self.normalization.setCurrentIndex(self.normalization.findData(component.normalization_rule))
        self.up = QPushButton("↑")
        self.up.setAccessibleName("Mover componente para cima")
        self.down = QPushButton("↓")
        self.down.setAccessibleName("Mover componente para baixo")
        self.remove = QPushButton("Remover")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.source, 1)
        layout.addWidget(self.normalization, 1)
        for button in (self.up, self.down, self.remove):
            layout.addWidget(button)

    def component(self):
        return CompositeSource(self.source.currentData(), NormalizationRule(self.normalization.currentData()))


class CompositeDialog(QDialog):
    def __init__(
        self, inspection: CSVInspectionResult,
        validate: Callable[[CompositeColumnConfig], object],
        composite: CompositeColumnConfig | None = None, parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Coluna composta")
        self._inspection = inspection
        self._validate = validate
        self._identifier = composite.identifier if composite else uuid4()
        self.result_config: CompositeColumnConfig | None = None
        self.rows: list[ComponentRow] = []
        self.name_field = QLineEdit(composite.output_name if composite else "")
        self.prefix_field = QLineEdit(composite.prefix if composite else "")
        self.prefix_field.setToolTip("Use o prefixo sem hífen, por exemplo CORR.")
        form = QFormLayout()
        form.addRow("Nome de saída:", self.name_field)
        form.addRow("Prefixo:", self.prefix_field)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        self.rows_layout = QVBoxLayout(body)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(body)
        self.add_button = QPushButton("+ Adicionar componente")
        self.add_button.clicked.connect(lambda: self.add_component())
        help_label = QLabel("Colunas excluídas da saída ainda podem ser usadas como fontes.\n"
                            "A ordem dos componentes altera a identidade da coluna composta.")
        help_label.setWordWrap(True)
        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("Erro de configuração composta")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Salvar" if composite else "Criar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Componentes"))
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.add_button)
        layout.addWidget(help_label)
        layout.addWidget(self.error_label)
        layout.addWidget(self.buttons)
        if composite:
            for component in composite.components:
                self.add_component(component)
        else:
            self.add_component()
            self.add_component()
            if len(inspection.headers) > 1:
                self.rows[1].source.setCurrentIndex(1)
        available = self.screen().availableGeometry()
        self.resize(min(self.sizeHint().width(), available.width()), min(460, available.height()))

    def add_component(self, component=None):
        row = ComponentRow(self._inspection, component, self)
        row.up.clicked.connect(lambda: self.move_component(row, -1))
        row.down.clicked.connect(lambda: self.move_component(row, 1))
        row.remove.clicked.connect(lambda: self.remove_component(row))
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self._refresh_order()

    def move_component(self, row, offset):
        index = self.rows.index(row)
        target = index + offset
        if 0 <= target < len(self.rows):
            self.rows.insert(target, self.rows.pop(index))
            self.rows_layout.removeWidget(row)
            self.rows_layout.insertWidget(target, row)
            self._refresh_order()

    def remove_component(self, row):
        self.rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._refresh_order()

    def _refresh_order(self):
        for index, row in enumerate(self.rows):
            row.up.setEnabled(index > 0)
            row.down.setEnabled(index < len(self.rows) - 1)

    def accept(self):
        candidate = CompositeColumnConfig(
            self.name_field.text().strip(), self.prefix_field.text(),
            tuple(row.component() for row in self.rows), self._identifier,
        )
        try:
            self._validate(candidate)
        except PlanningError as error:
            self.error_label.setText(str(error))
            return
        self.result_config = candidate
        super().accept()
