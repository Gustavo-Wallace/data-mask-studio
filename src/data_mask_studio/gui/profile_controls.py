from collections.abc import Callable

from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.styles import section_title_stylesheet
from data_mask_studio.i18n import Translator

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ProfileControls(QWidget):
    """Agrupa apenas os controles visuais de perfis da aba individual."""

    def __init__(
        self,
        *,
        apply_profile: Callable[[], None],
        save_profile: Callable[[], None],
        update_profile: Callable[[], None],
        rename_profile: Callable[[], None],
        delete_profile: Callable[[], None],
        selection_changed: Callable[..., None],
        translator: Translator | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator("pt-BR")
        self.heading_label = QLabel()
        self.heading_label.setStyleSheet(section_title_stylesheet())

        self.profile_combo = ScrollSafeComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.apply_button = QPushButton()
        self.apply_button.clicked.connect(apply_profile)
        self.save_button = QPushButton()
        self.save_button.clicked.connect(save_profile)
        self.update_button = QPushButton()
        self.update_button.clicked.connect(update_profile)
        self.rename_button = QPushButton()
        self.rename_button.clicked.connect(rename_profile)
        self.delete_button = QPushButton()
        self.delete_button.clicked.connect(delete_profile)
        self.profile_combo.currentIndexChanged.connect(selection_changed)

        selection = QHBoxLayout()
        selection.addWidget(self.profile_combo, stretch=1)
        selection.addWidget(self.apply_button)
        actions = QHBoxLayout()
        actions.addWidget(self.save_button)
        actions.addWidget(self.update_button)
        actions.addWidget(self.rename_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.heading_label)
        layout.addLayout(selection)
        layout.addLayout(actions)
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Relabel entry points without changing profile selection or callbacks."""
        self._translator = translator
        self.heading_label.setText(translator.text("masking.profile.heading"))
        self.profile_combo.setAccessibleName(translator.text("masking.profile.heading"))
        for button, message_id in (
            (self.apply_button, "masking.profile.apply"),
            (self.save_button, "masking.profile.save"),
            (self.update_button, "masking.profile.update"),
            (self.rename_button, "masking.profile.rename"),
            (self.delete_button, "masking.profile.delete"),
        ):
            button.setText(translator.text(message_id))
