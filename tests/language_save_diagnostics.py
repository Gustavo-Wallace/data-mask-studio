"""Temporary opt-in observations for one CI-only language-save investigation.

No event pumping, waits, decisions, or preference writes are added. Only known
locale/theme IDs and anonymous Qt state are logged, never UI text or paths.
"""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import time

import pytest


ENABLED_ENV = "DMS_QT_LANGUAGE_DIAGNOSTICS"
TRACE_ENV = "DMS_QT_LANGUAGE_TRACE"
TARGET = ("tests/test_theme_startup.py::"
          "test_theme_save_during_owned_work_is_deferred_and_language_draft_remains_independent")
WORKERS = (
    ("anonymization_widget", "_worker"), ("anonymization_widget", "_detection_worker"),
    ("batch_widget", "_validation_worker"), ("batch_widget", "_processing_worker"),
    ("restoration_widget", "_worker"), ("html_restoration_widget", "_worker"),
    ("batch_restoration_widget", "_analysis_worker"), ("batch_restoration_widget", "_processing_worker"),
    ("backup_widget", "_worker"), ("maintenance_widget", "_worker"),
)


def choice(value, allowed):
    value = getattr(value, "value", value)
    return value if isinstance(value, str) and value in allowed else "unsupported"


def locale(value):
    return choice(value, ("pt-BR", "en", "system"))


def preferences(result):
    return {"language": locale(result.preferences.language),
            "theme": choice(result.preferences.theme, ("system", "light", "dark")),
            "error": result.error.name if result.error is not None else None}


class DisabledTrace:
    def watch(self, *args):
        pass

    def capture(self, *args):
        pass


