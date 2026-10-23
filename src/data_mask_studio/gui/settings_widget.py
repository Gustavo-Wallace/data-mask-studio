"""Presentation of preferences only; no vault access or language application."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from data_mask_studio.gui.components.presentation import set_button_role, set_feedback_state
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.styles import section_title_stylesheet
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.preferences import (
    LanguagePreference, PreferencesError, PreferencesResult, PreferencesService,
)


class SettingsWidget(QWidget):
    def __init__(self, preferences_service: PreferencesService | None = None) -> None:
        super().__init__()
        self._preferences = preferences_service or PreferencesService()
        self._saving = False
        self._acknowledged_language: LanguagePreference | None = None
        self._readable = False

        self.language_label = QLabel("Idioma do aplicativo")
        self.language_label.setStyleSheet(section_title_stylesheet())
        self.language_combo = ScrollSafeComboBox()
        self.language_combo.setAccessibleName("Idioma do aplicativo")
        self.language_combo.setAccessibleDescription(
            "Preferência salva para tradução futura; a interface continua em português."
        )
        self.language_label.setBuddy(self.language_combo)
        for text, value in (
            ("Automático (Windows)", LanguagePreference.SYSTEM),
            ("English", LanguagePreference.ENGLISH),
            ("Português (Brasil)", LanguagePreference.BRAZILIAN_PORTUGUESE),
        ):
            self.language_combo.addItem(text, value.value)

        self.save_button = QPushButton("Salvar preferência")
        self.save_button.setEnabled(False)
        set_button_role(self.save_button, "primary")
        self.notice_label = QLabel(
            "A preferência será aplicada à interface quando a tradução estiver disponível. "
            "Nesta etapa, o aplicativo continua em português."
        )
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
        self.language_combo.currentIndexChanged.connect(self._selection_changed)
        self.save_button.clicked.connect(self.save_preference)

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

    def _feedback(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        set_feedback_state(self.status_label, "error" if error else "neutral")

    def load_preferences(self) -> None:
        result = self._preferences.load()
        self._display_stored(result)
        self._feedback(result.error.message if result.error else "", error=bool(result.error))

    def _selection_changed(self) -> None:
        self._acknowledged_language = None
        self.save_button.setEnabled(self._readable and not self._saving)
        if self._readable:
            self._feedback("")

    def save_preference(self) -> None:
        if self._saving or not self._readable:
            return
        try:
            language = LanguagePreference(self.language_combo.currentData())
        except (TypeError, ValueError):
            self._display_stored(self._preferences.load())
            self._feedback(PreferencesError.INVALID_LANGUAGE.message, error=True)
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
                self._feedback(result.error.message, error=True)
            else:
                self._display_stored(result)
                self._acknowledged_language = result.preferences.language
                self.status_label.setText("Preferência de idioma salva.")
                set_feedback_state(self.status_label, "success")
        finally:
            self._saving = False
            self.save_button.setEnabled(
                self._readable and self._acknowledged_language is None
            )
