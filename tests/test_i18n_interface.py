"""The unused foundation must not activate translation or disturb existing Qt."""

import os

import pytest
from PySide6.QtCore import QCoreApplication, QLocale, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QLabel

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
from test_main_navigation import build_window


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.mark.parametrize("preference", ["system", "en", "pt-BR"])
def test_existing_portuguese_gui_and_settings_behavior_are_unchanged(tmp_path, preference):
    service = PreferencesService()
    assert service.save_language(preference).error is None
    before = service.path.read_bytes(), service.path.stat().st_mtime_ns
    app = create_application([])
    locale_before = QLocale().name()
    standard_yes = QCoreApplication.translate("QPlatformTheme", "&Yes")
    window = build_window(tmp_path)
    window.show()
    app.processEvents()
    titles = [button.text() for button in window.navigation.buttons]
    assert titles == [
        "Mascarar arquivo", "Mascaramento em lote", "Restaurar arquivo", "Restauração em lote",
        "Consultar cofre", "Backup e recuperação", "Cofre e manutenção", "Configurações",
    ]
    for locale in ("en", "pt-BR"):
        translator = Translator(locale)
        assert translator.text("common.settings")
    assert window.current_page_index() == 0 and window.navigation.buttons[0].isChecked()
    window.set_current_page(window.page_index(window.settings_widget))
    app.processEvents()
    settings = window.settings_widget
    assert settings.language_combo.currentData() == preference
    assert settings.save_button.text() == "Salvar preferência"
    assert settings.notice_label.text() == (
        "A preferência será aplicada à interface quando a tradução estiver disponível. "
        "Nesta etapa, o aplicativo continua em português."
    )
    assert window.anonymization_widget.select_button.text() == "Selecionar CSV"
    assert [shell.header.title_label.text() for shell in window.page_shells] == titles
    assert QLocale().name() == locale_before
    assert QCoreApplication.translate("QPlatformTheme", "&Yes") == standard_yes == "&Sim"
    assert (service.path.read_bytes(), service.path.stat().st_mtime_ns) == before


def test_plain_and_html_outputs_can_be_rendered_safely_by_qt():
    create_application([])
    translator = Translator("pt-BR")
    filename = '<img src=x onerror="alert(1)">&ação.csv'
    text = translator.text("restoration.completed", count=2, filename=filename)
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(text)
    assert label.text() == text and filename in label.text()
    document = QTextDocument()
    document.setHtml(translator.html("restoration.completed", count=2, filename=filename))
    assert document.toPlainText() == text
