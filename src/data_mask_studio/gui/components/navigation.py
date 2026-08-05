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


@dataclass(frozen=True, slots=True)
class NavigationItem:
    group: str
    title: str
    accessible_description: str


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

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navigationCategory")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName(f"Categoria {title.title()}")
        self.label = QLabel(title)
        self.label.setObjectName("navigationGroup")
        self.label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        divider = QFrame()
        divider.setObjectName("navigationDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 0, 3, 0)
        layout.setSpacing(8)
        layout.addWidget(self.label)
        layout.addWidget(divider, stretch=1)


class SidebarNavigation(QWidget):
    """Navegação principal fixa, acessível e inteiramente visível."""

    current_changed = Signal(int)

    def __init__(
        self, items: tuple[NavigationItem, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sidebarNavigation")
        self.setAccessibleName("Navegação principal")
        self.setFixedWidth(220)
        self._current_index = 0
        self._buttons: list[QPushButton] = []
        self._categories: list[NavigationCategory] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 18)
        layout.setSpacing(4)

        self.identity = ApplicationIdentity()
        layout.addWidget(self.identity)
        layout.addSpacing(12)
        previous_group = ""
        for index, item in enumerate(items):
            if item.group != previous_group:
                category = NavigationCategory(item.group)
                self._categories.append(category)
                layout.addSpacing(12 if previous_group else 2)
                layout.addWidget(category)
                previous_group = item.group
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
            layout.addWidget(button)
        layout.addStretch()
        if self._buttons:
            self._buttons[0].setChecked(True)

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
