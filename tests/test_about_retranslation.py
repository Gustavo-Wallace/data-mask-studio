"""About acquires metadata lazily once, not while rendering another locale."""

from pathlib import Path
import sqlite3

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QDialogButtonBox, QWidget

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio import metadata
from data_mask_studio.app import create_application
from data_mask_studio.gui import about_dialog as about_module, main_window as window_module
from data_mask_studio.gui.about_dialog import AboutDialog, PUBLIC_URLS
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.vault.database import SCHEMA_VERSION
from test_about_localization import plain_text
from test_navigation_localization import build_localized_window


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    return create_application([])


def forbidden(*args, **kwargs):
    pytest.fail("About retranslation must not acquire metadata or perform IO")


def worker_references(window):
    return tuple(tuple(getattr(page, name, None) for name in (
        "_worker", "_detection_worker", "_validation_worker", "_processing_worker", "_analysis_worker",
    )) for page in window.page_widgets)


@pytest.mark.parametrize("visible", [False, True], ids=["cached-hidden", "visible"])
def test_repeated_about_retranslation_does_not_add_metadata_reads(app, tmp_path, monkeypatch, visible):
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    original_open = Path.open
    reads = []

    def observe_open(path, *args, **kwargs):
        if path == project_file:
            reads.append(path)
        return original_open(path, *args, **kwargs)

    translators = [Translator(locale) for locale in ("en", "pt-BR", "en", "pt-BR")]
    monkeypatch.setattr(Path, "open", observe_open)
    window = build_localized_window(tmp_path)
    window.show()
    app.processEvents()
    assert window.about_dialog is None and reads == []
    window.show_about_dialog()
    app.processEvents()
    dialog = window.about_dialog
    assert reads == [project_file]
    if not visible:
        dialog.close()
        app.processEvents()
    for translator in translators:
        window.retranslate(translator)
        app.processEvents()
        assert dialog.isVisible() is visible
        assert translator.text("about.version", version="1.3.0") in plain_text(dialog.details_label)
        assert translator.text("about.schema", schema=SCHEMA_VERSION) in plain_text(dialog.details_label)
    assert reads == [project_file], f"Expected first-use read only; observed {len(reads)} reads"


