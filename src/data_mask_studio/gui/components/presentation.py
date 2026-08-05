from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTextEdit,
)


def set_button_role(button: QPushButton, role: str) -> None:
    button.setProperty("role", role)
    button.style().unpolish(button)
    button.style().polish(button)


def configure_path_field(field: QLineEdit, accessible_name: str) -> None:
    field.setAccessibleName(accessible_name)
    field.setMaximumWidth(760)
    field.setClearButtonEnabled(False)
    field.textChanged.connect(field.setToolTip)
    field.setToolTip(field.text())


def configure_table(table: QTableWidget) -> None:
    table.setAlternatingRowColors(True)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    table.verticalHeader().setDefaultSectionSize(30)
    table.horizontalHeader().setMinimumSectionSize(72)


def configure_result_area(
    editor: QPlainTextEdit | QTextEdit, empty_height: int = 280
) -> None:
    """Mantém relatórios vazios compactos e libera expansão quando preenchidos."""

    def update_height() -> None:
        editor.setMaximumHeight(
            16_777_215 if editor.toPlainText().strip() else empty_height
        )

    editor.textChanged.connect(update_height)
    update_height()
