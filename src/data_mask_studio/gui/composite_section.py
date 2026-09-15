from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from data_mask_studio.gui.composite_dialog import CompositeDialog, source_label
from data_mask_studio.normalization import normalization_label


class CompositeSection(QGroupBox):
    changed = Signal()

    def __init__(self, context, validate, parent=None):
        super().__init__("Colunas compostas", parent)
        self._context = context
        self._validate = validate
        self.configurations = ()
        self.add_button = QPushButton("+ Adicionar")
        self.add_button.setToolTip("Combine duas ou mais colunas em um token determinístico.")
        self.add_button.clicked.connect(lambda: self.edit())
        self.empty_label = QLabel("Nenhuma coluna composta configurada.")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Nome de saída", "Componentes", "Prefixo", "Ações"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for index in (0, 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        for index in (2, 3):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
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
            values = [composite.output_name, " + ".join(source_label(s.reference) for s in composite.components), composite.prefix]
            for column, text in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(text))
            self.table.item(index, 1).setToolTip(" + ".join(
                f"{source_label(s.reference)} [{normalization_label(s.normalization_rule)}]" for s in composite.components
            ))
            actions = QWidget()
            buttons = QHBoxLayout(actions)
            buttons.setContentsMargins(0, 0, 0, 0)
            edit = QPushButton("Editar")
            remove = QPushButton("Excluir")
            edit.clicked.connect(lambda checked=False, row=index: self.edit(row))
            remove.clicked.connect(lambda checked=False, row=index: self.remove(row))
            buttons.addWidget(edit)
            buttons.addWidget(remove)
            self.table.setCellWidget(index, 3, actions)
        self.table.resizeRowsToContents()
        self.table.setMaximumHeight(self.table.horizontalHeader().sizeHint().height() +
                                    max(1, min(3, len(self.configurations))) * (self.fontMetrics().height() + 24) + 8)
        self.table.setVisible(bool(self.configurations))
        self.empty_label.setVisible(not self.configurations)
        if notify:
            self.changed.emit()

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
            self._validate(tuple(proposed))

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
