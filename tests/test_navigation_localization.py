"""Explicit shell localization must not activate or recreate workflows."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.components import NavigationItem, PageShell, SidebarNavigation
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.gui import main_window as main_window_module
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository
from test_main_navigation import FakeProtector, FixedKeyProvider


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def build_localized_window(tmp_path, translator=None, preferences_service=None):
    local = tmp_path / "environment"
    local.mkdir()
    paths = EnvironmentPaths(
        directory=local, hmac_key_path=local / "secret.key",
        vault_key_path=local / "vault_key.dpapi",
        vault_database_path=local / "vault.db", profiles_path=local / "profiles.json",
    )
    cipher = VaultCipher(b"V" * 32)
    return MainWindow(
        key_provider=FixedKeyProvider(b"H" * 32),
        vault_repository_factory=lambda: VaultRepository(paths.vault_database_path, cipher),
        profile_service=ProfileService(ProfileRepository(paths.profiles_path)),
        backup_paths=paths, vault_key_provider=FixedKeyProvider(b"V" * 32),
        data_protector=FakeProtector(), preferences_service=preferences_service,
        translator=translator,
    )


EXPECTED_NAVIGATION = {
    "pt-BR": [
        "Mascarar arquivo", "Mascaramento em lote", "Restaurar arquivo",
        "Restauração em lote", "Consultar cofre", "Backup e recuperação",
        "Cofre e manutenção", "Configurações",
    ],
    "en": [
        "Mask file", "Batch masking", "Restore file", "Batch restoration",
        "Browse vault", "Backup and recovery", "Vault and maintenance", "Settings",
    ],
}


@pytest.mark.parametrize("locale", ["pt-BR", "en", "unsupported"])
def test_explicit_shell_localization_preserves_routes_and_keyboard(locale, tmp_path):
    app = create_application([])
    translator = Translator(locale)
    window = build_localized_window(tmp_path, translator)
    window.show()
    app.processEvents()

    expected = EXPECTED_NAVIGATION[translator.locale]
    assert [button.text() for button in window.navigation.buttons] == expected
    assert window.page_stack.count() == len(window.page_widgets) == 8
    assert window.current_page_index() == 0
    assert window.navigation.buttons[0].isChecked()
    assert [category.label.text() for category in window.navigation.categories] == (
        ["PROCESSAMENTO", "RESTAURAÇÃO", "COFRE"] if translator.locale == "pt-BR"
        else ["PROCESSING", "RESTORATION", "VAULT"]
    )
    assert window.navigation.about_button.text() == translator.text("common.about")
    assert window.navigation.accessibleName() == translator.text("navigation.main")
    for index, (button, shell, widget) in enumerate(zip(
        window.navigation.buttons, window.page_shells, window.page_widgets, strict=True,
    )):
        assert shell.content is widget and window.page_index(widget) == index
        assert shell.header.title_label.text() == expected[index]
        assert button.accessibleName() == expected[index]
        assert button.accessibleDescription() == shell.header.description_label.text()
        assert shell.header.title_label.accessibleName() == translator.text(
            "navigation.page_title_accessible", title=expected[index],
        )

    window.navigation.buttons[-1].setFocus()
    QTest.keyClick(window.navigation.buttons[-1], Qt.Key.Key_Down)
    app.processEvents()
    assert window.current_page_index() == 0
    assert window.navigation.buttons[0].hasFocus()
    QTest.keyClick(window.navigation.buttons[0], Qt.Key.Key_Up)
    app.processEvents()
    assert window.current_page_index() == 7
    assert window.page_stack.currentWidget().content is window.settings_widget
    assert window.navigation.buttons[7].hasFocus()
    # Only migrated presentation follows the explicitly injected translator.
    assert window.anonymization_widget.select_button.text() == translator.text("masking.select_csv")
    assert window.maintenance_widget.refresh_button.text() == translator.text("maintenance.overview.refresh")


def test_normal_startup_ignores_stored_english_and_windows_language(tmp_path):
    app = create_application([])
    service = PreferencesService(
        tmp_path / "preferences.json",
        ui_languages_provider=lambda: pytest.fail("Do not activate OS language detection"),
    )
    assert service.save_language("en").error is None
    before = service.path.read_bytes()
    window = build_localized_window(tmp_path, preferences_service=service)
    window.show()
    window.set_current_page(window.page_index(window.settings_widget))
    app.processEvents()

    assert window._translator.locale == "pt-BR"
    assert [button.text() for button in window.navigation.buttons] == EXPECTED_NAVIGATION["pt-BR"]
    assert window.settings_widget.language_combo.currentData() == "en"
    assert window.settings_widget.save_button.text() == "Salvar preferência"
    assert service.path.read_bytes() == before
    window.show_about_dialog()
    assert window.about_dialog.windowTitle() == "Sobre o Data Mask Studio"
    assert window.about_dialog._translator is window._translator


def test_in_place_retranslation_preserves_widgets_forms_focus_and_environment(tmp_path, monkeypatch):
    app = create_application([])
    window = build_localized_window(tmp_path)
    window.show()
    window.show_about_dialog()
    window.about_dialog.close()
    window.set_current_page(window.page_index(window.settings_widget))
    app.processEvents()
    window.consultant_widget.codes_input.setPlainText("SYNTHETIC-REFERENCE")
    combo = window.settings_widget.language_combo
    combo.setCurrentIndex(combo.findData("en"))  # Unsaved form state must survive.
    window.activateWindow()  # Closing a modal About window restores activation asynchronously.
    app.processEvents()
    combo.setFocus()
    app.processEvents()
    assert combo.hasFocus()
    window.navigation.set_page_enabled(1, False)
    before = (
        window.page_widgets, tuple(window.page_shells), window.navigation.buttons,
        window.navigation.categories, window.about_dialog, window.settings_widget,
        window.batch_widget._processing_worker, window.batch_widget._validation_worker,
    )
    changes = []
    window.navigation.current_changed.connect(changes.append)

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not initialize or access the environment")

    monkeypatch.setattr(main_window_module, "initialize_existing_vault", forbidden)
    monkeypatch.setattr(window._key_provider, "get_key", forbidden)
    monkeypatch.setattr(window._vault_key_provider, "get_key", forbidden)
    monkeypatch.setattr(window.settings_widget._preferences, "load", forbidden)
    translator = Translator("en")
    window.retranslate(translator)
    app.processEvents()

    after = (
        window.page_widgets, tuple(window.page_shells), window.navigation.buttons,
        window.navigation.categories, window.about_dialog, window.settings_widget,
        window.batch_widget._processing_worker, window.batch_widget._validation_worker,
    )
    assert before == after
    assert window.current_page_index() == 7 and changes == []
    assert combo.hasFocus() and combo.currentData() == "en"
    assert window.settings_widget.save_button.isEnabled()
    assert window.consultant_widget.codes_input.toPlainText() == "SYNTHETIC-REFERENCE"
    assert not window.navigation.page_enabled(1)
    assert window.navigation.buttons[7].isChecked()
    assert window.settings_widget._translator is window.about_dialog._translator is translator
    assert [button.text() for button in window.navigation.buttons] == EXPECTED_NAVIGATION["en"]
    window.retranslate(Translator("pt-BR"))
    assert [button.text() for button in window.navigation.buttons] == EXPECTED_NAVIGATION["pt-BR"]
    assert combo.currentData() == "en" and changes == []


def test_navigation_groups_use_stable_ids_not_localized_labels():
    create_application([])
    items = (
        NavigationItem("same displayed group", "one", "first", group_message_id="navigation.group.processing"),
        NavigationItem("same displayed group", "two", "second", group_message_id="navigation.group.restoration"),
    )
    navigation = SidebarNavigation(items, translator=Translator("en"))
    categories = navigation.categories
    assert [category.label.text() for category in categories] == ["PROCESSING", "RESTORATION"]
    navigation.retranslate(Translator("pt-BR"))
    assert navigation.categories == categories
    assert [category.label.text() for category in categories] == ["PROCESSAMENTO", "RESTAURAÇÃO"]
    assert [button.text() for button in navigation.buttons] == ["one", "two"]


def test_legacy_page_shell_constructor_preserves_explicit_unmigrated_text():
    create_application([])
    content = QWidget()
    shell = PageShell("Existing title", "Existing description", content)
    shell.retranslate(Translator("en"))
    assert shell.content is content
    assert shell.header.title_label.text() == "Existing title"
    assert shell.header.description_label.text() == "Existing description"
    assert shell.header.title_label.accessibleName() == "Page title: Existing title"


def test_explicit_translator_does_not_bypass_fail_closed_vault_startup(tmp_path, monkeypatch):
    create_application([])

    def refuse_initialization(*args):
        raise RuntimeError("Synthetic vault startup refusal")

    monkeypatch.setattr(main_window_module, "initialize_existing_vault", refuse_initialization)
    with pytest.raises(RuntimeError, match="Synthetic vault startup refusal"):
        build_localized_window(tmp_path, Translator("en"))
