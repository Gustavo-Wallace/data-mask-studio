from collections.abc import Callable

from PySide6.QtWidgets import (
    QComboBox,
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        label = QLabel("Perfil de configuração")
        label.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.apply_button = QPushButton("Aplicar perfil")
        self.apply_button.clicked.connect(apply_profile)
        self.save_button = QPushButton("Salvar como perfil")
        self.save_button.clicked.connect(save_profile)
        self.update_button = QPushButton("Atualizar perfil")
        self.update_button.clicked.connect(update_profile)
        self.rename_button = QPushButton("Renomear")
        self.rename_button.clicked.connect(rename_profile)
        self.delete_button = QPushButton("Excluir")
        self.delete_button.clicked.connect(delete_profile)
        self.profile_combo.currentIndexChanged.connect(selection_changed)

        controls = QHBoxLayout()
        controls.addWidget(self.profile_combo, stretch=1)
        controls.addWidget(self.apply_button)
        controls.addWidget(self.save_button)
        controls.addWidget(self.update_button)
        controls.addWidget(self.rename_button)
        controls.addWidget(self.delete_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(label)
        layout.addLayout(controls)
