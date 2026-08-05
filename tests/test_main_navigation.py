import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabWidget

from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.components import EmptyStateTable, PageHeader
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository


class FixedKeyProvider:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def get_key(self) -> bytes:
        return self.key


class FakeProtector:
    def protect(self, data: bytes) -> bytes:
        return b"protected:" + data

    def unprotect(self, data: bytes) -> bytes:
        return data.removeprefix(b"protected:")


def build_window(tmp_path: Path) -> MainWindow:
    local = tmp_path / "local"
    local.mkdir()
    paths = EnvironmentPaths(
        directory=local,
        hmac_key_path=local / "secret.key",
        vault_key_path=local / "vault_key.dpapi",
        vault_database_path=local / "vault.db",
        profiles_path=local / "profiles.json",
    )
    cipher = VaultCipher(b"V" * 32)
    return MainWindow(
        key_provider=FixedKeyProvider(b"H" * 32),
        vault_repository_factory=lambda: VaultRepository(paths.vault_database_path, cipher),
        profile_service=ProfileService(ProfileRepository(paths.profiles_path)),
        backup_paths=paths,
        vault_key_provider=FixedKeyProvider(b"V" * 32),
        data_protector=FakeProtector(),
    )


def test_sidebar_navigation_order_accessibility_and_state(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    expected = [
        "Anonimizar CSV",
        "Anonimização em lote",
        "Restaurar CSV",
        "Restaurar HTML",
        "Restauração em lote",
        "Consultar cofre",
        "Backup e recuperação",
        "Integridade",
        "Cofre e manutenção",
    ]

    assert [button.text() for button in window.navigation.buttons] == expected
    assert all(button.accessibleName() for button in window.navigation.buttons)
    assert window.navigation.accessibleName() == "Navegação principal"
    assert window.navigation.buttons[0].isChecked()
    assert "tabs" not in window.__dict__

    window.consultant_widget.codes_input.setPlainText("CPF-ABCDEFGHI234")
    window.set_current_page(5)
    window.set_current_page(0)
    window.set_current_page(5)
    assert window.consultant_widget.codes_input.toPlainText() == "CPF-ABCDEFGHI234"

    window.close()
    application.processEvents()


def test_sidebar_keyboard_navigation_and_current_highlight(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.show()
    first = window.navigation.buttons[0]
    first.setFocus()

    QTest.keyClick(first, Qt.Key.Key_Down)
    application.processEvents()

    assert window.current_page_index() == 1
    assert window.navigation.buttons[1].isChecked()
    assert window.navigation.buttons[1].hasFocus()

    window.close()
    application.processEvents()


def test_sidebar_mouse_click_changes_the_visible_page(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.show()

    window.navigation.buttons[6].click()
    application.processEvents()

    assert window.current_page_index() == 6
    assert window.page_stack.currentWidget() is window.page_shells[6]
    assert window.navigation.buttons[6].isChecked()

    window.navigation.buttons[2].click()
    application.processEvents()

    assert window.current_page_index() == 2
    assert window.page_stack.currentWidget() is window.page_shells[2]

    window.close()
    application.processEvents()


def test_common_headers_roles_paths_empty_states_and_internal_tabs(
    tmp_path: Path,
) -> None:
    application = create_application([])
    window = build_window(tmp_path)

    assert len(window.findChildren(PageHeader)) == 9
    assert all(shell.header.title_label.text() for shell in window.page_shells)
    primary_buttons = (
        window.anonymization_widget.generate_button,
        window.batch_widget.start_button,
        window.restoration_widget.generate_button,
        window.html_restoration_widget.generate_button,
        window.batch_restoration_widget.start_button,
        window.backup_widget.create_button,
        window.integrity_widget.run_button,
    )
    assert all(button.property("role") == "primary" for button in primary_buttons)

    full_path = str(tmp_path / ("diretorio-" * 20) / "arquivo.csv")
    window.anonymization_widget.path_field.setText(full_path)
    assert window.anonymization_widget.path_field.text() == full_path
    assert window.anonymization_widget.path_field.toolTip() == full_path
    assert window.anonymization_widget.path_field.maximumWidth() == 760
    assert isinstance(window.batch_widget.file_table, EmptyStateTable)
    assert "Nenhum arquivo" in window.batch_widget.file_table.accessibleDescription()
    report = window.maintenance_widget.overview_output
    assert report.maximumHeight() == 280
    report.setPlainText("Relatório técnico seguro")
    assert report.maximumHeight() == 16_777_215
    report.clear()
    assert report.maximumHeight() == 280
    assert [
        window.maintenance_widget.sections.tabText(index)
        for index in range(window.maintenance_widget.sections.count())
    ] == ["Visão geral", "Validar backup", "Temporários", "Compactação"]
    assert window.findChildren(QTabWidget) == [window.maintenance_widget.sections]

    window.close()
    application.processEvents()


def test_layout_remains_visible_at_supported_sizes(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.show()

    for width, height in ((1100, 760), (1280, 800), (1920, 1080)):
        window.resize(width, height)
        application.processEvents()
        assert window.navigation.height() == window.centralWidget().height()
        assert all(
            button.isVisible() and button.geometry().bottom() < window.navigation.height()
            for button in window.navigation.buttons
        )
        for index, shell in enumerate(window.page_shells):
            window.set_current_page(index)
            application.processEvents()
            assert shell.header.isVisible()
            assert shell.scroll_area.viewport().width() > 0
            assert shell.scroll_area.viewport().height() > 0

    window.close()
    application.processEvents()
