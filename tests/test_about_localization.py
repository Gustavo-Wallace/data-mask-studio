"""Controlled About localization without activating application-wide language changes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.about_dialog import AboutDialog, PUBLIC_URLS, REPOSITORY_URL
from data_mask_studio.gui.visual_tokens import COLORS
from data_mask_studio.i18n import Translator
from data_mask_studio.metadata import application_version
from data_mask_studio.vault.database import SCHEMA_VERSION


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    return create_application([])


def plain_text(label):
    document = QTextDocument()
    document.setHtml(label.text())
    return document.toPlainText()


@pytest.mark.parametrize("locale,title,version,schema,local,telemetry,license_label,close", [
    ("pt-BR", "Sobre o Data Mask Studio", "Versão", "Schema suportado",
     "Processamento local", "Sem telemetria", "Licenciado sob", "Fechar"),
    ("en", "About Data Mask Studio", "Version", "Supported schema",
     "Local processing", "No telemetry", "Licensed under", "Close"),
])
def test_about_text_localizes_without_changing_public_metadata(
    app, locale, title, version, schema, local, telemetry, license_label, close,
):
    dialog = AboutDialog(translator=Translator(locale))
    assert dialog.windowTitle() == dialog.accessibleName() == title
    assert dialog.title_label.text() == "Data Mask Studio"
    details = plain_text(dialog.details_label)
    assert f"{version} {application_version()}" in details
    assert f"{schema}: {SCHEMA_VERSION}" in details
    assert local in details and telemetry in details
    assert plain_text(dialog.copyright_label) == (
        "Copyright © 2026 Gustavo Wallace Macedo Santos\n"
        f"{license_label} GNU GPL v3.0 only."
    )
    for url in PUBLIC_URLS:
        assert f'href="{url}"' in dialog.links_label.text()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Close).text() == close
    assert not dialog.links_label.openExternalLinks()
    assert dialog.warning_label.wordWrap()
    assert dialog.isModal()
    text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert all(sensitive not in text for sensitive in ("LOCALAPPDATA", "vault.db", "secret.key"))


def test_about_defaults_to_portuguese_and_does_not_change_qt_dialog_translator(app):
    before = QCoreApplication.translate("QPlatformTheme", "Close")
    dialog = AboutDialog()
    english = AboutDialog(translator=Translator("en"))
    assert dialog.windowTitle() == "Sobre o Data Mask Studio"
    assert english.windowTitle() == "About Data Mask Studio"
    assert QCoreApplication.translate("QPlatformTheme", "Close") == before == "Fechar"


def test_about_explicit_unsupported_locale_uses_catalog_english_fallback(app):
    dialog = AboutDialog(translator=Translator("unsupported"))
    assert dialog.windowTitle() == "About Data Mask Studio"
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Close).text() == "Close"


def test_about_retranslation_preserves_widget_identity_links_and_signal_connections(app, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "data_mask_studio.gui.about_dialog.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    dialog = AboutDialog()
    labels = tuple(dialog.findChildren(QLabel))
    buttons = dialog.buttons
    close = buttons.button(QDialogButtonBox.StandardButton.Close)
    rejected = []
    dialog.rejected.connect(lambda: rejected.append(True))
    for locale in ("en", "pt-BR", "en"):
        dialog.retranslate(Translator(locale))
        assert tuple(dialog.findChildren(QLabel)) == labels
        assert dialog.buttons is buttons
        assert buttons.button(QDialogButtonBox.StandardButton.Close) is close
        assert buttons.buttonRole(close) == QDialogButtonBox.ButtonRole.RejectRole
    for url in sorted(PUBLIC_URLS):
        dialog.links_label.linkActivated.emit(url)
    dialog.links_label.linkActivated.emit("https://example.invalid/unapproved")
    assert opened == sorted(PUBLIC_URLS)
    dialog.show()
    app.processEvents()
    close.click()
    assert rejected == [True]
    assert dialog.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("interaction", ["click", "enter", "escape"])
def test_about_localized_close_preserves_default_reject_and_keyboard_behavior(app, locale, interaction):
    dialog = AboutDialog(translator=Translator(locale))
    dialog.show()
    app.processEvents()
    close = dialog.buttons.button(QDialogButtonBox.StandardButton.Close)
    assert close.isDefault()
    assert dialog.buttons.standardButton(close) == QDialogButtonBox.StandardButton.Close
    assert dialog.buttons.buttonRole(close) == QDialogButtonBox.ButtonRole.RejectRole
    if interaction == "click":
        close.click()
    else:
        QTest.keyClick(dialog, Qt.Key.Key_Return if interaction == "enter" else Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_about_rich_text_escapes_interpolated_metadata(app, monkeypatch):
    version = '<img src="untrusted">&version'
    monkeypatch.setattr("data_mask_studio.gui.about_dialog.application_version", lambda: version)
    dialog = AboutDialog(translator=Translator("en"))
    assert f"Version {version}" in plain_text(dialog.details_label)
    assert "<img" not in dialog.details_label.text()
    assert "&lt;img" in dialog.details_label.text()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_about_links_render_underlined_in_theme_light_blue_with_original_destinations(app, locale):
    dialog = AboutDialog(translator=Translator(locale))
    document = QTextDocument()
    document.setHtml(dialog.links_label.text())
    destinations = set()
    block = document.begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            formatting = fragment.charFormat()
            if formatting.isAnchor():
                destinations.add(formatting.anchorHref())
                assert formatting.foreground().color() == QColor(COLORS["focus"])
                assert formatting.fontUnderline()
            fragments += 1
        block = block.next()
    assert destinations == PUBLIC_URLS

    def luminance(color):
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in channels]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))

    foreground = luminance(QColor(COLORS["focus"]))
    background = luminance(dialog.palette().color(dialog.backgroundRole()))
    assert (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05) >= 4.5


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_about_styled_links_preserve_hover_and_keyboard_activation(app, locale, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "data_mask_studio.gui.about_dialog.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    dialog = AboutDialog(translator=Translator(locale))
    dialog.show()
    app.processEvents()
    links = dialog.links_label
    hovered = []
    links.linkHovered.connect(hovered.append)
    QTest.mouseMove(links, QPoint(links.width() - 1, links.height() - 1))
    QTest.mouseMove(links, QPoint(2, links.fontMetrics().height() // 2))
    app.processEvents()
    assert REPOSITORY_URL in hovered
    assert not links.openExternalLinks()
    assert links.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    links.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(links, Qt.Key.Key_Tab)  # Focus the first anchor inside the label.
    QTest.keyClick(links, Qt.Key.Key_Return)
    app.processEvents()
    assert opened == [REPOSITORY_URL]
    assert dialog.isVisible()  # Enter activates the link, not the default Close button.
