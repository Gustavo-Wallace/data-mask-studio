"""Regression coverage for test-owned worker shutdown, without timing limits."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from data_mask_studio.app import create_application
from qt_lifecycle import owned_test_widgets, qt_widget_lifecycle


@pytest.mark.parametrize("method", ["stop_worker", "stop_workers"])
def test_owned_widget_stops_worker_before_destruction(method):
    application = create_application([])
    stopped = []
    with owned_test_widgets():
        widget = QWidget()
        worker = QThread(widget)

        def stop():
            worker.quit()
            finished = worker.wait(5000)
            stopped.append(finished)
            return finished

        setattr(widget, method, stop)
        worker.start()
    assert stopped == [True]
    assert not isValid(widget)
    assert not isValid(worker)
    assert isValid(application)
