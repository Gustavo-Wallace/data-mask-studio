"""Opt-in, non-owning QMessageBox provenance for the shard-1 diagnostic run.

IDs identify native QObject lifetimes, not Python wrapper IDs. No dialog/parent
references, frame objects, contents, titles, or native addresses enter the log.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
import weakref

import pytest
from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import getCppPointer, isValid, ownedByPython


TRACE_ENV = "DMS_QT_DIALOG_TRACE"
_ACTIVE_TRACKER = None
LIFECYCLE_EVENTS = {QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Close,
                    QEvent.Type.ParentChange, QEvent.Type.DeferredDelete, QEvent.Type.Polish}


class DialogOwnershipTracker:
    def __init__(self, destination, repository):
        self.destination = Path(destination)
        self.repository = os.path.normcase(os.path.abspath(repository)) + os.sep
        self.test = None
        self.collection = None
        self.phase = "configuration"
        self.started = time.perf_counter()
        self.records, self.live, self.references, self.callbacks = {}, {}, {}, {}
        self.sequence = 0
        self.stream = None
        self.filter = None
        self.application = None  # Weak reference only.
        self.closed = False
        self.patches = []

    def emit(self, event, **data):
        if self.closed or self.stream is None:
            return None
        self.sequence += 1
        row = {"seq": self.sequence, "seconds": time.perf_counter() - self.started,
               "event": event, "test": self.test, "phase": self.phase, **data}
        try:
            self.stream.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n")
            self.stream.flush()
        except (OSError, ValueError) as error:
            print(f"QT_DIALOG_TRACE_WRITE_ERROR:{type(error).__name__}", flush=True)
        return {"seq": self.sequence, "event": event, "test": self.test, "phase": self.phase}

    def creation_stack(self):
        # Frame metadata only: no linecache/source reads and no retained frames.
        entries = []
        frame = sys._getframe(1)
        try:
            while frame is not None and len(entries) < 6:
                filename = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
                if filename.startswith(self.repository):
                    relative = filename[len(self.repository):].replace("\\", "/")
                    if (relative.startswith(("tests/", "src/", "scripts/"))
                            and relative != "scripts/ci_qt_dialog_ownership.py"):
                        entries.append({"file": relative, "line": frame.f_lineno,
                                        "function": frame.f_code.co_name})
                frame = frame.f_back
        finally:
            del frame
        return entries

    def destroyed(self, identifier):
        record = self.records[identifier]
        if record["destroyed"]:
            return
        record["destroyed"] = True
        if self.live.get(record["pointer"]) == identifier:
            del self.live[record["pointer"]]
        event = self.emit("qobject.destroyed", object_id=identifier,
                          object_type=record["type"], creator_test=record.get("creator_test"))
        if event:
            record["history"].append(event)

    def identity(self, obj):
        pointer = getCppPointer(obj)[0]
        if pointer in self.live:
            return self.live[pointer]
        identifier = f"qt-{len(self.records) + 1:06d}"
        self.live[pointer] = identifier
        self.records[identifier] = {"pointer": pointer, "type": type(obj).__name__,
                                    "destroyed": False, "history": [], "message": False,
                                    "destruction_observation_available": False}
        try:
            self.references[identifier] = weakref.ref(obj)
        except TypeError:
            self.references[identifier] = lambda: None  # Never fall back to ownership.
        tracker_ref = weakref.ref(self)

        def on_destroyed(*_ignored, identifier=identifier):
            tracker = tracker_ref()
            if tracker is not None and not tracker.closed:
                tracker.destroyed(identifier)

        try:
            obj.destroyed.connect(on_destroyed)
        except RuntimeError as error:
            self.emit("observation.error", error_type=type(error).__name__, operation="subscribe_destroyed")
        else:
            self.callbacks[identifier] = on_destroyed
            self.records[identifier]["destruction_observation_available"] = True
        return identifier

    def parent_state(self, obj):
        parent = obj.parent()
        if parent is None:
            return None
        return {"object_id": self.identity(parent), "type": type(parent).__name__,
                "python_owned": ownedByPython(parent)}

    def state(self, obj):
        return {"visible": obj.isVisible(), "hidden": obj.isHidden(),
                "parent": self.parent_state(obj), "python_owned": ownedByPython(obj)}

    def observe(self, obj, *, constructor=False, stack=None, location=None):
        identifier = self.identity(obj)
        record = self.records[identifier]
        state = self.state(obj)
        if not record["message"]:
            record.update(message=True, origin="unintercepted_first_seen", creator_test=None,
                          creation_phase=None, creation_stack=[], collection_node=None,
                          first_seen_test=self.test, first_seen_phase=self.phase,
                          first_seen_location=location, first_seen_stack=self.creation_stack(),
                          initial_parent=state["parent"])
            event = self.emit("message.first_seen", dialog_id=identifier, state=state,
                              origin=record["origin"], creator_known=False,
                              observation_stack=record["first_seen_stack"])
            if event:
                record["history"].append(event)
        if constructor and record["origin"] != "python_constructor":
            record.update(origin="python_constructor", creator_test=self.test,
                          creation_phase=self.phase, creation_stack=stack or [],
                          collection_node=self.collection if self.test is None else None,
                          initial_parent=state["parent"])
            event = self.emit("message.created", dialog_id=identifier, state=state,
                              origin=record["origin"], creator_test=self.test,
                              collection_node=record["collection_node"], creation_stack=record["creation_stack"])
            if event:
                record["history"].append(event)
        record["last_state"] = state
        return identifier

    def lifecycle(self, obj, kind):
        identifier = self.observe(obj, location=f"event:{kind}")
        event = self.emit("message.event", dialog_id=identifier, kind=kind,
                          delivery="before_handler", state=self.state(obj))
        if event:
            self.records[identifier]["history"].append(event)

    def ensure_application_filter(self):
        application = QApplication.instance()
        if application is None:
            return
        previous = self.application() if self.application is not None else None
        if previous is application:
            return
        if self.filter is None:
            tracker_ref = weakref.ref(self)

            class Observer(QObject):
                def eventFilter(self, watched, event):
                    tracker = tracker_ref()
                    if tracker is not None and not tracker.closed:
                        try:
                            if (isinstance(watched, QMessageBox) and isValid(watched)
                                    and event.type() in LIFECYCLE_EVENTS):
                                tracker.lifecycle(watched, event.type().name)
                        except Exception as error:
                            tracker.emit("observation.error", error_type=type(error).__name__)
                    return False

            self.filter = Observer()  # Owned by tracing, never parented to application/dialogs.
        application.installEventFilter(self.filter)
        self.application = weakref.ref(application)
        self.emit("application.observed", application_id=self.identity(application))

    def scan(self, location):
        try:
            self.ensure_application_filter()
            if QApplication.instance() is not None:
                identifiers = [self.observe(obj, location=location) for obj in QApplication.topLevelWidgets()
                               if isinstance(obj, QMessageBox) and isValid(obj)]
                self.emit("message.snapshot", location=location, surviving_dialogs=identifiers)
        except Exception as error:
            self.emit("observation.error", error_type=type(error).__name__)

    def describe(self, obj):
        identifier = self.observe(obj, location="language_guard")
        record = self.records[identifier]
        initial_parent = record["initial_parent"]
        # Detached metadata: later destruction cannot rewrite the guard snapshot.
        return deepcopy({"dialog_id": identifier, "origin": record["origin"],
                         "creator_test": record["creator_test"], "creation_phase": record["creation_phase"],
                         "creation_stack": record["creation_stack"], "collection_node": record["collection_node"],
                         "first_seen_test": record["first_seen_test"], "first_seen_phase": record["first_seen_phase"],
                         "first_seen_location": record["first_seen_location"], "first_seen_stack": record["first_seen_stack"],
                         "initial_parent": record["initial_parent"], "current_state": record["last_state"],
                         "initial_parent_destruction_observed": (self.records[initial_parent["object_id"]]["destroyed"]
                                                                  if initial_parent else None),
                         "destruction_observation_available": record["destruction_observation_available"],
                         "destruction_observed": record["destroyed"], "recent_lifecycle": record["history"][-12:]})

    def install(self):
        from unittest.mock import patch
        # Open once before test filesystem guards: app I/O remains fully guarded.
        self.stream = self.destination.open("x", encoding="utf-8")
        original_init, original_delete = QMessageBox.__init__, QMessageBox.deleteLater
        tracker_ref = weakref.ref(self)

        def observed_init(obj, *args, **kwargs):
            tracker = tracker_ref()
            stack = tracker.creation_stack() if tracker is not None else []
            result = original_init(obj, *args, **kwargs)
            if tracker is not None and not tracker.closed:
                try:
                    tracker.ensure_application_filter()
                    tracker.observe(obj, constructor=True, stack=stack, location="python_constructor")
                except Exception as error:
                    tracker.emit("observation.error", error_type=type(error).__name__)
            return result

        def observed_delete(obj):
            tracker = tracker_ref()
            if tracker is not None and not tracker.closed:
                try:
                    identifier = tracker.observe(obj, location="python_delete_later")
                    event = tracker.emit("message.delete_later_requested", dialog_id=identifier)
                    if event:
                        tracker.records[identifier]["history"].append(event)
                except Exception as error:
                    tracker.emit("observation.error", error_type=type(error).__name__)
            return original_delete(obj)  # Original call, once, no processing of events.

        for name, replacement in (("__init__", observed_init), ("deleteLater", observed_delete)):
            context = patch.object(QMessageBox, name, replacement)
            context.start()
            self.patches.append(context)
        self.emit("tracker.started", limitations=[
            "C++/static/bypassed constructors have unknown creation provenance; first observation is not creation.",
            "Events before QApplication filtering or after tracing stops are not observable.",
            "Lifecycle event snapshots precede widget handling; Close/Hide do not prove destruction.",
            "Native addresses and wrapper IDs are not logged; tracing adds observational overhead.",
        ])
        self.scan("tracing_start")

    def close(self):
        if self.closed:
            return
        if self.stream is not None:
            self.scan("tracing_stop")
            self.emit("tracker.stopped")
        self.closed = True
        for context in reversed(self.patches):
            context.stop()
        self.patches.clear()
        application = self.application() if self.application is not None else None
        if application is not None and isValid(application) and self.filter is not None:
            application.removeEventFilter(self.filter)
        for identifier, reference in self.references.items():
            obj = reference()
            if (obj is not None and isValid(obj) and not self.records[identifier]["destroyed"]
                    and identifier in self.callbacks):
                try:
                    obj.destroyed.disconnect(self.callbacks[identifier])
                except (RuntimeError, TypeError):
                    pass
        self.filter = None
        if self.stream is not None:
            self.stream.close()


def correlate_message_box(obj):
    if _ACTIVE_TRACKER is None or _ACTIVE_TRACKER.closed or not isinstance(obj, QMessageBox):
        return None
    try:
        return _ACTIVE_TRACKER.describe(obj)
    except Exception as error:
        return {"tracing_error_type": type(error).__name__}


def pytest_configure(config):
    global _ACTIVE_TRACKER
    if os.environ.get("DMS_QT_LANGUAGE_DIAGNOSTICS") != "1":
        return
    tracker = DialogOwnershipTracker(os.environ[TRACE_ENV], config.rootpath)
    _ACTIVE_TRACKER = tracker
    try:
        tracker.install()
    except BaseException:
        tracker.close()
        _ACTIVE_TRACKER = None
        raise


def pytest_collectstart(collector):
    if _ACTIVE_TRACKER is not None:
        _ACTIVE_TRACKER.phase = "collection"
        if collector.nodeid.endswith(".py"):
            _ACTIVE_TRACKER.collection = collector.nodeid


def pytest_collectreport(report):
    if _ACTIVE_TRACKER is not None:
        _ACTIVE_TRACKER.scan("collection_report")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    tracker = _ACTIVE_TRACKER
    if tracker is not None:
        tracker.test, tracker.phase = item.nodeid, "setup"
        tracker.scan("test_start")
    try:
        yield
    finally:
        if tracker is not None:
            tracker.scan("test_end")
            tracker.test, tracker.phase = None, "between_tests"


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    yield
    if _ACTIVE_TRACKER is not None:
        _ACTIVE_TRACKER.scan("fixtures_ready")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    if _ACTIVE_TRACKER is not None:
        _ACTIVE_TRACKER.phase = "call"


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    if _ACTIVE_TRACKER is not None:
        _ACTIVE_TRACKER.phase = "teardown"
    yield
    if _ACTIVE_TRACKER is not None:
        _ACTIVE_TRACKER.scan("fixtures_finished")


def pytest_unconfigure(config):
    global _ACTIVE_TRACKER
    if _ACTIVE_TRACKER is not None:
        _ACTIVE_TRACKER.close()
        _ACTIVE_TRACKER = None
