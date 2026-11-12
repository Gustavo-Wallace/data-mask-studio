"""Synthetic provenance/lifetime checks; never replay a real shard locally."""

import json
import os
from pathlib import Path
import subprocess
import sys
import weakref

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget
from shiboken6 import isValid, ownedByPython

from qt_lifecycle import owned_test_widgets, qt_widget_lifecycle
from scripts import ci_qt_dialog_ownership as ownership
from language_save_diagnostics import LanguageSaveTrace
from data_mask_studio.preferences import PreferencesService
from test_bootstrap_localization import app, dialog_result
from test_live_language_settings import WaitingWorker
from test_navigation_localization import build_localized_window


@pytest.fixture
def tracker(app, tmp_path, request):
    trace = ownership.DialogOwnershipTracker(tmp_path / "ownership.jsonl", Path(__file__).resolve().parents[1])
    trace.test, trace.phase = request.node.nodeid, "call"
    trace.install()
    try:
        yield trace
    finally:
        trace.close()


def rows(tracker):
    return [json.loads(line) for line in tracker.destination.read_text(encoding="utf-8").splitlines()]


def test_tracker_does_not_keep_parentless_dialog_alive_and_ids_identify_lifetimes(tracker):
    original = QMessageBox()
    assert ownedByPython(original) and original.parent() is None
    first = tracker.describe(original)
    reference = weakref.ref(original)
    del original
    assert reference() is None
    assert tracker.records[first["dialog_id"]]["destroyed"]
    assert any(row["event"] == "qobject.destroyed" and row["object_id"] == first["dialog_id"] for row in rows(tracker))
    replacement = QMessageBox()
    assert tracker.describe(replacement)["dialog_id"] != first["dialog_id"]
    assert first["destruction_observed"] is False  # Detached historic snapshot.


def test_parent_identity_and_ownership_are_observed_without_retaining_either_object(tracker):
    parent = QWidget()
    message = QMessageBox(parent)
    assert ownedByPython(parent) and not ownedByPython(message)
    before = tracker.describe(message)
    assert before["current_state"]["parent"]["type"] == "QWidget"
    assert before["initial_parent"] == before["current_state"]["parent"]
    assert before["creator_test"] == tracker.test
    assert before["creation_stack"] and all(set(frame) == {"file", "line", "function"}
                                            for frame in before["creation_stack"])
    parent_ref, message_ref = weakref.ref(parent), weakref.ref(message)
    del message
    del parent
    assert parent_ref() is None
    assert message_ref() is None or not isValid(message_ref())
    assert tracker.records[before["dialog_id"]]["destroyed"]
    assert tracker.records[before["initial_parent"]["object_id"]]["destroyed"]


def test_show_hide_close_and_targeted_owned_teardown_are_distinct_lifecycle_events(tracker):
    with owned_test_widgets():
        message = QMessageBox()
        identifier = tracker.describe(message)["dialog_id"]
        message.show()
        message.hide()
        assert message.close()
        assert isValid(message)  # close/hide are not destruction.
        message.deleteLater()
        assert not tracker.records[identifier]["destroyed"]
    # The existing fixture owns the deletion, never the tracer.
    assert not isValid(message)
    observed = rows(tracker)
    kinds = {row["kind"] for row in observed if row["event"] == "message.event" and row["dialog_id"] == identifier}
    assert {"Show", "Hide", "Close", "DeferredDelete"}.issubset(kinds)
    assert any(row["event"] == "message.delete_later_requested" for row in observed)
    assert tracker.records[identifier]["destroyed"]


def test_unintercepted_constructor_is_not_falsely_attributed_to_observing_test(app, tmp_path, request):
    original = QMessageBox.__init__
    trace = ownership.DialogOwnershipTracker(tmp_path / "unintercepted.jsonl", Path(__file__).resolve().parents[1])
    trace.test, trace.phase = request.node.nodeid, "call"
    trace.install()
    try:
        message = QMessageBox.__new__(QMessageBox)
        original(message)  # Simulates a path bypassing our constructor wrapper.
        trace.scan("first_test_boundary")
        details = trace.describe(message)
        assert details["origin"] == "unintercepted_first_seen"
        assert details["creator_test"] is None and details["creation_stack"] == []
        assert details["first_seen_test"] == request.node.nodeid
        assert details["first_seen_location"] != "python_constructor"
    finally:
        trace.close()


def test_qt_static_message_box_has_explicit_unknown_creation_and_observed_destruction(tracker, app):
    def respond(message):
        message.button(QMessageBox.StandardButton.Ok).click()
    result = dialog_result(app, lambda: QMessageBox.information(
        None, "Synthetic static title", "PRIVATE STATIC SENTINEL", QMessageBox.StandardButton.Ok), respond)
    assert result == QMessageBox.StandardButton.Ok
    records = [record for record in tracker.records.values() if record["message"]]
    assert records and all(record["origin"] == "unintercepted_first_seen" for record in records)
    assert all(record["creator_test"] is None for record in records)
    assert all(record["destroyed"] for record in records)
    assert "PRIVATE STATIC SENTINEL" not in tracker.destination.read_text(encoding="utf-8")


def test_reparenting_keeps_dialog_id_and_observes_both_parent_lifetimes(tracker):
    original_parent, new_parent = QWidget(), QWidget()
    message = QMessageBox(original_parent)
    before = tracker.describe(message)
    message.setParent(new_parent)
    after = tracker.describe(message)
    assert before["dialog_id"] == after["dialog_id"]
    assert before["initial_parent"] == after["initial_parent"]
    assert after["current_state"]["parent"] != before["initial_parent"]
    assert not ownedByPython(message)
    del original_parent
    assert tracker.records[before["initial_parent"]["object_id"]]["destroyed"]
    assert isValid(message) and not tracker.records[before["dialog_id"]]["destroyed"]
    assert any(row["event"] == "message.event" and row["kind"] == "ParentChange" for row in rows(tracker))
    del message
    del new_parent
    assert tracker.records[before["dialog_id"]]["destroyed"]


