"""Live modal presentation must not become a compaction decision or operation."""

from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.maintenance_worker import CompactionWorker
from data_mask_studio.gui.maintenance_widget import MaintenanceWidget, _format_bytes, _render_compaction
from data_mask_studio.i18n import Translator
from test_dialog_consistency import modal_reply
from test_maintenance_compaction_localization import page, isolated_appdata, result_for
from test_maintenance_interface import wait
from test_maintenance import FixedKeyProvider, HMAC_KEY, VAULT_KEY
from test_navigation_localization import build_localized_window


@contextmanager
def reply_safely(parent, callback):
    """Propagate callback assertions out of Qt's nested loop, always rejecting on error."""
    errors = []

    def reply():
        box = QApplication.activeModalWidget()
        try:
            assert isinstance(box, QMessageBox)
            callback(box)
        except BaseException as error:
            errors.append(error)
        finally:
            if isinstance(box, QMessageBox) and box.isVisible():
                box.reject()

    with modal_reply(parent, reply):
        yield
    if errors:
        raise errors[0]


def assert_presentation(box, translator, size):
    title = translator.text("maintenance.compaction.confirm.title")
    text = translator.text("maintenance.compaction.confirm.text", size=_format_bytes(size))
    assert box.windowTitle() == title
    assert box.text() == text
    assert box.accessibleName() == title and box.accessibleDescription() == text
    assert box.textFormat() == Qt.TextFormat.PlainText
    assert box.icon() == QMessageBox.Icon.Warning and box.isModal()
    assert box.standardButtons() == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    assert box.standardButton(box.defaultButton()) == box.standardButton(box.escapeButton()) == QMessageBox.StandardButton.No
    for standard, identifier, role in (
        (QMessageBox.StandardButton.Yes, "dialog.button.yes", QMessageBox.ButtonRole.YesRole),
        (QMessageBox.StandardButton.No, "dialog.button.no", QMessageBox.ButtonRole.NoRole),
    ):
        button = box.button(standard)
        assert button.text() == translator.text(identifier) and box.buttonRole(button) == role


def forbidden(*args, **kwargs):
    pytest.fail("Retranslation must not read the filesystem, inspect the vault or run compaction")