@pytest.mark.parametrize("visible", [False, True], ids=["cached-hidden", "visible"])
def test_about_retranslation_is_io_free_and_preserves_existing_window_state(app, tmp_path, monkeypatch, visible):
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en", "pt-BR")]
    window = build_localized_window(tmp_path)
    window.show()
    window.set_current_page(window.page_index(window.settings_widget))
    window.settings_widget.language_combo.setCurrentIndex(window.settings_widget.language_combo.findData("en"))
    window.consultant_widget.codes_input.setPlainText("Synthetic unsubmitted input")
    window.navigation.set_page_enabled(1, False)
    window.show_about_dialog()
    app.processEvents()
    dialog = window.about_dialog
    close = dialog.buttons.button(QDialogButtonBox.StandardButton.Close)
    focus = close if visible else window.settings_widget.language_combo
    if not visible:
        dialog.close()
        window.activateWindow()
        app.processEvents()
    focus.setFocus()
    app.processEvents()
    assert app.focusWidget() is focus
    controls = tuple(window.findChildren(QWidget))
    pages = (window.page_widgets, tuple(window.page_shells), window.page_stack.currentWidget())
    workers = worker_references(window)
    changes = QSignalSpy(window.navigation.current_changed)
    language_changes = QSignalSpy(window.settings_widget.language_combo.currentIndexChanged)
    with monkeypatch.context() as guard:
        guard.setattr(about_module, "application_version", forbidden)
        guard.setattr(metadata, "version", forbidden)
        guard.setattr(window_module, "AboutDialog", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        for name in ("open", "read_text", "read_bytes", "stat", "is_file", "exists", "resolve", "iterdir"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            window.retranslate(translator)
            app.processEvents()
            assert window.about_dialog is dialog and dialog.isVisible() is visible
            assert tuple(window.findChildren(QWidget)) == controls
            assert (window.page_widgets, tuple(window.page_shells), window.page_stack.currentWidget()) == pages
            assert app.focusWidget() is focus
            assert window.settings_widget.language_combo.currentData() == "en"
            assert window.consultant_widget.codes_input.toPlainText() == "Synthetic unsubmitted input"
            assert not window.navigation.page_enabled(1)
            assert close.isDefault() and dialog.buttons.buttonRole(close) == QDialogButtonBox.ButtonRole.RejectRole
            assert worker_references(window) == workers
            assert all(f'href="{url}"' in dialog.links_label.text() for url in PUBLIC_URLS)
    assert changes.count() == language_changes.count() == 0


def test_first_use_remains_lazy_and_metadata_cache_is_dialog_local(app, tmp_path, monkeypatch):
    translators = {locale: Translator(locale) for locale in ("pt-BR", "en")}
    current_version = ["1.3.0"]
    acquired = []

    def acquire_version():
        acquired.append(current_version[0])
        return current_version[0]

    monkeypatch.setattr(about_module, "application_version", acquire_version)
    preferences = PreferencesService(tmp_path / "preferences.json", ui_languages_provider=forbidden)
    assert preferences.save_language("en").error is None
    saved = preferences.path.read_bytes()
    window = build_localized_window(tmp_path, preferences_service=preferences)
    window.show()
    app.processEvents()
    assert window._translator.locale == "pt-BR" and acquired == []
    for locale in ("en", "pt-BR"):
        window.retranslate(translators[locale])
    assert acquired == [] and window.about_dialog is None
    window.show_about_dialog()
    dialog = window.about_dialog
    assert acquired == ["1.3.0"] and dialog.windowTitle() == "Sobre o Data Mask Studio"
    current_version[0] = "synthetic-later-metadata"
    dialog.close()
    for locale in ("en", "pt-BR"):
        window.retranslate(translators[locale])
    window.show_about_dialog()
    assert window.about_dialog is dialog and acquired == ["1.3.0"]
    assert "Versão 1.3.0" in plain_text(dialog.details_label)
    assert preferences.path.read_bytes() == saved
    another = AboutDialog(translator=translators["en"])
    assert acquired == ["1.3.0", "synthetic-later-metadata"]
    assert "Version synthetic-later-metadata" in plain_text(another.details_label)


@pytest.mark.parametrize("case", ["missing", "malformed", "unreadable", "missing-distribution"])
def test_about_keeps_existing_metadata_fallbacks_on_first_use(app, tmp_path, monkeypatch, case):
    monkeypatch.setattr(metadata, "__file__", str(tmp_path / "bundle" / "data_mask_studio" / "metadata.py"))
    project_file = tmp_path / "pyproject.toml"
    if case in ("malformed", "unreadable"):
        project_file.write_text("[not valid TOML", encoding="utf-8")
    original_open = Path.open

    def read_project(path, *args, **kwargs):
        if path == project_file and case == "unreadable":
            raise PermissionError("Synthetic inaccessible metadata")
        return original_open(path, *args, **kwargs)

    requested = []

    def installed_version(name):
        requested.append(name)
        if case == "missing-distribution":
            raise metadata.PackageNotFoundError(name)
        return "synthetic-installed-version"

    monkeypatch.setattr(Path, "open", read_project)
    monkeypatch.setattr(metadata, "version", installed_version)
    translators = [Translator(locale) for locale in ("pt-BR", "en", "pt-BR")]
    dialog = AboutDialog(translator=translators[0])
    expected = "0+unknown" if case == "missing-distribution" else "synthetic-installed-version"
    assert requested == ["data-mask-studio"]
    with monkeypatch.context() as guard:
        guard.setattr(metadata, "version", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "resolve", forbidden)
        for translator in translators:
            dialog.retranslate(translator)
            assert translator.text("about.version", version=expected) in plain_text(dialog.details_label)
    assert requested == ["data-mask-studio"]


def test_about_does_not_suppress_unexpected_metadata_errors(app, tmp_path, monkeypatch):
    monkeypatch.setattr(metadata, "__file__", str(tmp_path / "bundle" / "data_mask_studio" / "metadata.py"))
    error = RuntimeError("Synthetic metadata provider failure")

    def fail(name):
        raise error

    monkeypatch.setattr(metadata, "version", fail)
    with pytest.raises(RuntimeError) as caught:
        AboutDialog()
    assert caught.value is error