class LanguageSaveTrace:
    def __init__(self, destination):
        self.destination = Path(destination)
        self.window = self.service = self.worker = None
        self.started = time.perf_counter()
        self.stack = ExitStack()
        self.events, self.identities = [], {}
        self.finished_delivered = 0
        self.spy = self.receiver = None

    def emit(self, event, **data):
        record = {"event": event, "seconds": time.perf_counter() - self.started, **data}
        self.events.append(record)
        # Failure to write diagnostics must not change the test or its guards.
        try:
            with self.destination.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")
        except OSError as error:
            print(f"QT_LANGUAGE_DIAGNOSTIC_WRITE_ERROR:{type(error).__name__}", flush=True)

    def object_state(self, obj):
        if obj is None:
            return None
        key = id(obj)
        self.identities.setdefault(key, len(self.identities) + 1)
        result = {"ref": self.identities[key], "type": type(obj).__name__,
                  "visible": obj.isVisible(), "is_main_window": obj is self.window}
        ownership = sys.modules.get("ci_qt_dialog_ownership")
        if ownership is not None:
            details = ownership.correlate_message_box(obj)
            if details is not None:
                result["ownership"] = details
        return result

    def gui_state(self):
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QDialog
        window, settings = self.window, self.window.settings_widget
        workers = {}
        for page, attr in WORKERS:
            worker = getattr(getattr(window, page), attr)
            info = {"owned": worker is not None, "running": False}
            if worker is not None:
                try:
                    info["running"] = worker.isRunning()
                except RuntimeError:
                    info["running"] = "unavailable"
            workers[f"{page}.{attr}"] = info
        others = []
        for obj in QApplication.topLevelWidgets():
            if obj is not window and (isinstance(obj, QDialog) or obj.isVisible()):
                others.append({**self.object_state(obj), "dialog": isinstance(obj, QDialog),
                               "cached_about": obj is window.about_dialog})
        return {
            "translator_locale": locale(window._translator.locale),
            "selected_language": locale(settings.language_combo.currentData()),
            "theme_draft": choice(settings.theme_combo.currentData(), ("system", "light", "dark")),
            "button_enabled": settings.save_button.isEnabled(),
            "saving": settings._saving, "readable": settings._readable,
            "acknowledged_language": (locale(settings._acknowledged_language)
                                      if settings._acknowledged_language is not None else None),
            "feedback_severity": choice(settings._feedback_state, ("neutral", "success", "warning", "error")),
            "window_visible": window.isVisible(), "window_enabled": window.isEnabled(),
            "settings_visible": settings.isVisible(),
            "settings_page_current": window.current_page_index() == window.page_index(settings),
            "workers": workers, "other_windows": others,
            "modal_widget": self.object_state(QApplication.activeModalWidget()),
            "modal_window": self.object_state(QGuiApplication.modalWindow()),
            "popup": self.object_state(QApplication.activePopupWidget()),
            "worker_running": self.worker.isRunning() if self.worker is not None else None,
            "finished_emissions": self.spy.count() if self.spy is not None else None,
            "finished_gui_deliveries": self.finished_delivered,
        }

    def capture(self, event):
        if self.window is None:
            return
        try:
            # Observe UI first; the extra preference read is explicitly recorded.
            state = self.gui_state()
            try:
                stored = preferences(self.service.load())
            except Exception as error:
                stored = {"read_error_type": type(error).__name__}
            self.emit(event, state=state, persisted=stored)
        except Exception as error:
            self.emit("observation_error", phase=event, error_type=type(error).__name__)

    def install(self):
        from unittest.mock import patch
        from data_mask_studio.gui.main_window import MainWindow
        from data_mask_studio.gui.settings_widget import SettingsWidget
        from data_mask_studio.preferences import PreferencesService
        save = SettingsWidget.save_preference
        handler = MainWindow._language_preference_saved
        gate = MainWindow._can_apply_saved_language
        render = MainWindow.retranslate
        persist = PreferencesService.save_language
        trace = self

        # Keep the original callable signatures: Qt must deliver the same args.
        def observed_save(settings):
            watched = trace.window is not None and settings is trace.window.settings_widget
            if watched:
                trace.emit("save_handler.enter")
            try:
                return save(settings)
            except Exception as error:
                if watched:
                    trace.emit("save_handler.exception", error_type=type(error).__name__)
                raise
            finally:
                if watched:
                    trace.emit("save_handler.exit")

        def observed_handler(window, language):
            watched = window is trace.window
            if watched:
                trace.emit("language_handler.enter", requested=locale(language))
            try:
                return handler(window, language)
            except Exception as error:
                if watched:
                    trace.emit("language_handler.exception", error_type=type(error).__name__)
                raise
            finally:
                if watched:
                    trace.emit("language_handler.exit", translator_locale=locale(window._translator.locale))

        def observed_gate(window):
            result = gate(window)  # Exactly one original guard call.
            if window is trace.window:
                try:
                    trace.emit("gate.result", allowed=result, state=trace.gui_state())
                except Exception as error:
                    trace.emit("observation_error", phase="gate", error_type=type(error).__name__)
            return result

        def observed_render(window, translator):
            watched = window is trace.window
            if watched:
                trace.emit("retranslate.enter", requested=locale(translator.locale))
            try:
                return render(window, translator)
            except Exception as error:
                if watched:
                    trace.emit("retranslate.exception", error_type=type(error).__name__)
                raise
            finally:
                if watched:
                    trace.emit("retranslate.exit", translator_locale=locale(window._translator.locale))

        def observed_persist(service, language):
            watched = service is trace.service
            if watched:
                trace.emit("persistence.enter", requested=locale(language))
            try:
                result = persist(service, language)
            except Exception as error:
                if watched:
                    trace.emit("persistence.exception", error_type=type(error).__name__)
                raise
            if watched:
                trace.emit("persistence.result", persisted=preferences(result))
            return result

        for owner, name, method in (
            (SettingsWidget, "save_preference", observed_save),
            (MainWindow, "_language_preference_saved", observed_handler),
            (MainWindow, "_can_apply_saved_language", observed_gate),
            (MainWindow, "retranslate", observed_render),
            (PreferencesService, "save_language", observed_persist),
        ):
            self.stack.enter_context(patch.object(owner, name, method))

    def watch(self, window, service, worker):
        import PySide6
        from PySide6.QtCore import QObject, Slot, qVersion
        from PySide6.QtTest import QSignalSpy
        self.window, self.service, self.worker = window, service, worker
        self.spy = QSignalSpy(worker.finished)
        trace = self

        class Receiver(QObject):
            @Slot()
            def finished(self):
                trace.finished_delivered += 1

        self.receiver = Receiver()
        worker.finished.connect(self.receiver.finished)
        original_wait = worker.wait

        def observed_wait(*args, **kwargs):
            result = original_wait(*args, **kwargs)  # Same wait, same timeout, once.
            trace.capture("worker.wait.returned")
            trace.emit("worker.wait.result", completed=result)
            return result

        from unittest.mock import patch
        self.stack.enter_context(patch.object(worker, "wait", observed_wait))
        self.emit("attached", target=TARGET, python=sys.version.split()[0],
                  pyside=PySide6.__version__, qt=qVersion(), platform=sys.platform)

    def close(self):
        try:
            self.capture("test.teardown")
        finally:
            self.stack.close()
            if self.worker is not None and self.receiver is not None:
                try:
                    self.worker.finished.disconnect(self.receiver.finished)
                except (RuntimeError, TypeError):
                    pass
            self.window = self.service = self.worker = self.spy = self.receiver = None


@pytest.fixture
def language_save_diagnostics():
    if os.environ.get(ENABLED_ENV) != "1":
        yield DisabledTrace()
        return
    trace = LanguageSaveTrace(os.environ[TRACE_ENV])
    try:
        trace.install()
        yield trace
    finally:
        trace.close()
