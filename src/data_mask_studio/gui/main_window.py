from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.anonymization import (
    ColumnConfig,
    create_column_configs,
    normalize_prefix,
    validate_configuration,
)
from data_mask_studio.csv_tools import CSVInspectionError, CSVInspectionResult, inspect_csv

SEPARATOR_NAMES = {
    ",": "Vírgula (,)",
    ";": "Ponto e vírgula (;)",
    "\t": "Tabulação",
    "|": "Barra vertical (|)",
}


class MainWindow(QMainWindow):
    """Janela principal do Data Mask Studio."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Data Mask Studio")
        self.resize(900, 600)

        title = QLabel("Data Mask Studio")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: 600;")

        description = QLabel(
            "Selecione um arquivo CSV para visualizar os cabeçalhos encontrados."
        )
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        description.setStyleSheet("font-size: 14px; color: #555;")

        self.select_button = QPushButton("Selecionar CSV")
        self.select_button.clicked.connect(self._select_csv)

        self.clear_button = QPushButton("Limpar seleção")
        self.clear_button.clicked.connect(self.clear_selection)
        self.clear_button.setEnabled(False)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.select_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()

        self.path_field = QLineEdit()
        self.path_field.setReadOnly(True)
        self.path_field.setPlaceholderText("Nenhum arquivo selecionado")

        self.file_name_label = QLabel("—")
        self.encoding_label = QLabel("—")
        self.delimiter_label = QLabel("—")
        self.column_count_label = QLabel("—")

        details_layout = QFormLayout()
        details_layout.addRow("Arquivo:", self.file_name_label)
        details_layout.addRow("Caminho completo:", self.path_field)
        details_layout.addRow("Codificação:", self.encoding_label)
        details_layout.addRow("Separador:", self.delimiter_label)
        details_layout.addRow("Quantidade de colunas:", self.column_count_label)

        configuration_label = QLabel("Configuração das colunas")
        configuration_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.select_all_button = QPushButton("Selecionar todas")
        self.select_all_button.clicked.connect(self.select_all_columns)
        self.select_all_button.setEnabled(False)

        self.unselect_all_button = QPushButton("Desmarcar todas")
        self.unselect_all_button.clicked.connect(self.unselect_all_columns)
        self.unselect_all_button.setEnabled(False)

        self.selected_count_label = QLabel("0 colunas selecionadas")

        selection_layout = QHBoxLayout()
        selection_layout.addWidget(self.select_all_button)
        selection_layout.addWidget(self.unselect_all_button)
        selection_layout.addStretch()
        selection_layout.addWidget(self.selected_count_label)

        self.config_table = QTableWidget(0, 4)
        self.config_table.setHorizontalHeaderLabels(
            ["Anonimizar", "Cabeçalho", "Prefixo", "Status"]
        )
        self.config_table.setAlternatingRowColors(True)
        self.config_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.config_table.verticalHeader().setVisible(False)
        table_header = self.config_table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self.validate_button = QPushButton("Validar configuração")
        self.validate_button.clicked.connect(self.validate_current_configuration)
        self.validate_button.setEnabled(False)

        self._column_configs: list[ColumnConfig] = []
        self._checkboxes: list[QCheckBox] = []
        self._prefix_fields: list[QLineEdit] = []

        self.status_label = QLabel("Selecione um arquivo CSV para começar.")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(8)
        layout.addLayout(button_layout)
        layout.addLayout(details_layout)
        layout.addWidget(configuration_label)
        layout.addLayout(selection_layout)
        layout.addWidget(self.config_table, stretch=1)
        layout.addWidget(self.validate_button)
        layout.addWidget(self.status_label)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def _select_csv(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar arquivo CSV",
            "",
            "Arquivos CSV (*.csv)",
        )
        if not file_path:
            self._set_status("Seleção de arquivo cancelada.", is_error=False)
            return
        self.load_csv(file_path)

    def load_csv(self, file_path: str) -> None:
        """Inspeciona o CSV selecionado e atualiza a interface."""
        try:
            result = inspect_csv(file_path)
        except CSVInspectionError as error:
            self._show_error(file_path, str(error))
            return

        self._show_result(result)

    def _show_result(self, result: CSVInspectionResult) -> None:
        self.file_name_label.setText(result.path.name)
        self.path_field.setText(str(result.path))
        self.encoding_label.setText(result.encoding)
        self.delimiter_label.setText(SEPARATOR_NAMES[result.delimiter])
        self.column_count_label.setText(str(len(result.headers)))
        self._build_configuration_table(result.headers)
        self.clear_button.setEnabled(True)
        self._set_status("Cabeçalhos lidos com sucesso.", is_error=False)

    def _show_error(self, file_path: str, message: str) -> None:
        selected_path = Path(file_path).expanduser().absolute()
        self._reset_details()
        self.file_name_label.setText(selected_path.name)
        self.path_field.setText(str(selected_path))
        self.clear_button.setEnabled(True)
        self._set_status(message, is_error=True)

    def clear_selection(
        self,
        *,
        status: str = "Seleção limpa.",
        is_error: bool = False,
    ) -> None:
        """Remove da interface todas as informações do arquivo selecionado."""
        self._reset_details()
        self._set_status(status, is_error=is_error)

    def _reset_details(self) -> None:
        self.file_name_label.setText("—")
        self.path_field.clear()
        self.encoding_label.setText("—")
        self.delimiter_label.setText("—")
        self.column_count_label.setText("—")
        self._clear_configuration_table()
        self.clear_button.setEnabled(False)

    def _build_configuration_table(self, headers: list[str]) -> None:
        self._clear_configuration_table()
        self._column_configs = create_column_configs(headers)
        self.config_table.setRowCount(len(headers))

        for row, configuration in enumerate(self._column_configs):
            checkbox = QCheckBox()
            checkbox.setToolTip(f"Anonimizar a coluna {configuration.header}")
            checkbox_container = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_container)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            checkbox_layout.addWidget(checkbox)
            self.config_table.setCellWidget(row, 0, checkbox_container)

            header_item = QTableWidgetItem(configuration.header)
            header_item.setFlags(header_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.config_table.setItem(row, 1, header_item)

            prefix_field = QLineEdit()
            prefix_field.setEnabled(False)
            prefix_field.setMaxLength(24)
            prefix_field.setPlaceholderText("Marque a coluna para definir")
            self.config_table.setCellWidget(row, 2, prefix_field)

            status_item = QTableWidgetItem("Não selecionada")
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.config_table.setItem(row, 3, status_item)

            checkbox.toggled.connect(
                lambda checked, current_row=row: self._column_toggled(
                    current_row, checked
                )
            )
            prefix_field.textChanged.connect(
                lambda text, current_row=row: self._prefix_changed(current_row, text)
            )
            self._checkboxes.append(checkbox)
            self._prefix_fields.append(prefix_field)

        has_headers = bool(headers)
        self.select_all_button.setEnabled(has_headers)
        self.unselect_all_button.setEnabled(has_headers)
        self.validate_button.setEnabled(has_headers)
        self._update_selected_count()

    def _clear_configuration_table(self) -> None:
        self.config_table.setRowCount(0)
        self._column_configs = []
        self._checkboxes = []
        self._prefix_fields = []
        self.select_all_button.setEnabled(False)
        self.unselect_all_button.setEnabled(False)
        self.validate_button.setEnabled(False)
        self.selected_count_label.setText("0 colunas selecionadas")

    def _column_toggled(self, row: int, checked: bool) -> None:
        configuration = self._column_configs[row]
        prefix_field = self._prefix_fields[row]
        configuration.anonymize = checked
        prefix_field.setEnabled(checked)
        if checked and not prefix_field.text():
            prefix_field.setText(normalize_prefix(configuration.header))
        self._update_selected_count()
        self._refresh_row_statuses()
        self._configuration_changed()

    def _prefix_changed(self, row: int, text: str) -> None:
        self._column_configs[row].prefix = text
        self._refresh_row_statuses()
        self._configuration_changed()

    def _configuration_changed(self) -> None:
        self._set_status(
            "Configuração alterada. Use “Validar configuração” para conferir.",
            is_error=False,
        )

    def select_all_columns(self) -> None:
        for checkbox in self._checkboxes:
            checkbox.setChecked(True)

    def unselect_all_columns(self) -> None:
        for checkbox in self._checkboxes:
            checkbox.setChecked(False)

    def _update_selected_count(self) -> None:
        selected_count = sum(
            configuration.anonymize for configuration in self._column_configs
        )
        self.selected_count_label.setText(
            f"{selected_count} de {len(self._column_configs)} colunas selecionadas"
        )

    def _refresh_row_statuses(self) -> None:
        result = validate_configuration(self._column_configs)
        for row, (configuration, row_result) in enumerate(
            zip(self._column_configs, result.column_results, strict=True)
        ):
            status_item = self.config_table.item(row, 3)
            prefix_field = self._prefix_fields[row]
            if not configuration.anonymize:
                status_item.setText("Não selecionada")
                status_item.setBackground(QColor("transparent"))
                prefix_field.setStyleSheet("")
            elif row_result.is_valid:
                status_item.setText("Válida")
                status_item.setBackground(QColor("#dcfce7"))
                prefix_field.setStyleSheet("")
            else:
                status_item.setText(row_result.error_message or "Inválida")
                status_item.setBackground(QColor("#fee2e2"))
                prefix_field.setStyleSheet("border: 1px solid #b42318;")

    def validate_current_configuration(self) -> None:
        result = validate_configuration(self._column_configs)
        self._refresh_row_statuses()
        if result.is_valid:
            suffix = "coluna" if result.selected_count == 1 else "colunas"
            self._set_status(
                f"Configuração válida para {result.selected_count} {suffix}.",
                is_error=False,
            )
        else:
            self._set_status(result.error_message or "Configuração inválida.", is_error=True)

    def _set_status(self, message: str, *, is_error: bool) -> None:
        color = "#b42318" if is_error else "#276749"
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color};")
