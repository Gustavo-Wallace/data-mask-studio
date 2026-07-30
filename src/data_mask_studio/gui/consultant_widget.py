from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.consultant import (
    ConsultantService,
    ConsultationResult,
    ConsultationStatus,
)
from data_mask_studio.vault import VaultRepository


class ConsultantWidget(QWidget):
    """Interface transitória de consulta exata ao cofre local."""

    def __init__(
        self,
        repository_factory: Callable[[], VaultRepository],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = ConsultantService(repository_factory)

        title = QLabel("Consultar cofre")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: 600;")

        instructions = QLabel(
            "Informe códigos completos, separados por linha, vírgula ou ponto e vírgula."
        )
        instructions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instructions.setWordWrap(True)

        self.codes_input = QPlainTextEdit()
        self.codes_input.setPlaceholderText("CPF_ID-ABCDEFGHI234\nIP-BCDEFGHI234A")
        self.codes_input.setMaximumHeight(110)

        self.consult_button = QPushButton("Consultar")
        self.consult_button.clicked.connect(self.consult)
        self.clear_button = QPushButton("Limpar")
        self.clear_button.clicked.connect(self.clear_consultation)
        self.copy_button = QPushButton("Copiar resultado")
        self.copy_button.clicked.connect(self.copy_result)
        self.copy_button.setEnabled(False)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.consult_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.copy_button)
        button_layout.addStretch()

        self.results_output = QPlainTextEdit()
        self.results_output.setReadOnly(True)
        self.results_output.setPlaceholderText("Os resultados da consulta aparecerão aqui.")

        warning = QLabel(
            "Atenção: os resultados contêm dados sensíveis e devem permanecer "
            "neste ambiente local."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a5a00;")

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(instructions)
        layout.addWidget(self.codes_input)
        layout.addLayout(button_layout)
        layout.addWidget(self.results_output, stretch=1)
        layout.addWidget(warning)
        layout.addWidget(self.status_label)

    def consult(self) -> None:
        results = self._service.consult(self.codes_input.toPlainText())
        if not results:
            self.results_output.clear()
            self.copy_button.setEnabled(False)
            self._set_status("Informe ao menos um código para consultar.", is_error=True)
            return

        rendered = "\n\n".join(_render_result(result) for result in results)
        self.results_output.setPlainText(rendered)
        self.copy_button.setEnabled(bool(rendered))
        found_count = sum(
            result.status is ConsultationStatus.FOUND for result in results
        )
        self._set_status(
            f"Consulta concluída: {found_count} de {len(results)} códigos encontrados.",
            is_error=False,
        )

    def copy_result(self) -> None:
        text = self.results_output.toPlainText()
        if not text:
            self.copy_button.setEnabled(False)
            return
        QApplication.clipboard().setText(text)
        self._set_status("Resultado copiado para a área de transferência.", is_error=False)

    def clear_consultation(self) -> None:
        self.codes_input.clear()
        self.results_output.clear()
        self.status_label.clear()
        self.copy_button.setEnabled(False)
        self.codes_input.setFocus()

    def _set_status(self, message: str, *, is_error: bool) -> None:
        color = "#b42318" if is_error else "#276749"
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color};")


def _render_result(result: ConsultationResult) -> str:
    if result.status is not ConsultationStatus.FOUND or result.mapping is None:
        return f"Código: {result.code}\n{result.message or 'Consulta indisponível.'}"

    mapping = result.mapping
    return "\n".join(
        (
            f"Código: {mapping.code}",
            f"Prefixo: {mapping.prefix}",
            f"Cabeçalho de origem: {mapping.source_header}",
            f"Valor original: {mapping.original_value}",
            f"Primeira aparição: {mapping.first_seen}",
            f"Última aparição: {mapping.last_seen}",
            f"Ocorrências: {mapping.occurrence_count}",
        )
    )

