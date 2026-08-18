import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QTabWidget

from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.components import (
    EmptyStatePlainTextEdit,
    EmptyStateTable,
    EmptyStateTextEdit,
    PageHeader,
)
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
    identity = window.navigation.identity
    assert identity.monogram_label.text() == "DMS"
    assert identity.name_label.text() == "Data Mask Studio"
    assert not hasattr(identity, "subtitle_label")
    assert identity.accessibleName() == "Data Mask Studio"
    assert identity.focusPolicy() is Qt.FocusPolicy.NoFocus
    assert identity.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert identity.findChildren(QPushButton) == []
    assert [category.label.text() for category in window.navigation.categories] == [
        "PROCESSAMENTO",
        "RESTAURAÇÃO",
        "COFRE",
    ]
    assert all(
        category.focusPolicy() is Qt.FocusPolicy.NoFocus
        and category.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        and category.findChildren(QPushButton) == []
        for category in window.navigation.categories
    )
    assert window.navigation.buttons[0].isChecked()
    assert "tabs" not in window.__dict__

    window.consultant_widget.codes_input.setPlainText("CPF-ABCDEFGHI234")
    window.set_current_page(5)
    window.set_current_page(0)
    window.set_current_page(5)
    assert window.consultant_widget.codes_input.toPlainText() == "CPF-ABCDEFGHI234"

    window.close()
    application.processEvents()


def test_initial_window_respects_simulated_available_geometry(
    tmp_path: Path, monkeypatch,
) -> None:
    application = create_application([])
    available = QRect(1600, 80, 1024, 700)
    monkeypatch.setattr(
        MainWindow,
        "_available_screen_geometry",
        lambda self, screen=None: QRect(available),
    )

    window = build_window(tmp_path)
    window.show()
    application.processEvents()

    assert window.minimumWidth() <= available.width()
    assert window.minimumHeight() <= available.height()
    assert available.contains(window.frameGeometry())
    assert window.width() <= available.width()
    assert window.height() <= available.height()

    window.close()
    application.processEvents()


def test_maximized_window_uses_qt_available_screen_geometry(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.showMaximized()
    application.processEvents()

    available = window.screen().availableGeometry()
    assert available.contains(window.frameGeometry())
    assert window.isMaximized()

    shell = window.page_shells[4]
    window.set_current_page(4)
    application.processEvents()
    shell.scroll_area.verticalScrollBar().setValue(
        shell.scroll_area.verticalScrollBar().maximum()
    )
    application.processEvents()
    status = window.batch_restoration_widget.status_label
    status_top = status.mapTo(shell.scroll_area.viewport(), QPoint(0, 0)).y()
    assert status_top >= 0
    assert status_top + status.height() <= shell.scroll_area.viewport().height()

    window.close()
    application.processEvents()


def test_long_pages_keep_vertical_scrolling_available(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.resize(960, 640)
    window.show()

    for index, shell in enumerate(window.page_shells):
        window.set_current_page(index)
        application.processEvents()
        scrollbar = shell.scroll_area.verticalScrollBar()
        assert scrollbar.maximum() >= 0
        if shell.content.sizeHint().height() > shell.scroll_area.viewport().height():
            assert scrollbar.maximum() > 0
            scrollbar.setValue(scrollbar.maximum())
            application.processEvents()
            assert scrollbar.value() == scrollbar.maximum()

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


def test_navigation_categories_do_not_trigger_page_changes(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.show()

    for category in window.navigation.categories:
        QTest.mouseClick(category, Qt.MouseButton.LeftButton)
        application.processEvents()

    assert window.current_page_index() == 0
    assert window.navigation.buttons[0].isChecked()

    window.close()
    application.processEvents()


def test_about_control_opens_public_information_dialog(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.show()

    window.navigation.about_button.click()
    application.processEvents()

    assert window.about_dialog is not None
    assert window.about_dialog.isVisible()
    assert window.about_dialog.windowTitle() == "Sobre o Data Mask Studio"
    assert window.current_page_index() == 0

    window.about_dialog.close()
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
    assert isinstance(window.consultant_widget.results_output, EmptyStatePlainTextEdit)
    assert isinstance(window.batch_restoration_widget.summary_output, EmptyStateTextEdit)
    assert window.backup_widget.restore_button.text() == "Restaurar backup…"
    assert window.backup_widget.restore_button.property("role") == "attention"
    assert "QLabel { background: transparent; }" in application.styleSheet()
    assert "#bdc8d5" in application.styleSheet()
    assert "#8592a3" in application.styleSheet()
    assert [
        window.maintenance_widget.sections.tabText(index)
        for index in range(window.maintenance_widget.sections.count())
    ] == ["Visão geral", "Validar backup", "Temporários", "Compactação"]
    assert window.findChildren(QTabWidget) == [window.maintenance_widget.sections]

    window.close()
    application.processEvents()


def test_batch_restoration_footer_is_reachable_at_compact_sizes(
    tmp_path: Path,
) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.show()
    window.set_current_page(4)

    for width, height in ((1100, 760), (1280, 800)):
        window.resize(width, height)
        application.processEvents()
        shell = window.page_shells[4]
        scrollbar = shell.scroll_area.verticalScrollBar()
        assert window.batch_restoration_widget.splitter_panel.maximumHeight() == 455
        assert window.batch_restoration_widget.splitter_panel.height() <= 455
        assert window.batch_restoration_widget.file_table.height() <= 270
        assert window.batch_restoration_widget.column_table.height() <= 210
        assert window.batch_restoration_widget.current_progress.height() >= 16
        assert window.batch_restoration_widget.overall_progress.height() >= 16

        scrollbar.setValue(scrollbar.maximum())
        application.processEvents()
        status = window.batch_restoration_widget.status_label
        top = status.mapTo(shell.scroll_area.viewport(), QPoint(0, 0)).y()
        assert top >= 0
        assert top + status.height() <= shell.scroll_area.viewport().height()

    window.close()
    application.processEvents()


def test_layout_remains_visible_at_supported_sizes(tmp_path: Path) -> None:
    application = create_application([])
    window = build_window(tmp_path)
    window.show()
    application.processEvents()
    identity = window.navigation.identity
    assert abs(
        identity.monogram_label.geometry().center().y()
        - identity.name_label.geometry().center().y()
    ) <= 1
    assert identity.height() <= 36

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
