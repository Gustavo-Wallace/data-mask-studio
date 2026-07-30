from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget


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
            "Em breve, você poderá selecionar um arquivo CSV e anonimizar suas colunas."
        )
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        description.setStyleSheet("font-size: 14px; color: #555;")

        layout = QVBoxLayout()
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(16)
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addStretch()

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

