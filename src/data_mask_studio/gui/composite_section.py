from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget,
)

from data_mask_studio.gui.composite_dialog import CompositeDialog
from data_mask_studio.gui.composite_table import CompositeTable
from data_mask_studio.gui.action_styles import ACTION_INDICATOR_STYLES
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.i18n import Translator
from data_mask_studio.processing.composite_actions import composite_action_error


class CompositeSection(QGroupBox):
    changed = Signal()

    def __init__(self, context, validate, parent=None, *, translator: Translator | None = None):
        super().__init__(parent)
        self._translator = translator or Translator("pt-BR")
        self._context = context
        self._validate = validate
        self.configurations = ()
        self.add_button = QPushButton()
        self.add_button.clicked.connect(lambda: self.edit())
        self.empty_label = QLabel()
        self.table = CompositeTable(translator=self._translator)
        bar = QHBoxLayout()
        bar.addWidget(self.empty_label, 1)
        bar.addStretch()
        bar.addWidget(self.add_button)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.table)
        self.set_configurations(())

    def retranslate(self, translator: Translator) -> None:
        """Translate existing controls, preserving configuration, focus and signals."""
        self._translator = translator
        self.setTitle(translator.text("masking.composite.title"))
        self.add_button.setText(translator.text("masking.composite.add"))
        self.add_button.setToolTip(translator.text("masking.composite.add_tooltip"))
        self.empty_label.setText(translator.text("masking.composite.empty"))
        self.table.retranslate(translator)
        for index, composite in enumerate(self.configurations):
            action = self.table.cellWidget(index, 0)
            with QSignalBlocker(action):
                for option in range(action.count()):
                    value = ColumnAction(action.itemData(option)).value
                    action.setItemText(option, translator.text(f"masking.action.{value}"))
            action.setAccessibleName(translator.text(
                "masking.composite.action_accessible", name=composite.output_name,
            ))
            components = self.table.item(index, 2)
            components.setText(" + ".join(
                self._source_label(source.reference) for source in composite.components
            ))
            components.setToolTip(" + ".join(
                f"{self._source_label(source.reference)} "
                f"[{translator.text(f'masking.normalization.{source.normalization_rule.value}')}]"
                for source in composite.components
            ))
            prefix = self.table.cellWidget(index, 3)
            prefix.setAccessibleName(translator.text(
                "masking.composite.prefix_accessible", name=composite.output_name,
            ))
            placeholder = translator.text("masking.table.prefix_placeholder")
            prefix.setPlaceholderText(placeholder)
            prefix.setMinimumWidth(prefix.fontMetrics().horizontalAdvance(placeholder + "MM"))
            self._refresh_prefix_tooltip(index)
            buttons = self.table.cellWidget(index, 4).findChildren(QPushButton)
            buttons[0].setText(translator.text("masking.composite.edit"))
            buttons[1].setText(translator.text("masking.composite.delete"))
        self.table.refresh_layout()

    def _source_label(self, reference):
        if reference.is_synthetic:
            return self._translator.text("masking.composite.generated_source", header=reference.header)
        suffix = f" ({reference.occurrence + 1})" if reference.occurrence is not None else ""
        return reference.header + suffix

    def set_configurations(self, configurations, *, notify=False):
        self.configurations = tuple(configurations)
        self.table.setRowCount(len(self.configurations))
        for index, composite in enumerate(self.configurations):
            action = ScrollSafeComboBox()
            action.setSizeAdjustPolicy(ScrollSafeComboBox.SizeAdjustPolicy.AdjustToContents)
            action.addItem(self._translator.text("masking.action.preserve"), ColumnAction.PRESERVE)
            action.addItem(self._translator.text("masking.action.mask"), ColumnAction.MASK)
            action.setCurrentIndex(action.findData(composite.action))
            action.currentIndexChanged.connect(lambda _, row=index: self._action_changed(row))
            self.table.setCellWidget(index, 0, action)
            values = [composite.output_name, " + ".join(self._source_label(s.reference) for s in composite.components)]
            for column, text in enumerate(values, 1):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(text)
                self.table.setItem(index, column, item)
            prefix = QLineEdit(composite.prefix)
            prefix.setMaxLength(24)
            prefix.textChanged.connect(lambda text, row=index: self._prefix_changed(row, text))
            self.table.setCellWidget(index, 3, prefix)
            self._refresh_prefix(index)
            actions = QWidget()
            buttons = QHBoxLayout(actions)
            buttons.setContentsMargins(0, 0, 0, 0)
            buttons.setSpacing(self.fontMetrics().horizontalAdvance(" "))
            buttons.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            edit = QPushButton()
            remove = QPushButton()
            edit.clicked.connect(lambda checked=False, row=index: self.edit(row))
            remove.clicked.connect(lambda checked=False, row=index: self.remove(row))
            buttons.addWidget(edit)
            buttons.addWidget(remove)
            self.table.setCellWidget(index, 4, actions)
        self.retranslate(self._translator)
        self.table.setVisible(bool(self.configurations))
        self.empty_label.setVisible(not self.configurations)
        if notify:
            self.changed.emit()

    def _replace(self, index, **changes):
        configurations = list(self.configurations)
        configurations[index] = replace(configurations[index], **changes)
        self.configurations = tuple(configurations)
        self._refresh_prefix(index)
        self.changed.emit()

    def _action_changed(self, index):
        action = ColumnAction(self.table.cellWidget(index, 0).currentData())
        self._replace(index, action=action, prefix="")

    def _prefix_changed(self, index, text):
        self._replace(index, prefix=text)

    def _refresh_prefix(self, index):
        composite = self.configurations[index]
        action = self.table.cellWidget(index, 0)
        action.setProperty("columnAction", composite.action.value)
        action.setStyleSheet(ACTION_INDICATOR_STYLES[composite.action])
        field = self.table.cellWidget(index, 3)
        field.blockSignals(True)
        field.setText(composite.prefix)
        field.blockSignals(False)
        field.setEnabled(composite.action is ColumnAction.MASK)
        error = composite_action_error(composite.action, composite.prefix)
        self._refresh_prefix_tooltip(index)
        field.setStyleSheet("border: 1px solid #b42318;" if error else "")

    def _refresh_prefix_tooltip(self, index):
        composite = self.configurations[index]
        error = composite_action_error(composite.action, composite.prefix)
        # Untyped service errors retain their safe existing wording until 05D.2.
        self.table.cellWidget(index, 3).setToolTip(
            error or self._translator.text("masking.composite.prefix_hint")
        )

    def edit(self, index=None):
        inspection = self._context()
        if inspection is None:
            return
        def validate(candidate):
            proposed = list(self.configurations)
            if index is None:
                proposed.append(candidate)
            else:
                proposed[index] = candidate
            # O diálogo valida estrutura; ação/prefixo são editados na tabela.
            self._validate(tuple(replace(c, action=ColumnAction.PRESERVE, prefix="") for c in proposed))

        dialog = CompositeDialog(inspection, validate, None if index is None else self.configurations[index], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            proposed = list(self.configurations)
            if index is None:
                proposed.append(dialog.result_config)
            else:
                proposed[index] = dialog.result_config
            self.set_configurations(proposed, notify=True)
        dialog.deleteLater()

    def remove(self, index):
        self.set_configurations(self.configurations[:index] + self.configurations[index + 1:], notify=True)
