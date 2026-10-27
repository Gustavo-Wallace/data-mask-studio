from collections.abc import Callable

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QTextCursor
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
from data_mask_studio.gui.components import EmptyStatePlainTextEdit
from data_mask_studio.gui.components.presentation import (
    FeedbackState, configure_result_area, set_feedback_state,
)
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.i18n import Translator
from data_mask_studio.vault import VaultRepository
from data_mask_studio.vault.composite_models import CompositeMapping
from data_mask_studio.composite_text import composite_text


class ConsultantWidget(QWidget):
    """Interface transitória de consulta exata ao cofre local."""

    def __init__(
        self,
        repository_factory: Callable[[], VaultRepository],
        parent: QWidget | None = None,
        *, translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = ConsultantService(repository_factory)
        self._translator = translator or Translator("pt-BR")
        self._results: list[ConsultationResult] = []
        self._status_message: tuple[str, dict[str, int]] | None = None

        self.codes_input = QPlainTextEdit()
        self.codes_input.setPlaceholderText("CPF_ID-ABCDEFGHI234\nIP-BCDEFGHI234A")
        self.codes_input.setMaximumHeight(110)

        self.consult_button = QPushButton()
        self.consult_button.clicked.connect(self.consult)
        self.clear_button = QPushButton()
        self.clear_button.clicked.connect(self.clear_consultation)
        self.copy_button = QPushButton()
        self.copy_button.clicked.connect(self.copy_result)
        self.copy_button.setEnabled(False)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.consult_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.copy_button)
        button_layout.addStretch()

        self.results_output = EmptyStatePlainTextEdit("")
        self.results_output.setReadOnly(True)
        configure_result_area(self.results_output, 160)

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setTextFormat(Qt.TextFormat.PlainText)
        set_feedback_state(self.warning_label, "warning")

        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        set_feedback_state(self.status_label, "neutral")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addWidget(self.codes_input)
        layout.addLayout(button_layout)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.results_output, stretch=1)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Relabel the cached consultation; never reopen or query the vault."""
        self._translator = translator
        for button, message_id in (
            (self.consult_button, "consultant.consult"),
            (self.clear_button, "consultant.clear"),
            (self.copy_button, "consultant.copy"),
        ):
            button.setText(translator.text(message_id))
        self.codes_input.setAccessibleName(translator.text("consultant.input_accessible"))
        guidance = translator.text("consultant.input_description")
        self.codes_input.setAccessibleDescription(guidance)
        self.codes_input.setToolTip(guidance)
        self.results_output.setAccessibleName(translator.text("consultant.results_accessible"))
        self.results_output.empty_text = translator.text("consultant.empty")
        self.results_output.setAccessibleDescription(self.results_output.empty_text)
        self.results_output.viewport().update()
        self.warning_label.setText(translator.text("consultant.warning"))
        if self._results:
            self._render_results(preserve_position=True)
        self._render_status()

    def _render_results(self, *, preserve_position: bool = False) -> None:
        output = self.results_output
        text = "\n\n".join(_render_result(result, self._translator) for result in self._results)
        if not preserve_position:
            output.setPlainText(text)
            return

        # Anchor within each logical line from its end: translated label lengths
        # may change, but selected data and the report's line structure do not.
        cursor = output.textCursor()
        positions = []
        for position in (cursor.anchor(), cursor.position()):
            cursor.setPosition(position)
            block = cursor.block()
            positions.append((block.blockNumber(), block.length() - 1 - cursor.positionInBlock()))
        vertical = output.verticalScrollBar().value()
        horizontal = output.horizontalScrollBar().value()
        with QSignalBlocker(output):
            output.setPlainText(text)
            cursor = output.textCursor()
            for index, (number, offset) in enumerate(positions):
                block = output.document().findBlockByNumber(number)
                position = block.position() + max(0, block.length() - 1 - offset)
                cursor.setPosition(position, QTextCursor.MoveMode.MoveAnchor if index == 0
                                   else QTextCursor.MoveMode.KeepAnchor)
            output.setTextCursor(cursor)
            output.verticalScrollBar().setValue(vertical)
            output.horizontalScrollBar().setValue(horizontal)

    def consult(self) -> None:
        results = self._service.consult(self.codes_input.toPlainText())
        self._results = results
        if not results:
            self.results_output.clear()
            self.copy_button.setEnabled(False)
            self._set_status_id("consultant.status.input_required", state="error")
            return

        self._render_results()
        self.copy_button.setEnabled(bool(self.results_output.toPlainText()))
        found_count = sum(
            result.status is ConsultationStatus.FOUND for result in results
        )
        self._set_status_id(
            "consultant.status.completed", found=found_count, count=len(results),
            state="error" if any(result.status is ConsultationStatus.RECOVERY_FAILED for result in results)
            else "warning" if found_count < len(results) else "success",
        )

    def copy_result(self) -> None:
        text = self.results_output.toPlainText()
        if not text:
            self.copy_button.setEnabled(False)
            return
        QApplication.clipboard().setText(text)
        self._set_status_id("consultant.status.copied", state="success")

    def clear_consultation(self) -> None:
        self.codes_input.clear()
        self._results = []
        self._status_message = None
        self.results_output.clear()
        self.status_label.clear()
        set_feedback_state(self.status_label, "neutral")
        self.copy_button.setEnabled(False)
        self.codes_input.setFocus()

    def _set_status_id(
        self, message_id: str, *, state: FeedbackState = "neutral", **parameters: int,
    ) -> None:
        self._status_message = (message_id, parameters)
        self._render_status()
        set_feedback_state(self.status_label, state)

    def _set_status(
        self, message: str, *, is_error: bool, state: FeedbackState = "neutral",
    ) -> None:
        """Keep literal feedback literal, including existing internal callers."""
        self._status_message = None
        self.status_label.setText(message)
        set_feedback_state(self.status_label, "error" if is_error else state)

    def _render_status(self) -> None:
        if self._status_message is None:
            return
        message_id, parameters = self._status_message
        if message_id == "consultant.status.completed":
            text = self._translator.plural(message_id, parameters["count"], found=parameters["found"])
        else:
            text = self._translator.text(message_id, **parameters)
        self.status_label.setText(text)


def _render_result(result: ConsultationResult, translator: Translator | None = None) -> str:
    translator = translator or Translator("pt-BR")
    text = translator.text
    if result.status is not ConsultationStatus.FOUND or result.mapping is None:
        message_ids = {
            ConsultationStatus.INVALID: "consultant.result.invalid",
            ConsultationStatus.NOT_FOUND: "consultant.result.not_found",
            ConsultationStatus.RECOVERY_FAILED: "consultant.result.recovery_failed",
        }
        message_id = message_ids.get(result.status)
        message = (text(message_id) if message_id else
                   result.message or text("consultant.unavailable"))
        return text("consultant.result.code", code=result.code) + "\n" + message

    mapping = result.mapping
    if isinstance(mapping, CompositeMapping):
        lines = [text("consultant.result.code", code=mapping.code),
                 text("consultant.result.type_composite"),
                 text("consultant.result.prefix", prefix=mapping.prefix),
                 text("consultant.result.component_count", count=mapping.component_count),
                 text("consultant.result.canonical", value=composite_text(mapping.canonical_values)),
                 text("consultant.result.first_seen", timestamp=mapping.first_seen),
                 text("consultant.result.last_seen", timestamp=mapping.last_seen),
                 text("consultant.result.total", count=mapping.occurrence_count)]
        for index, rule in enumerate(mapping.normalization_rules, 1):
            lines.append(text("consultant.result.component", index=index,
                              normalization=text(f"masking.normalization.{rule.value}")))
        lines.append(translator.plural("consultant.result.variations", len(mapping.variations)))
        lines.append(text("consultant.result.restore_unique" if len(mapping.variations) == 1
                          else "consultant.result.restore_ambiguous"))
        for index, variation in enumerate(mapping.variations, 1):
            lines.extend((text("consultant.result.original_variation", index=index,
                               value=composite_text(variation.original_values)),
                          text("consultant.result.variation_count", count=variation.occurrence_count),
                          text("consultant.result.variation_first_seen", timestamp=variation.first_seen),
                          text("consultant.result.variation_last_seen", timestamp=variation.last_seen)))
        return "\n".join(lines)
    primary = mapping.variations[0]
    lines = [
        text("consultant.result.code", code=mapping.code),
        text("consultant.result.type_scalar"),
        text("consultant.result.prefix", prefix=mapping.prefix),
        text("consultant.result.source_header", header=mapping.source_header),
        text("consultant.result.normalization",
             normalization=text(f"masking.normalization.{mapping.normalization_rule.value}")),
        text("consultant.result.first_seen", timestamp=mapping.first_seen),
        text("consultant.result.last_seen", timestamp=mapping.last_seen),
        text("consultant.result.primary_value", value=primary.original_value),
        text("consultant.result.variation_first_seen", timestamp=primary.first_seen),
        text("consultant.result.variation_last_seen", timestamp=primary.last_seen),
        text("consultant.result.primary_count", count=primary.occurrence_count),
    ]
    for index, variation in enumerate(mapping.variations[1:], start=1):
        lines.extend(
            (
                "",
                text("consultant.result.other_variation", index=index, value=variation.original_value),
                text("consultant.result.variation_first_seen", timestamp=variation.first_seen),
                text("consultant.result.variation_last_seen", timestamp=variation.last_seen),
                text("consultant.result.variation_count", count=variation.occurrence_count),
            )
        )
    lines.extend(("", text("consultant.result.total", count=mapping.occurrence_count)))
    return "\n".join(lines)
