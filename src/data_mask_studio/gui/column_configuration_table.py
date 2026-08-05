from PySide6.QtWidgets import QAbstractItemView, QHeaderView

from data_mask_studio.gui.components.empty_state_table import EmptyStateTable


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
            ["Anonimizar", "Cabeçalho", "Prefixo", "Normalização", "Status"]
        )
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
