from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
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

        headers_label = QLabel("Cabeçalhos encontrados")
        headers_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.headers_list = QListWidget()
        self.headers_list.setAlternatingRowColors(True)

        self.status_label = QLabel("Selecione um arquivo CSV para começar.")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(8)
        layout.addLayout(button_layout)
        layout.addLayout(details_layout)
        layout.addWidget(headers_label)
        layout.addWidget(self.headers_list, stretch=1)
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
        self.headers_list.clear()
        self.headers_list.addItems(result.headers)
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
        self.headers_list.clear()
        self.clear_button.setEnabled(False)

    def _set_status(self, message: str, *, is_error: bool) -> None:
        color = "#b42318" if is_error else "#276749"
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color};")