@pytest.mark.parametrize("focused_button", [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
def test_same_open_confirmation_retranslates_both_directions_without_operations(page, monkeypatch, focused_button):
    app, widget, paths, _, _ = page
    size = paths.vault_database_path.stat().st_size
    result = result_for(paths)
    widget._last_compaction = result
    widget.compaction_result.setPlainText(_render_compaction(result, Translator("pt-BR")))
    widget._set_compaction_status_id("maintenance.compaction.status.completed", state="success")
    widget.progress.setRange(0, 1)
    widget.progress.setValue(1)
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en", "pt-BR")]
    changes = [QSignalSpy(widget.busy_changed), QSignalSpy(widget.environment_changed),
               QSignalSpy(widget.sections.currentChanged), QSignalSpy(widget.progress.valueChanged)]
    observed = []

    def respond(box):
        assert box.windowTitle() == "Confirmar compactação"
        focus = box.button(focused_button)
        focus.setFocus()
        app.processEvents()
        assert app.focusWidget() is focus
        buttons = tuple(box.buttons())
        decision = box.result(), box.clickedButton(), box.windowModality()
        signals = [QSignalSpy(box.finished), QSignalSpy(box.accepted), QSignalSpy(box.rejected),
                   QSignalSpy(box.buttonClicked)]
        observed.append(box)
        with monkeypatch.context() as guard:
            guard.setattr(widget, "start_compaction", forbidden)
            guard.setattr(widget, "_prepare_operation", forbidden)
            guard.setattr(widget._compactor, "compact", forbidden)
            guard.setattr(widget._diagnostics, "run", forbidden)
            guard.setattr(CompactionWorker, "start", forbidden)
            guard.setattr(sqlite3, "connect", forbidden)
            for name in ("open", "read_text", "read_bytes", "is_file", "stat", "exists", "resolve", "iterdir"):
                guard.setattr(Path, name, forbidden)
            for translator in translators:
                widget.retranslate(translator)
                app.processEvents()
                assert app.activeModalWidget() is box and box.isVisible()
                assert_presentation(box, translator, size)
                assert tuple(box.buttons()) == buttons and app.focusWidget() is focus
                assert (box.result(), box.clickedButton(), box.windowModality()) == decision
                assert widget._worker is None and widget._last_compaction is result
                assert widget.progress.value() == 1 and widget.sections.currentIndex() == 3
                assert widget.status_label.property("feedbackState") == "success"
                assert widget.compaction_result.toPlainText() == _render_compaction(result, translator)
                assert all(signal.count() == 0 for signal in signals + changes)
        box.button(QMessageBox.StandardButton.No).click()

    with reply_safely(widget, respond):
        widget.start_compaction()
    assert len(observed) == 1 and widget._worker is None and widget._last_compaction is result
    assert widget._compaction_confirmation is None
    QCoreApplication.sendPostedEvents(observed[0], QEvent.Type.DeferredDelete)
    assert not isValid(observed[0])


def test_main_window_propagates_to_same_modal_and_new_confirmation_uses_current_language(tmp_path, monkeypatch):
    app = create_application([])
    window = build_localized_window(tmp_path)
    window._vault_repository_factory()
    widget = window.maintenance_widget
    window.show()
    window.set_current_page(window.page_index(widget))
    widget.sections.setCurrentIndex(3)
    window.consultant_widget.codes_input.setPlainText("Synthetic unsubmitted input")
    window.settings_widget.language_combo.setCurrentIndex(window.settings_widget.language_combo.findData("en"))
    app.processEvents()
    pages = window.page_widgets, tuple(window.page_shells), window.page_stack.currentWidget()
    navigation = QSignalSpy(window.navigation.current_changed)
    progress = widget.progress.minimum(), widget.progress.maximum(), widget.progress.value()
    translators = {locale: Translator(locale) for locale in ("en", "pt-BR")}
    seen = []

    def respond(box):
        seen.append(box)
        size = widget._compaction_confirmation[1]
        assert widget._compaction_confirmation[0] is box
        # Existing maintenance preflight clears consultation before opening the modal.
        consultation = window.consultant_widget.codes_input.toPlainText()
        assert consultation == ""
        focus = box.button(QMessageBox.StandardButton.No)
        focus.setFocus()
        app.processEvents()
        with monkeypatch.context() as guard:
            guard.setattr(widget._compactor, "compact", forbidden)
            guard.setattr(CompactionWorker, "start", forbidden)
            guard.setattr(sqlite3, "connect", forbidden)
            for name in ("open", "stat", "is_file", "resolve"):
                guard.setattr(Path, name, forbidden)
            for locale in ("en", "pt-BR", "en"):
                window.retranslate(translators[locale])
                app.processEvents()
                assert_presentation(box, translators[locale], size)
                assert app.activeModalWidget() is box and app.focusWidget() is focus
                assert (window.page_widgets, tuple(window.page_shells), window.page_stack.currentWidget()) == pages
                assert (widget.progress.minimum(), widget.progress.maximum(), widget.progress.value()) == progress
                assert window.consultant_widget.codes_input.toPlainText() == consultation
                assert window.settings_widget.language_combo.currentData() == "en"
                assert widget.sections.currentIndex() == 3 and widget._worker is None
                assert navigation.count() == 0
        focus.click()

    with reply_safely(widget, respond):
        widget.start_compaction()
    assert widget._compaction_confirmation is None
    QCoreApplication.sendPostedEvents(seen[0], QEvent.Type.DeferredDelete)
    assert not isValid(seen[0])

    def respond_again(box):
        assert box is not seen[0]
        seen.append(box)
        assert_presentation(box, translators["en"], widget._compaction_confirmation[1])
        box.button(QMessageBox.StandardButton.No).click()

    with reply_safely(widget, respond_again):
        widget.start_compaction()
    assert len(seen) == 2 and widget._compaction_confirmation is None and widget._worker is None
    assert navigation.count() == 0


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("decision", ["no", "escape", "close", "reject", "yes"])
def test_only_explicit_yes_starts_one_worker_and_terminal_dialog_references_are_cleared(page, monkeypatch, locale, decision):
    app, widget, _, _, _ = page
    initial = Translator(locale)
    opposite = Translator("en" if locale == "pt-BR" else "pt-BR")
    widget.retranslate(initial)
    starts = []
    monkeypatch.setattr(CompactionWorker, "start", lambda worker: starts.append(worker))
    monkeypatch.setattr(widget._compactor, "compact", forbidden)
    seen = []

    def respond(box):
        seen.append(box)
        size = widget._compaction_confirmation[1]
        assert_presentation(box, initial, size)
        for translator in (opposite, initial):
            widget.retranslate(translator)
            app.processEvents()
            assert_presentation(box, translator, size)
            assert widget._worker is None and starts == []
        if decision in ("yes", "no"):
            box.button(QMessageBox.StandardButton.Yes if decision == "yes" else QMessageBox.StandardButton.No).click()
        elif decision == "escape":
            QTest.keyClick(box, Qt.Key.Key_Escape)
        elif decision == "close":
            box.close()
        else:
            box.reject()

    with reply_safely(widget, respond):
        widget.start_compaction()
    assert widget._compaction_confirmation is None and len(seen) == 1
    if decision == "yes":
        assert len(starts) == 1 and starts[0] is widget._worker
        worker = widget._worker
        widget.retranslate(opposite)
        assert widget._worker is worker and starts == [worker]
        worker.cancelled.emit()
        worker.finished.emit()
    else:
        assert starts == []
    assert widget._worker is None
    QCoreApplication.sendPostedEvents(seen[0], QEvent.Type.DeferredDelete)
    assert not isValid(seen[0])
    widget.retranslate(initial)  # No retired dialog may be dereferenced.


def test_exec_exception_releases_transient_dialog_reference_without_starting_compaction(page, monkeypatch):
    _, widget, _, _, _ = page
    seen = []
    failure = RuntimeError("Synthetic modal failure")

    def fail(box):
        seen.append(box)
        raise failure

    monkeypatch.setattr(QMessageBox, "exec", fail)
    monkeypatch.setattr(CompactionWorker, "start", forbidden)
    with pytest.raises(RuntimeError) as caught:
        widget.start_compaction()
    assert caught.value is failure and len(seen) == 1
    assert widget._compaction_confirmation is None and widget._worker is None
    QCoreApplication.sendPostedEvents(seen[0], QEvent.Type.DeferredDelete)
    assert not isValid(seen[0])
    widget.retranslate(Translator("en"))


def test_vault_growth_during_confirmation_keeps_preview_but_engine_uses_current_vault(page, monkeypatch):
    app, widget, paths, repository, code = page
    original = repository.get_decrypted_mapping(code)
    translator = Translator("en")
    sizes = []

    def respond(box):
        preview = widget._compaction_confirmation[1]
        with closing(sqlite3.connect(paths.vault_database_path)) as connection:
            connection.execute("CREATE TABLE synthetic_new_data (payload BLOB)")
            connection.execute("INSERT INTO synthetic_new_data VALUES (zeroblob(1048576))")
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        current = paths.vault_database_path.stat().st_size
        assert current > preview
        sizes.append(current)
        with monkeypatch.context() as guard:
            guard.setattr(Path, "stat", forbidden)
            guard.setattr(widget._compactor, "compact", forbidden)
            widget.retranslate(translator)
        assert_presentation(box, translator, preview)
        box.button(QMessageBox.StandardButton.Yes).click()

    with reply_safely(widget, respond):
        widget.start_compaction()
    wait(app, widget)
    assert widget._worker is None and widget._compaction_confirmation is None
    assert widget._last_compaction.size_before == sizes[0]
    assert widget._last_compaction.audit.failure_count == 0
    assert repository.get_decrypted_mapping(code) == original


def test_vault_corrupted_during_confirmation_is_still_rejected_by_real_compaction(page):
    app, _, paths, repository, code = page
    original = repository.get_decrypted_mapping(code)
    changed = paths.directory / "synthetic-confirmation-copy.db"
    with closing(sqlite3.connect(paths.vault_database_path)) as source, closing(sqlite3.connect(changed)) as destination:
        source.backup(destination)
    widget = MaintenanceWidget(replace(paths, vault_database_path=changed),
                               FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY))
    widget.sections.setCurrentIndex(3)
    widget.show()
    app.processEvents()
    translator = Translator("en")
    damaged = b"Synthetic invalid SQLite file"

    def respond(box):
        preview = widget._compaction_confirmation[1]
        changed.write_bytes(damaged)
        widget.retranslate(translator)
        assert_presentation(box, translator, preview)
        box.button(QMessageBox.StandardButton.Yes).click()

    with reply_safely(widget, respond):
        widget.start_compaction()
    wait(app, widget)
    assert widget._worker is None and widget._compaction_confirmation is None
    assert widget._last_compaction is None and widget.progress.value() == 0
    assert widget.status_label.property("feedbackState") == "error"
    assert changed.read_bytes() == damaged and not list(paths.directory.glob("dms-vault-compaction-*"))
    assert repository.get_decrypted_mapping(code) == original
