"""Presentation of preferences only; no vault access or language application."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from data_mask_studio.gui.components.presentation import (
    FeedbackState, set_button_role, set_feedback_state,
)
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.styles import section_title_stylesheet
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import (
    LanguagePreference, PreferencesError, PreferencesResult, PreferencesService,
)


_ERROR_MESSAGE_IDS = {
    PreferencesError.STORAGE_UNAVAILABLE: "settings.error.storage_unavailable",
    PreferencesError.READ_FAILED: "settings.error.read_failed",
    PreferencesError.INVALID_FILE: "settings.error.invalid_file",
    PreferencesError.INVALID_LANGUAGE: "settings.error.invalid_language",
    PreferencesError.WRITE_FAILED: "settings.error.write_failed",
}
_LANGUAGE_MESSAGES = (
    (LanguagePreference.SYSTEM, "settings.language.system"),
    (LanguagePreference.ENGLISH, "settings.language.english"),
    (LanguagePreference.BRAZILIAN_PORTUGUESE, "settings.language.portuguese"),
)


class SettingsWidget(QWidget):
    def __init__(
        self, preferences_service: PreferencesService | None = None, *,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self._preferences = preferences_service or PreferencesService()
        self._translator = translator or Translator("pt-BR")
        self._saving = False
        self._acknowledged_language: LanguagePreference | None = None
        self._readable = False
        self._feedback_id: str | None = None
        self._feedback_state: FeedbackState = "neutral"

        self.language_label = QLabel()
        self.language_label.setStyleSheet(section_title_stylesheet())
        self.language_combo = ScrollSafeComboBox()
        self.language_label.setBuddy(self.language_combo)
        for value, _ in _LANGUAGE_MESSAGES:
            self.language_combo.addItem("", value.value)

        self.save_button = QPushButton()
        self.save_button.setEnabled(False)
        set_button_role(self.save_button, "primary")
        self.notice_label = QLabel()
        self.notice_label.setWordWrap(True)
        set_feedback_state(self.notice_label, "neutral")
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        set_feedback_state(self.status_label, "neutral")

        layout = QVBoxLayout(self)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addWidget(self.language_label)
        layout.addWidget(self.language_combo, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.notice_label)
        layout.addWidget(self.save_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.retranslate(self._translator)
        self.language_combo.currentIndexChanged.connect(self._selection_changed)
        self.save_button.clicked.connect(self.save_preference)

    def retranslate(self, translator: Translator) -> None:
        """Update presentation only, retaining stored and unsaved preference state."""
        self._translator = translator
        self.language_label.setText(translator.text("settings.language.label"))
        self.language_combo.setAccessibleName(translator.text("settings.language.label"))
        self.language_combo.setAccessibleDescription(
            translator.text("settings.language.accessible_description")
        )
        blocked = self.language_combo.blockSignals(True)
        try:
            for index, (_, message_id) in enumerate(_LANGUAGE_MESSAGES):
                self.language_combo.setItemText(index, translator.text(message_id))
        finally:
            self.language_combo.blockSignals(blocked)
        self.save_button.setText(translator.text("settings.save"))
        self.notice_label.setText(translator.text("settings.notice"))
        self._render_feedback()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Reload on each visit, including changes made by another instance.
        # Loading never creates a preferences document or resolves an OS language.
        self.load_preferences()

    def _display_stored(self, result: PreferencesResult) -> None:
        blocked = self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(
            self.language_combo.findData(result.preferences.language.value)
        )
        self.language_combo.blockSignals(blocked)
        self._acknowledged_language = None
        self._readable = result.error is None
        self.save_button.setEnabled(self._readable and not self._saving)

    def _render_feedback(self) -> None:
        self.status_label.setText(
            self._translator.text(self._feedback_id) if self._feedback_id else ""
        )
        set_feedback_state(self.status_label, self._feedback_state)

    def _feedback(self, message_id: str | None = None, *, state: FeedbackState = "neutral") -> None:
        self._feedback_id = message_id
        self._feedback_state = state
        self._render_feedback()

    def load_preferences(self) -> None:
        result = self._preferences.load()
        self._display_stored(result)
        self._feedback(
            _ERROR_MESSAGE_IDS[result.error] if result.error else None,
            state="error" if result.error else "neutral",
        )

    def _selection_changed(self) -> None:
        self._acknowledged_language = None
        self.save_button.setEnabled(self._readable and not self._saving)
        if self._readable:
            self._feedback()

    def save_preference(self) -> None:
        if self._saving or not self._readable:
            return
        try:
            language = LanguagePreference(self.language_combo.currentData())
        except (TypeError, ValueError):
            self._display_stored(self._preferences.load())
            self._feedback(_ERROR_MESSAGE_IDS[PreferencesError.INVALID_LANGUAGE], state="error")
            return
        if language is self._acknowledged_language:
            return
        self._saving = True
        self.save_button.setEnabled(False)
        try:
            result = self._preferences.save_language(language)
            if result.error is not None:
                # Show the actual stored choice, not an unsaved attempted value.
                self._display_stored(self._preferences.load())
                self._feedback(_ERROR_MESSAGE_IDS[result.error], state="error")
            else:
                self._display_stored(result)
                self._acknowledged_language = result.preferences.language
                self._feedback("settings.saved", state="success")
        finally:
            self._saving = False
            self.save_button.setEnabled(
                self._readable and self._acknowledged_language is None
            )
