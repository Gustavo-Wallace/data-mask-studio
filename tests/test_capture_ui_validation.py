"""Screenshot filenames must describe the real active page, not a stale index."""

import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from qt_lifecycle import qt_widget_lifecycle
from scripts import capture_ui_validation as script
from test_main_navigation import build_window


EXPECTED_PAGES = {
    "anonimizar-csv": "anonymization_widget",
    "restauracao-lote": "batch_restoration_widget",
    "backup-recuperacao": "backup_widget",
    # The historical filename now denotes the consolidated diagnostic overview.
    "integridade": "maintenance_widget",
    "cofre-manutencao": "maintenance_widget",
}


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.fixture
def page(tmp_path):
    application = script.create_application([])
    window = build_window(tmp_path)
    window.resize(1100, 760)
    window.show()
    application.processEvents()
    return application, window


def test_capture_configuration_preserves_filenames_dimensions_and_count():
    assert script.CAPTURE_PAGES == EXPECTED_PAGES
    assert script.CAPTURE_SIZES == ((1100, 760), (1280, 800), (1920, 1080))
    assert len(script.CAPTURE_PAGES) * len(script.CAPTURE_SIZES) == 15


@pytest.mark.parametrize("name", EXPECTED_PAGES)
def test_every_capture_selects_the_real_registered_widget(page, name):
    application, window = page
    target = getattr(window, EXPECTED_PAGES[name])
    if name == "integridade":
        sections = window.maintenance_widget.sections
        overview = window.maintenance_widget.overview_output.parentWidget()
        sections.tabBar().moveTab(sections.indexOf(overview), sections.count() - 1)
        sections.setCurrentWidget(window.maintenance_widget.backup_result.parentWidget())
    captured = script.capture_page(application, window, name)
    assert not captured.isNull()
    assert window.page_stack.currentWidget().content is target
    assert window.current_page_index() == window.navigation.current_index() == window.page_index(target)
    assert target.isVisible()
    if name == "integridade":
        assert sections.currentWidget() is overview


@pytest.mark.parametrize("fault", ["unknown", "missing", "not_widget", "unregistered", "destroyed"])
def test_missing_or_invalid_page_fails_before_capture(page, monkeypatch, fault):
    application, window = page
    name = "restauracao-lote"
    previous = window.page_stack.currentWidget()
    monkeypatch.setattr(window, "grab", lambda: pytest.fail("An invalid target must never be captured"))
    if fault == "unknown":
        name = "unconfigured-page"
    else:
        attribute = "_invalid_capture_page"
        if fault == "missing":
            target = None
        elif fault == "not_widget":
            target = object()
        else:
            target = QWidget(window)
            if fault == "destroyed":
                target.deleteLater()
                QCoreApplication.sendPostedEvents(target, QEvent.Type.DeferredDelete)
                assert not isValid(target)
        if fault != "missing":
            monkeypatch.setattr(window, attribute, target, raising=False)
        monkeypatch.setitem(script.CAPTURE_PAGES, name, attribute)
    with pytest.raises(RuntimeError, match=name):
        script.capture_page(application, window, name)
    assert window.page_stack.currentWidget() is previous


@pytest.mark.parametrize("index", [-1, 99, None, True, 1.5])
def test_invalid_page_index_fails_without_retaining_previous_page_as_a_capture(page, monkeypatch, index):
    application, window = page
    previous = window.page_stack.currentWidget()
    monkeypatch.setattr(window, "page_index", lambda target: index)
    monkeypatch.setattr(window, "grab", lambda: pytest.fail("An invalid index must never be captured"))
    with pytest.raises(RuntimeError, match="restauracao-lote"):
        script.capture_page(application, window, "restauracao-lote")
    assert window.page_stack.currentWidget() is previous


@pytest.mark.parametrize("fault", ["wrong_index", "wrong_shell"])
def test_registered_page_and_stack_content_must_agree_before_capture(page, monkeypatch, fault):
    application, window = page
    if fault == "wrong_index":
        wrong_index = window.page_index(window.consultant_widget)
        monkeypatch.setattr(window, "page_index", lambda target: wrong_index)
    else:
        shell = window.page_stack.widget(window.page_index(window.batch_restoration_widget))
        window.page_stack.removeWidget(shell)
    monkeypatch.setattr(window, "grab", lambda: pytest.fail("Mismatched content must never be captured"))
    with pytest.raises(RuntimeError, match="restauracao-lote"):
        script.capture_page(application, window, "restauracao-lote")


@pytest.mark.parametrize("fault", ["disabled_navigation", "hidden_content", "queued_navigation", "queued_tab"])
def test_actual_active_page_is_verified_after_navigation_and_pending_events(page, monkeypatch, fault):
    application, window = page
    name = "integridade" if fault == "queued_tab" else "restauracao-lote"
    if fault == "disabled_navigation":
        window.navigation.set_page_enabled(window.page_index(window.batch_restoration_widget), False)
    elif fault == "hidden_content":
        window.batch_restoration_widget.hide()
    elif fault == "queued_navigation":
        QTimer.singleShot(0, lambda: window.set_current_page(window.page_index(window.consultant_widget)))
    else:
        sections = window.maintenance_widget.sections
        QTimer.singleShot(0, lambda: sections.setCurrentWidget(window.maintenance_widget.backup_result.parentWidget()))
    monkeypatch.setattr(window, "grab", lambda: pytest.fail("The wrong active page must never be captured"))
    with pytest.raises(RuntimeError, match=name):
        script.capture_page(application, window, name)


def test_script_captures_every_label_from_its_intended_real_page(tmp_path, monkeypatch, capsys):
    output, readme = tmp_path / "captures", tmp_path / "readme.png"
    captures = []
    windows = []
    real_window = script.MainWindow

    def tracked_window(*args, **kwargs):
        window = real_window(*args, **kwargs)
        windows.append(window)
        grab = window.grab

        def checked_grab():
            name, attribute = list(EXPECTED_PAGES.items())[len(captures) % len(EXPECTED_PAGES)]
            target = getattr(window, attribute)
            assert window.page_stack.currentWidget().content is target, name
            assert window.current_page_index() == window.page_index(target), name
            if name == "integridade":
                assert window.maintenance_widget.sections.currentWidget() is window.maintenance_widget.overview_output.parentWidget()
            captured = grab()
            assert not captured.isNull()
            captures.append(name)
            return captured

        monkeypatch.setattr(window, "grab", checked_grab)
        return window

    monkeypatch.setattr(script, "MainWindow", tracked_window)
    # One of the existing sizes is enough to exercise all mappings and README
    # export without generating a screenshot-size matrix in regression tests.
    monkeypatch.setattr(script, "CAPTURE_SIZES", ((1280, 800),), raising=False)
    monkeypatch.setattr(sys, "argv", ["capture_ui_validation.py", "--output", str(output),
                                      "--readme-capture", str(readme)])
    assert not output.exists()  # Never exercise the script's existing cleanup on user files.
    assert script.main() == 0
    assert captures == list(EXPECTED_PAGES)
    assert {path.name for path in output.glob("*.png")} == {
        f"1280x800-{name}.png" for name in EXPECTED_PAGES}
    for path in output.glob("*.png"):
        assert not QImage(str(path)).isNull()
    image = QImage(str(readme))
    assert not image.isNull() and (image.width(), image.height()) == (1280, 800)
    assert (output / "isolated-local-data").is_dir()
    assert len(windows) == 1 and not windows[0].isVisible()
    assert "5 capturas salvas" in capsys.readouterr().out
