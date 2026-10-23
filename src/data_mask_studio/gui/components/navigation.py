from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.i18n import Translator


def _navigation_divider() -> QFrame:
    divider = QFrame()
    divider.setObjectName("navigationDivider")
    divider.setFrameShape(QFrame.Shape.HLine)
    divider.setFixedHeight(1)
    divider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return divider


@dataclass(frozen=True, slots=True)
class NavigationItem:
    group: str
    title: str
    accessible_description: str
    footer: bool = False
    group_message_id: str | None = None
    title_message_id: str | None = None
    description_message_id: str | None = None


class ApplicationIdentity(QWidget):
    """Identidade textual compacta, sem comportamento interativo."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("applicationIdentity")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Data Mask Studio")

        self.monogram_label = QLabel("DMS")
        self.monogram_label.setObjectName("identityMonogram")
        self.name_label = QLabel("Data Mask Studio")
        self.name_label.setObjectName("identityName")
        for label in (self.monogram_label, self.name_label):
            label.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        layout.addWidget(self.monogram_label)
        layout.addWidget(
            self.name_label,
            stretch=1,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )


class NavigationCategory(QWidget):
    """Separador semântico e não interativo entre grupos de páginas."""

    def __init__(
        self, title: str, parent: QWidget | None = None,
        *, translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("navigationCategory")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._translator = translator or Translator("pt-BR")
        self.label = QLabel(title)
        self.label.setObjectName("navigationGroup")
        self.label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        divider = _navigation_divider()
        layout = QHBoxLayout(self)
        # Alinha a categoria ao conteúdo dos botões, não à borda do controle.
        layout.setContentsMargins(
            METRICS["button_padding"] + METRICS["navigation_marker_width"],
            0,
            METRICS["button_padding"] + 1,
            0,
        )
        layout.setSpacing(8)
        layout.addWidget(self.label)
        layout.addWidget(divider, stretch=1)
        self.set_title(title, self._translator)

    def set_title(self, title: str, translator: Translator) -> None:
        self._translator = translator
        self.label.setText(title)
        self.setAccessibleName(translator.text(
            "navigation.category_accessible", category=title.title(),
        ))


class SidebarNavigation(QWidget):
    """Navegação principal fixa, acessível e inteiramente visível."""

    current_changed = Signal(int)
    about_requested = Signal()

    def __init__(
        self, items: tuple[NavigationItem, ...], parent: QWidget | None = None,
        *, translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sidebarNavigation")
        self._translator = translator or Translator("pt-BR")
        self._items = items
        self.setFixedWidth(220)
        self._current_index = 0
        self._buttons: list[QPushButton] = []
        self._categories: list[NavigationCategory] = []
        self._category_items: list[NavigationItem] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 18)
        layout.setSpacing(4)

        self.identity = ApplicationIdentity()
        layout.addWidget(self.identity)
        layout.addSpacing(METRICS["button_padding"])
        previous_group = ""
        footer_buttons = []
        for index, item in enumerate(items):
            group_identity = item.group_message_id or item.group
            if not item.footer and group_identity != previous_group:
                category = NavigationCategory(item.group, translator=self._translator)
                self._categories.append(category)
                self._category_items.append(item)
                layout.addSpacing(METRICS["button_padding"] if previous_group else 2)
                layout.addWidget(category)
                previous_group = group_identity
            button = QPushButton(item.title)
            button.setObjectName("navigationItem")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setAccessibleName(item.title)
            button.setAccessibleDescription(item.accessible_description)
            button.installEventFilter(self)
            button.clicked.connect(
                lambda _checked=False, page_index=index: self.set_current_index(
                    page_index
                )
            )
            self._group.addButton(button, index)
            self._buttons.append(button)
            if item.footer:
                footer_buttons.append(button)
            else:
                layout.addWidget(button)
        layout.addStretch()
        self.footer_divider = _navigation_divider()
        layout.addWidget(self.footer_divider)
        for button in footer_buttons:
            layout.addWidget(button)
        self.about_button = QPushButton()
        self.about_button.setObjectName("navigationUtility")
        self.about_button.setAutoDefault(False)
        self.about_button.clicked.connect(self.about_requested.emit)
        layout.addWidget(self.about_button)
        if self._buttons:
            self._buttons[0].setChecked(True)
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Update labels without replacing buttons, routes or connections."""
        self._translator = translator
        self.setAccessibleName(translator.text("navigation.main"))
        self.about_button.setText(translator.text("common.about"))
        self.about_button.setAccessibleName(translator.text(
            "about.title", application="Data Mask Studio",
        ))
        for category, item in zip(self._categories, self._category_items, strict=True):
            title = (
                translator.text(item.group_message_id)
                if item.group_message_id else item.group
            )
            category.set_title(title, translator)
        for button, item in zip(self._buttons, self._items, strict=True):
            title = (
                translator.text(item.title_message_id)
                if item.title_message_id else item.title
            )
            description = (
                translator.text(item.description_message_id)
                if item.description_message_id else item.accessible_description
            )
            button.setText(title)
            button.setAccessibleName(title)
            button.setAccessibleDescription(description)

    @property
    def buttons(self) -> tuple[QPushButton, ...]:
        return tuple(self._buttons)

    @property
    def categories(self) -> tuple[NavigationCategory, ...]:
        return tuple(self._categories)

    def current_index(self) -> int:
        return self._current_index

    def set_current_index(self, index: int) -> None:
        if not 0 <= index < len(self._buttons) or not self._buttons[index].isEnabled():
            return
        changed = self._current_index != index
        self._current_index = index
        self._buttons[index].setChecked(True)
        if changed:
            self.current_changed.emit(index)

    def set_page_enabled(self, index: int, enabled: bool) -> None:
        self._buttons[index].setEnabled(enabled)

    def page_enabled(self, index: int) -> bool:
        return self._buttons[index].isEnabled()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in self._buttons and event.type() == QEvent.Type.KeyPress:
            key = event.key()  # type: ignore[attr-defined]
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                direction = -1 if key == Qt.Key.Key_Up else 1
                current = self._buttons.index(watched)  # type: ignore[arg-type]
                for distance in range(1, len(self._buttons) + 1):
                    candidate = (current + direction * distance) % len(self._buttons)
                    if self._buttons[candidate].isEnabled():
                        self._buttons[candidate].setFocus()
                        self.set_current_index(candidate)
                        return True
        return super().eventFilter(watched, event)
