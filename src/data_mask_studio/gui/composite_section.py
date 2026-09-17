from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget,
)

from data_mask_studio.gui.composite_dialog import CompositeDialog, source_label
from data_mask_studio.gui.composite_table import CompositeTable
from data_mask_studio.gui.action_styles import ACTION_INDICATOR_STYLES
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.column_configuration_table import PREFIX_PLACEHOLDER
from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.processing.composite_actions import composite_action_error
from data_mask_studio.normalization import normalization_label


class CompositeSection(QGroupBox):
    changed = Signal()

    def __init__(self, context, validate, parent=None):
        super().__init__("Colunas compostas", parent)
        self._context = context
        self._validate = validate
        self.configurations = ()
        self.add_button = QPushButton("+ Adicionar")
        self.add_button.setToolTip("Combine duas ou mais colunas para preservar ou mascarar.")
        self.add_button.clicked.connect(lambda: self.edit())
        self.empty_label = QLabel("Nenhuma coluna composta configurada.")
        self.table = CompositeTable()
        bar = QHBoxLayout()
        bar.addWidget(self.empty_label, 1)
        bar.addStretch()
        bar.addWidget(self.add_button)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.table)
        self.set_configurations(())

    def set_configurations(self, configurations, *, notify=False):
        self.configurations = tuple(configurations)
        self.table.setRowCount(len(self.configurations))
        for index, composite in enumerate(self.configurations):
            action = ScrollSafeComboBox()
            action.setSizeAdjustPolicy(ScrollSafeComboBox.SizeAdjustPolicy.AdjustToContents)
            action.setAccessibleName(f"Ação da coluna composta {composite.output_name}")
            action.addItem("Preservar", ColumnAction.PRESERVE)
            action.addItem("Mascarar", ColumnAction.MASK)
            action.setCurrentIndex(action.findData(composite.action))
            action.currentIndexChanged.connect(lambda _, row=index: self._action_changed(row))
            self.table.setCellWidget(index, 0, action)
            values = [composite.output_name, " + ".join(source_label(s.reference) for s in composite.components)]
            for column, text in enumerate(values, 1):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(text)
                self.table.setItem(index, column, item)
            self.table.item(index, 2).setToolTip(" + ".join(
                f"{source_label(s.reference)} [{normalization_label(s.normalization_rule)}]" for s in composite.components
            ))
            prefix = QLineEdit(composite.prefix)
            prefix.setAccessibleName(f"Prefixo da coluna composta {composite.output_name}")
            prefix.setPlaceholderText(PREFIX_PLACEHOLDER)
            prefix.setMaxLength(24)
            prefix.setMinimumWidth(prefix.fontMetrics().horizontalAdvance(PREFIX_PLACEHOLDER + "MM"))
            prefix.textChanged.connect(lambda text, row=index: self._prefix_changed(row, text))
            self.table.setCellWidget(index, 3, prefix)
            self._refresh_prefix(index)
            actions = QWidget()
            buttons = QHBoxLayout(actions)
            buttons.setContentsMargins(0, 0, 0, 0)
            buttons.setSpacing(self.fontMetrics().horizontalAdvance(" "))
            buttons.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            edit = QPushButton("Editar")
            remove = QPushButton("Excluir")
            edit.clicked.connect(lambda checked=False, row=index: self.edit(row))
            remove.clicked.connect(lambda checked=False, row=index: self.remove(row))
            buttons.addWidget(edit)
            buttons.addWidget(remove)
            self.table.setCellWidget(index, 4, actions)
        self.table.refresh_layout()
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
        field.setToolTip(error or "Use o prefixo sem hífen, por exemplo CORR.")
        field.setStyleSheet("border: 1px solid #b42318;" if error else "")

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