def test_active_guard_correlates_hidden_dialog_with_origin_without_relaxing_decision(
    tracker, app, tmp_path, request, monkeypatch,
):
    monkeypatch.setattr(ownership, "_ACTIVE_TRACKER", tracker)
    monkeypatch.setitem(sys.modules, "ci_qt_dialog_ownership", ownership)
    language = LanguageSaveTrace(tmp_path / "language.jsonl")
    language.install()
    try:
        service = PreferencesService(tmp_path / "preferences.json")
        window = build_localized_window(tmp_path, preferences_service=service, live_language_switching=True)
        window.show()
        window.set_current_page(window.page_index(window.settings_widget))
        app.processEvents()  # Normal test setup; the tracing code never pumps.
        message = QMessageBox(window)
        message.setText("PRIVATE MESSAGE SENTINEL")
        message.setWindowTitle("PRIVATE TITLE SENTINEL")
        language.watch(window, service, WaitingWorker())
        settings = window.settings_widget
        settings.language_combo.setCurrentIndex(settings.language_combo.findData("en"))
        language.capture("before_language_save")
        settings.save_button.click()
        language.capture("after_language_save")
        gate = next(row for row in language.events if row["event"] == "gate.result")
        assert gate["allowed"] is False and window._translator.locale == "pt-BR"
        assert service.load().preferences.language.value == "en"
        info = next(obj["ownership"] for obj in gate["state"]["other_windows"] if "ownership" in obj)
        assert info["creator_test"] == request.node.nodeid
        assert info["origin"] == "python_constructor"
        assert info["dialog_id"] == tracker.describe(message)["dialog_id"]
        assert info["current_state"]["parent"]["type"] == "MainWindow"
    finally:
        language.close()
    raw = tracker.destination.read_text(encoding="utf-8") + language.destination.read_text(encoding="utf-8")
    assert "PRIVATE MESSAGE SENTINEL" not in raw and "PRIVATE TITLE SENTINEL" not in raw
    assert str(tmp_path).replace("\\", "\\\\") not in raw


def test_preopened_diagnostic_stream_does_not_interfere_with_application_io_guards(tracker, monkeypatch):
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("Application filesystem read"))
        message = QMessageBox()
        reference = weakref.ref(message)
        details = tracker.describe(message)
        del message
        assert reference() is None and tracker.records[details["dialog_id"]]["destroyed"]


def test_stopping_tracing_preserves_live_dialog_and_its_ownership(tracker):
    message = QMessageBox()
    before = (message.parent(), ownedByPython(message), message.isVisible())
    tracker.describe(message)
    tracker.close()
    assert isValid(message)
    assert (message.parent(), ownedByPython(message), message.isVisible()) == before
    reference = weakref.ref(message)
    del message
    assert reference() is None


def test_plugin_records_fixture_creation_before_body_and_original_order_across_modules(tmp_path):
    root = Path(__file__).resolve().parents[1]
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths=["tests"]\n', encoding="utf-8")
    (tests / "conftest.py").write_text(
        'import pytest\nfrom PySide6.QtWidgets import QApplication,QMessageBox\n'
        '@pytest.fixture(scope="session")\ndef app():\n    return QApplication([])\n'
        '@pytest.fixture\ndef held(app):\n    app.synthetic_message=QMessageBox()\n    return app\n', encoding="utf-8")
    (tests / "test_creator.py").write_text(
        'def test_create(held):\n    held.synthetic_message.hide()\n', encoding="utf-8")
    (tests / "test_later.py").write_text(
        'import weakref\nimport ci_qt_dialog_ownership as plugin\n'
        'def test_later(app):\n'
        '    message=app.synthetic_message\n'
        '    info=plugin.correlate_message_box(message)\n'
        '    assert info["creator_test"]=="tests/test_creator.py::test_create"\n'
        '    assert info["creation_phase"]=="setup"\n'
        '    assert any(f["file"]=="tests/conftest.py" for f in info["creation_stack"])\n'
        '    ref=weakref.ref(message)\n'
        '    del app.synthetic_message\n    del message\n    assert ref() is None\n', encoding="utf-8")
    environment = os.environ.copy()
    environment.update(PYTHONPATH=str(root / "scripts"), QT_QPA_PLATFORM="offscreen",
                       PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                       LOCALAPPDATA=str(tmp_path / "localappdata"), DMS_QT_LANGUAGE_DIAGNOSTICS="1",
                       DMS_QT_DIALOG_TRACE=str(tmp_path / "ownership.jsonl"))
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run([sys.executable, "-B", "-m", "pytest", "-p", "ci_qt_dialog_ownership",
                             "tests/test_creator.py", "tests/test_later.py", "-q", "-p", "no:cacheprovider",
                             "--basetemp", str(tmp_path / "base")], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = [json.loads(line) for line in (tmp_path / "ownership.jsonl").read_text(encoding="utf-8").splitlines()]
    assert observed[0]["event"] == "tracker.started" and observed[-1]["event"] == "tracker.stopped"
    created = next(row for row in observed if row["event"] == "message.created")
    assert created["creator_test"] == "tests/test_creator.py::test_create"
    assert any(row["event"] == "qobject.destroyed" and row["object_id"] == created["dialog_id"]
               for row in observed)
