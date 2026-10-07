import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QScrollArea, QVBoxLayout, QWidget

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.restoration_widget import RestorationWidget
from data_mask_studio.gui.restoration_worker import CSVRestorationWorker
from data_mask_studio.restoration import (
    RestorationError, RestorationProgress, RestorationStage, RestorationResult,
    MissingCodePolicy, RepresentationPolicy,
)
from test_main_navigation import build_window


def test_subtitle_and_configuration_dropdowns(tmp_path):
    app = create_application([])
    window = build_window(tmp_path)
    page = window.page_shells[window.page_index(window.restoration_widget)]
    assert page.header.description_label.text() == (
        "Recupere valores de colunas selecionadas usando o cofre local ou um pacote de transferência."
    )
    controls = [window.restoration_widget.source_combo,
                window.restoration_widget.missing_policy_combo,
                window.restoration_widget.representation_combo,
                window.anonymization_widget.profile_controls.profile_combo,
                window.batch_widget.profile_combo,
                window.batch_restoration_widget.missing_policy_combo,
                window.batch_restoration_widget.representation_combo,
                window.html_restoration_widget.missing_policy_combo,
                window.html_restoration_widget.representation_combo]
    assert all(isinstance(combo, ScrollSafeComboBox) for combo in controls)
    assert all(isinstance(combo, ScrollSafeComboBox) for combo in window.findChildren(QComboBox))
    assert app is not None


def test_closed_combo_scrolls_page_and_open_combo_remains_interactive():
    app = create_application([])
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    layout = QVBoxLayout(body)
    combo = ScrollSafeComboBox()
    combo.addItems([f"Option {index}" for index in range(60)])
    layout.addWidget(combo)
    layout.addSpacing(1500)
    scroll.setWidget(body)
    scroll.resize(400, 300)
    scroll.show()
    app.processEvents()
    combo.setFocus()
    changed = []
    combo.currentIndexChanged.connect(changed.append)
    QTest.wheelEvent(scroll.windowHandle(), combo.mapTo(scroll, combo.rect().center()), QPoint(0, -120))
    app.processEvents()
    assert combo.currentIndex() == 0 and not changed
    assert scroll.verticalScrollBar().value() > 0
    scroll.verticalScrollBar().setValue(0)
    QTest.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentIndex() == 1 and changed == [1]
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    app.processEvents()
    view = combo.view()
    assert view.isVisible()
    page_position = scroll.verticalScrollBar().value()
    before = view.verticalScrollBar().value()
    popup = view.window()
    QTest.wheelEvent(popup.windowHandle(), view.viewport().mapTo(popup, view.viewport().rect().center()), QPoint(0, -120))
    app.processEvents()
    assert view.verticalScrollBar().value() > before
    assert scroll.verticalScrollBar().value() == page_position
    QTest.keyClick(view, Qt.Key.Key_Down)
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert not view.isVisible()
    assert combo.currentIndex() != 1


@pytest.mark.parametrize("package", [False, True])
def test_restoration_consecutive_outcomes_reset_progress(tmp_path, monkeypatch, package):
    app = create_application([])
    widget = RestorationWidget(lambda: pytest.fail("No worker backend in this signal test"))
    source = tmp_path / "input.csv"
    source.write_text("Name\nTest\n", encoding="utf-8")
    widget.load_csv(str(source))
    widget.select_all_columns()
    path = tmp_path / "test.dmspackage"
    path.write_bytes(b"synthetic")
    if package:
        widget.source_combo.setCurrentIndex(1)
        widget.package_controls.path.setText(str(path))
    monkeypatch.setattr(CSVRestorationWorker, "start", lambda self: None)
    for outcome in ("success", "failure", "cancel", "success"):
        widget.package_controls.password.setText("synthetic password")
        output = tmp_path / "out.csv"
        widget.start_restoration(output, overwrite=False)
        worker = widget._worker
        assert worker is not None
        assert widget.progress_bar.maximum() == 0
        assert widget.progress_label.text() == "0 linhas processadas"
        assert widget.status_label.property("feedbackState") == "neutral"
        if outcome != "failure":
            worker.progress.emit(RestorationProgress(list(RestorationStage)[0], 3, 2))
        counters = widget.progress_label.text()
        if outcome == "success":
            worker.completed.emit(RestorationResult(output, 3, 2, 0, 1, 0, 0.1,
                MissingCodePolicy.ABORT, RepresentationPolicy.FIRST_ORIGINAL))
        elif outcome == "failure": worker.failed.emit(RestorationError("Erro seguro"))
        else: worker.cancelled.emit()
        status = widget.status_label.text()
        worker.finished.emit()
        assert widget._worker is None
        assert widget.progress_bar.maximum() == 1
        assert widget.progress_bar.value() == (1 if outcome == "success" else 0)
        assert widget.progress_label.text() == counters
        assert widget.status_label.text() == status
        assert widget.status_label.property("feedbackState") == {
            "success": "success", "failure": "error", "cancel": "warning",
        }[outcome]
    app.processEvents()
