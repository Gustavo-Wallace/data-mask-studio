"""Editor de configuração; nenhuma leitura de valores ou operação criptográfica."""
from collections.abc import Callable
from uuid import uuid4

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from data_mask_studio.csv_tools.models import CSVInspectionResult
from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.csv_tools.source_binding import SourceBindingError, bind_source, source_ref_at
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.components.presentation import (
    configure_dialog_layout, set_button_role, set_feedback_state,
)
from data_mask_studio.normalization import NORMALIZATION_OPTIONS, NormalizationRule
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.processing.planner import PlanningError
from data_mask_studio.i18n import Translator


def source_label(reference, translator: Translator | None = None) -> str:
    if reference.is_synthetic:
        return (translator or Translator("pt-BR")).text(
            "masking.composite.generated_source", header=reference.header,
        )
    suffix = f" ({reference.occurrence + 1})" if reference.occurrence is not None else ""
    return reference.header + suffix


class ComponentRow(QWidget):
    def __init__(self, inspection, component=None, parent=None, *, translator: Translator | None = None):
        super().__init__(parent)
        self._translator = translator or Translator("pt-BR")
        self._unavailable_sources: set[int] = set()
        self.source = ScrollSafeComboBox()
        self.source.setMinimumContentsLength(12)
        self.source.setSizeAdjustPolicy(ScrollSafeComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for index in range(len(inspection.headers)):
            reference = source_ref_at(inspection, index)
            self.source.addItem(source_label(reference, self._translator), reference)
        self.normalization = ScrollSafeComboBox()
        for rule, label in NORMALIZATION_OPTIONS:
            self.normalization.addItem(label, rule)
        if component is not None:
            try:
                index = bind_source(component.reference, inspection)
            except SourceBindingError:
                self.source.addItem("", component.reference)
                index = self.source.count() - 1
                self._unavailable_sources.add(index)
            self.source.setCurrentIndex(index)
            self.normalization.setCurrentIndex(self.normalization.findData(component.normalization_rule))
        self.up = QPushButton("↑")
        self.down = QPushButton("↓")
        self.remove = QPushButton()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.source, 1)
        layout.addWidget(self.normalization, 1)
        for button in (self.up, self.down, self.remove):
            layout.addWidget(button)
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        self._translator = translator
        self.source.setAccessibleName(translator.text("composite.editor.source"))
        self.normalization.setAccessibleName(translator.text("composite.editor.normalization"))
        with QSignalBlocker(self.source), QSignalBlocker(self.normalization):
            for index in range(self.source.count()):
                text = source_label(self.source.itemData(index), translator)
                if index in self._unavailable_sources:
                    text = translator.text("composite.editor.unavailable_source", source=text)
                self.source.setItemText(index, text)
            for index in range(self.normalization.count()):
                rule = NormalizationRule(self.normalization.itemData(index))
                self.normalization.setItemText(index, translator.text(f"masking.normalization.{rule.value}"))
        self.up.setAccessibleName(translator.text("composite.editor.move_up"))
        self.down.setAccessibleName(translator.text("composite.editor.move_down"))
        self.remove.setText(translator.text("composite.editor.remove"))

    def component(self):
        return CompositeSource(self.source.currentData(), NormalizationRule(self.normalization.currentData()))


class CompositeDialog(QDialog):
    def __init__(
        self, inspection: CSVInspectionResult,
        validate: Callable[[CompositeColumnConfig], object],
        composite: CompositeColumnConfig | None = None, parent=None,
        *, translator: Translator | None = None,
    ):
        super().__init__(parent)
        self._translator = translator or Translator("pt-BR")
        self._editing = composite is not None
        self._inspection = inspection
        self._validate = validate
        self._identifier = composite.identifier if composite else uuid4()
        self._action = composite.action if composite else ColumnAction.PRESERVE
        self._prefix = composite.prefix if composite else ""
        self.result_config: CompositeColumnConfig | None = None
        self.rows: list[ComponentRow] = []
        self.name_field = QLineEdit(composite.output_name if composite else "")
        form = QFormLayout()
        self.name_label = QLabel()
        self.name_label.setBuddy(self.name_field)
        form.addRow(self.name_label, self.name_field)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        self.rows_layout = QVBoxLayout(body)
        self.rows_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(body)
        self.add_button = QPushButton()
        self.add_button.clicked.connect(lambda: self.add_component())
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.components_label = QLabel()
        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        set_feedback_state(self.error_label, "error")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        set_button_role(self.buttons.button(QDialogButtonBox.StandardButton.Save), "primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        configure_dialog_layout(layout)
        layout.addLayout(form)
        layout.addWidget(self.components_label)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.add_button)
        layout.addWidget(self.help_label)
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
        self.retranslate(self._translator)
        available = self.screen().availableGeometry()
        self.resize(min(self.sizeHint().width(), available.width()), min(self.sizeHint().height(), available.height()))

    def add_component(self, component=None):
        row = ComponentRow(self._inspection, component, self, translator=self._translator)
        row.up.clicked.connect(lambda: self.move_component(row, -1))
        row.down.clicked.connect(lambda: self.move_component(row, 1))
        row.remove.clicked.connect(lambda: self.remove_component(row))
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self._refresh_order()

    def retranslate(self, translator: Translator) -> None:
        """Relabel the live editor without rebuilding rows or validating/saving."""
        self._translator = translator
        self.setWindowTitle(translator.text("composite.editor.title"))
        self.name_label.setText(translator.text("composite.editor.output_label"))
        self.name_field.setAccessibleName(translator.text("masking.table.output_header"))
        self.components_label.setText(translator.text("masking.composite.components"))
        self.add_button.setText(translator.text("composite.editor.add"))
        self.help_label.setText(translator.text("composite.editor.help"))
        self.error_label.setAccessibleName(translator.text("composite.editor.error_accessible"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(translator.text(
            "composite.editor.save" if self._editing else "composite.editor.create"
        ))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(translator.text("dialog.button.cancel"))
        for row in self.rows:
            row.retranslate(translator)

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
        if self.rows:
            margins = self.rows_layout.contentsMargins()
            height = sum(row.sizeHint().height() for row in self.rows)
            height += max(0, len(self.rows) - 1) * self.rows_layout.spacing()
            height += margins.top() + margins.bottom() + 2 * self.scroll.frameWidth()
            self.scroll.setFixedHeight(min(height, self.screen().availableGeometry().height() // 3))
            self.adjustSize()

    def accept(self):
        candidate = CompositeColumnConfig(
            self.name_field.text().strip(), self._prefix,
            tuple(row.component() for row in self.rows), self._identifier, self._action,
        )
        try:
            self._validate(candidate)
        except PlanningError as error:
            self.error_label.setText(str(error))
            return
        self.result_config = candidate
        super().accept()
