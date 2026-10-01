"""Opt-in lifecycle for GUI tests that create their own top-level widgets."""

from contextlib import contextmanager

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


@contextmanager
def owned_test_widgets():
    # Keep existing objects alive and outside this test's cleanup scope.
    existing = list(QApplication.topLevelWidgets()) if QApplication.instance() else []
    try:
        yield
    finally:
        if QApplication.instance():
            owned = [widget for widget in QApplication.topLevelWidgets()
                     if widget.parent() is None and widget not in existing]
            for widget in owned:
                if not isValid(widget):
                    continue
                stop_worker = getattr(widget, "stop_worker", None)
                if stop_worker is not None:
                    assert stop_worker(), "A test worker did not stop"
                assert widget.close(), "A test window refused to close"
                assert not any(thread.isRunning() for thread in widget.findChildren(QThread))
                widget.deleteLater()
                # Deliver only this object's deletion, not unrelated pending events.
                QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
                assert not isValid(widget), "A test widget was not destroyed"


@pytest.fixture(autouse=True)
def qt_widget_lifecycle():
    with owned_test_widgets():
        yield
