"""Preferences presentation; the main window owns safe session application."""

from PySide6.QtCore import Qt, Signal
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
    LanguagePreference, ThemePreference, PreferencesError, PreferencesResult, PreferencesService,
)


_ERROR_MESSAGE_IDS = {
    PreferencesError.STORAGE_UNAVAILABLE: "settings.error.storage_unavailable",
    PreferencesError.READ_FAILED: "settings.error.read_failed",
    PreferencesError.INVALID_FILE: "settings.error.invalid_file",
    PreferencesError.INVALID_LANGUAGE: "settings.error.invalid_language",
    PreferencesError.INVALID_THEME: "settings.error.invalid_theme",
    PreferencesError.WRITE_FAILED: "settings.error.write_failed",
}
_LANGUAGE_MESSAGES = (
    (LanguagePreference.SYSTEM, "settings.language.system"),
    (LanguagePreference.ENGLISH, "settings.language.english"),
    (LanguagePreference.BRAZILIAN_PORTUGUESE, "settings.language.portuguese"),
)
_THEME_MESSAGES = (
    (ThemePreference.SYSTEM, "settings.theme.system"),
    (ThemePreference.LIGHT, "settings.theme.light"),
    (ThemePreference.DARK, "settings.theme.dark"),
)


class SettingsWidget(QWidget):
    language_saved = Signal(object)

    def __init__(
        self, preferences_service: PreferencesService | None = None, *,
        translator: Translator | None = None,
        live_language_switching: bool = False,
    ) -> None:
        super().__init__()
        self._preferences = preferences_service or PreferencesService()
        self._translator = translator or Translator("pt-BR")
        self._live_language_switching = live_language_switching
        self._saving = False
        self._acknowledged_language: LanguagePreference | None = None
        self._acknowledged_theme: ThemePreference | None = None
        self._readable = False
        self._feedback_id: str | None = None
        self._feedback_state: FeedbackState = "neutral"
        self._theme_feedback_id: str | None = None
        self._theme_feedback_state: FeedbackState = "neutral"

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
        self.theme_label = QLabel()
        self.theme_label.setStyleSheet(section_title_stylesheet())
        self.theme_combo = ScrollSafeComboBox()
        self.theme_label.setBuddy(self.theme_combo)
        for value, _ in _THEME_MESSAGES:
            self.theme_combo.addItem("", value.value)
        self.theme_notice_label = QLabel()
        self.theme_notice_label.setWordWrap(True)
        set_feedback_state(self.theme_notice_label, "neutral")
        self.theme_save_button = QPushButton()
        self.theme_save_button.setEnabled(False)
        set_button_role(self.theme_save_button, "primary")
        self.theme_status_label = QLabel()
        self.theme_status_label.setWordWrap(True)
        set_feedback_state(self.theme_status_label, "neutral")
        layout.addWidget(self.theme_label)
        layout.addWidget(self.theme_combo, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.theme_notice_label)
        layout.addWidget(self.theme_save_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.theme_status_label)
        layout.addStretch()
        self.retranslate(self._translator)
        self.language_combo.currentIndexChanged.connect(self._selection_changed)
        self.save_button.clicked.connect(self.save_preference)
        self.theme_combo.currentIndexChanged.connect(self._theme_selection_changed)
        self.theme_save_button.clicked.connect(self.save_theme_preference)

    def retranslate(self, translator: Translator) -> None:
        """Update presentation only, retaining stored and unsaved preference state."""
        self._translator = translator
        self.language_label.setText(translator.text("settings.language.label"))
        self.language_combo.setAccessibleName(translator.text("settings.language.label"))
        self.language_combo.setAccessibleDescription(
            translator.text("settings.notice.live" if self._live_language_switching
                            else "settings.language.accessible_description")
        )
        blocked = self.language_combo.blockSignals(True)
        try:
            for index, (_, message_id) in enumerate(_LANGUAGE_MESSAGES):
                self.language_combo.setItemText(index, translator.text(message_id))
        finally:
            self.language_combo.blockSignals(blocked)
        self.save_button.setText(translator.text("settings.save"))
        self.notice_label.setText(translator.text(
            "settings.notice.live" if self._live_language_switching else "settings.notice"
        ))
        self.theme_label.setText(translator.text("settings.theme.label"))
        self.theme_combo.setAccessibleName(translator.text("settings.theme.label"))
        self.theme_combo.setAccessibleDescription(translator.text("settings.theme.notice"))
        blocked = self.theme_combo.blockSignals(True)
        try:
            for index, (_, message_id) in enumerate(_THEME_MESSAGES):
                self.theme_combo.setItemText(index, translator.text(message_id))
        finally:
            self.theme_combo.blockSignals(blocked)
        self.theme_notice_label.setText(translator.text("settings.theme.notice"))
        self.theme_save_button.setText(translator.text("settings.theme.save"))
        self._render_feedback()

    def language_applied(self) -> None:
        """Acknowledge a successful synchronous application by the owning window."""
        self._feedback("settings.saved.applied", state="success")

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
        self._refresh_save_buttons()

    def _display_theme_stored(self, result: PreferencesResult) -> None:
        blocked = self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(result.preferences.theme.value))
        self.theme_combo.blockSignals(blocked)
        self._acknowledged_theme = None
        self._readable = result.error is None
        self._refresh_save_buttons()

    def _refresh_save_buttons(self) -> None:
        available = self._readable and not self._saving
        self.save_button.setEnabled(available and self._acknowledged_language is None)
        self.theme_save_button.setEnabled(available and self._acknowledged_theme is None)

    def _render_feedback(self) -> None:
        self.status_label.setText(
            self._translator.text(self._feedback_id) if self._feedback_id else ""
        )
        set_feedback_state(self.status_label, self._feedback_state)
        self.theme_status_label.setText(
            self._translator.text(self._theme_feedback_id) if self._theme_feedback_id else ""
        )
        set_feedback_state(self.theme_status_label, self._theme_feedback_state)

    def _feedback(self, message_id: str | None = None, *, state: FeedbackState = "neutral") -> None:
        self._feedback_id = message_id
        self._feedback_state = state
        self._render_feedback()

    def load_preferences(self) -> None:
        result = self._preferences.load()
        self._display_stored(result)
        self._display_theme_stored(result)
        self._feedback(
            _ERROR_MESSAGE_IDS[result.error] if result.error else None,
            state="error" if result.error else "neutral",
        )
        self._theme_feedback(
            _ERROR_MESSAGE_IDS[result.error] if result.error else None,
            state="error" if result.error else "neutral",
        )

    def _selection_changed(self) -> None:
        self._acknowledged_language = None
        self._refresh_save_buttons()
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
        self._refresh_save_buttons()
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
                self.language_saved.emit(result.preferences.language)
        finally:
            self._saving = False
            self._refresh_save_buttons()

    def _theme_feedback(self, message_id: str | None = None, *, state: FeedbackState = "neutral") -> None:
        self._theme_feedback_id, self._theme_feedback_state = message_id, state
        self._render_feedback()

    def _theme_selection_changed(self) -> None:
        self._acknowledged_theme = None
        self._refresh_save_buttons()
        if self._readable:
            self._theme_feedback()

    def save_theme_preference(self) -> None:
        # Deliberately no theme-applied signal or visual/session update.
        if self._saving or not self._readable:
            return
        try:
            theme = ThemePreference(self.theme_combo.currentData())
        except (TypeError, ValueError):
            self._display_theme_stored(self._preferences.load())
            self._theme_feedback(_ERROR_MESSAGE_IDS[PreferencesError.INVALID_THEME], state="error")
            return
        if theme is self._acknowledged_theme:
            return
        self._saving = True
        self._refresh_save_buttons()
        try:
            result = self._preferences.save_theme(theme)
            if result.error is not None:
                self._display_theme_stored(self._preferences.load())
                self._theme_feedback(_ERROR_MESSAGE_IDS[result.error], state="error")
            else:
                self._display_theme_stored(result)
                self._acknowledged_theme = result.preferences.theme
                self._theme_feedback("settings.theme.saved", state="success")
        finally:
            self._saving = False
            self._refresh_save_buttons()
