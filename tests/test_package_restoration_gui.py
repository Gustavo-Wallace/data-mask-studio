import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.restoration_widget import RestorationWidget
from data_mask_studio.gui import restoration_worker as worker_module
from data_mask_studio.restoration import MissingCodePolicy, RestorationCancelled
from data_mask_studio.transfer_package import encrypt_package
from test_package_csv_restoration import make_case, PASSWORD, read_rows


@pytest.fixture
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated"))
    app = create_application([])
    def forbidden():
        pytest.fail("Package mode must never access the local vault")
    widget = RestorationWidget(forbidden)
    source = tmp_path / "input.csv"
    source.write_text("Name\nNAME-ABCDEFGHIJKL\n", encoding="utf-8")
    widget.load_csv(str(source))
    widget.select_all_columns()
    return app, widget, source


def enable(widget, path, password=PASSWORD):
    widget.source_combo.setCurrentIndex(1)
    widget.package_controls.path.setText(str(path))
    widget.package_controls.password.setText(password)


def finish(app, widget):
    worker = widget._worker
    assert worker is not None
    assert worker.wait(15000)
    assert worker._package_request is None
    app.processEvents()
    assert widget._worker is None
    assert not widget.package_controls.password.text()
    assert not widget.package_controls.show_password.isChecked()
    return worker


def test_source_controls_and_transient_password(gui):
    _, widget, _ = gui
    controls = widget.package_controls
    assert widget.source_combo.currentText() == "Cofre local"
    assert controls.isHidden() and not controls.isEnabled()
    assert widget.analyze_button.isEnabled()
    widget.source_combo.setCurrentIndex(1)
    assert not controls.isHidden() and controls.isEnabled()
    assert not widget.analyze_button.isEnabled()
    assert not widget.missing_policy_combo.isEnabled()
    assert widget.missing_policy_combo.currentData() == MissingCodePolicy.ABORT.value
    assert widget._configuration().missing_code_policy is MissingCodePolicy.ABORT
    widget.start_analysis()  # Even direct calls must not consult the vault.
    assert widget._worker is None
    assert controls.password.echoMode() is QLineEdit.EchoMode.Password
    controls.password.setText("short")
    for visible in (True, False):
        controls.show_password.setChecked(visible)
        assert controls.password.text() == "short"
        assert controls.password.echoMode() is (
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
    widget.source_combo.setCurrentIndex(0)
    assert controls.isHidden() and not controls.password.text()
    assert widget.analyze_button.isEnabled() and widget.missing_policy_combo.isEnabled()
    assert widget.missing_policy_combo.currentData() == MissingCodePolicy.KEEP.value


def test_package_state_not_persisted_in_new_widget(gui, tmp_path):
    _, widget, _ = gui
    enable(widget, tmp_path / "private.dmspackage")
    widget.package_controls.show_password.setChecked(True)
    other = RestorationWidget(lambda: pytest.fail("No vault access"))
    assert other.source_combo.currentData() == "vault"
    assert not other.package_controls.path.text()
    assert not other.package_controls.password.text()
    assert not other.package_controls.show_password.isChecked()
    assert not (tmp_path / "isolated").exists()


@pytest.mark.parametrize("case", ["empty_password", "empty_path", "missing", "extension", "directory", "no_csv", "no_columns", "output"])
def test_basic_validation_prevents_launch(gui, tmp_path, monkeypatch, case):
    _, widget, _ = gui
    path = tmp_path / "sample.dmspackage"
    path.write_bytes(b"test")
    enable(widget, path)
    output = tmp_path / "out.csv"
    if case == "empty_password": widget.package_controls.password.clear()
    elif case == "empty_path": widget.package_controls.path.clear()
    elif case == "missing": path.unlink()
    elif case == "extension": widget.package_controls.path.setText(str(tmp_path / "file.zip"))
    elif case == "directory": widget.package_controls.path.setText(str(tmp_path))
    elif case == "no_csv": widget.clear_selection()
    elif case == "no_columns": widget.unselect_all_columns()
    else: output = tmp_path / "absent" / "out.csv"
    monkeypatch.setattr(worker_module.CSVRestorationWorker, "start", lambda *_: pytest.fail("Must not start"))
    widget.start_restoration(output, overwrite=False)
    assert widget._worker is None and not output.exists()
    assert PASSWORD not in widget.status_label.text()


@pytest.mark.parametrize("reset", ["load", "clear", "success", "failure", "cancel"])
def test_password_reset(gui, tmp_path, reset):
    _, widget, source = gui
    enable(widget, tmp_path / "file.dmspackage")
    controls = widget.package_controls
    controls.show_password.setChecked(True)
    if reset == "load": widget.load_csv(str(source))
    elif reset == "clear": widget.clear_selection()
    elif reset == "failure": widget._failed(RuntimeError(PASSWORD))
    elif reset == "cancel": widget._cancelled()
    else: widget._worker_finished()
    assert not controls.password.text() and not controls.show_password.isChecked()
    assert controls.password.echoMode() is QLineEdit.EchoMode.Password


def test_short_password_forwarded_off_gui_thread_and_cancellation(gui, tmp_path, monkeypatch):
    app, widget, _ = gui
    path = tmp_path / "sample.dmspackage"
    path.write_bytes(b"test")
    enable(widget, path, "x")
    observed = []
    from threading import Event
    entered, proceed = Event(), Event()
    def backend(config, output, package, password, **kwargs):
        observed.append((QThread.currentThread() != app.thread(), password, package, config.missing_code_policy))
        entered.set()
        assert proceed.wait(5)
        assert kwargs["should_cancel"]()
        raise RestorationCancelled("cancelled")
    monkeypatch.setattr(worker_module, "restore_csv_from_package", backend)
    widget.start_restoration(tmp_path / "out.csv", overwrite=False)
    try:
        assert entered.wait(5)
        assert not widget.source_combo.isEnabled()
        assert not widget.package_controls.password.text()
        assert not widget.package_controls.isEnabled()
        widget.cancel_processing()
    finally:
        proceed.set()
    finish(app, widget)
    assert observed == [(True, "x", path, MissingCodePolicy.ABORT)]
    assert "cancelada" in widget.status_label.text()
    assert widget.progress_bar.value() == 0
    assert widget.source_combo.isEnabled()


@pytest.mark.parametrize("case", ["success", "password", "binding", "scalar", "composite", "invalid", "unsupported", "unreadable", "publication"])
def test_real_package_restoration_without_vault(gui, tmp_path, monkeypatch, case):
    app, widget, _ = gui
    _, config, path, payload, output = make_case(tmp_path)
    widget.load_csv(str(config.source_path))
    widget._checkboxes[0].setChecked(True)
    widget._checkboxes[1].setChecked(True)
    enable(widget, path)
    if case == "password": widget.package_controls.password.setText("wrong password")
    elif case == "binding": config.source_path.write_bytes(config.source_path.read_bytes() + b"\n")
    elif case in ("scalar", "composite"):
        path.write_bytes(encrypt_package(replace(payload, **{case + "_mappings": ()}), PASSWORD))
    elif case == "invalid": path.write_bytes(b"invalid")
    elif case == "unsupported":
        from data_mask_studio.transfer_package.service import MAGIC
        content = bytearray(path.read_bytes())
        content[len(MAGIC):len(MAGIC) + 2] = b"\x00\x02"
        path.write_bytes(content)
    elif case == "unreadable":
        def fail_read(*args, **kwargs): raise PermissionError(PASSWORD)
        monkeypatch.setattr("data_mask_studio.transfer_package.restoration_source.read_package", fail_read)
    elif case == "publication":
        def fail(*args, **kwargs): raise OSError(PASSWORD)
        monkeypatch.setattr("data_mask_studio.restoration.csv_restorer.publish", fail)
    def forbidden(*args, **kwargs): pytest.fail("No local environment access")
    monkeypatch.setattr("data_mask_studio.environment.environment_lease", forbidden)
    widget.start_restoration(output, overwrite=False)
    assert not widget.package_controls.password.text()
    finish(app, widget)
    if case == "success":
        assert read_rows(output)[1][0] == " Synthetic Person "
        assert widget.progress_bar.value() == widget.progress_bar.maximum() == 1
        assert widget._last_output_path == output
        assert "sucesso" in widget.status_label.text()
    else:
        assert not output.exists() and widget._last_output_path is None
        assert widget.progress_bar.value() == 0
        assert widget._last_error.__traceback__ is None
        assert widget._last_error.__context__ is None
        assert "sucesso" not in widget.status_label.text()
    assert PASSWORD not in widget.status_label.text() + widget.summary.toPlainText()
    assert not list(tmp_path.glob("*.tmp"))


def test_local_worker_does_not_invoke_package_backend(gui, tmp_path, monkeypatch):
    app, widget, _ = gui
    calls = []
    def local(*args, **kwargs):
        assert "package_request" not in kwargs
        calls.append(True)
        raise RestorationCancelled("cancelled")
    monkeypatch.setattr(widget._service, "restore", local)
    monkeypatch.setattr(worker_module, "restore_csv_from_package", lambda *a, **k: pytest.fail("Not package mode"))
    widget.start_restoration(tmp_path / "out.csv", overwrite=False)
    finish(app, widget)
    assert calls == [True]


@pytest.mark.parametrize("overwrite", [False, True])
def test_package_output_confirmation_and_browse(gui, tmp_path, monkeypatch, overwrite):
    app, widget, _ = gui
    _, config, package, _, output = make_case(tmp_path)
    widget.load_csv(str(config.source_path))
    widget.select_all_columns()
    widget.source_combo.setCurrentIndex(1)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(package), ""))
    widget.package_controls.browse.click()
    widget.package_controls.password.setText(PASSWORD)
    output.write_text("protected", encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes if overwrite else QMessageBox.StandardButton.No)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), ""))
    widget._choose_output()
    if overwrite:
        finish(app, widget)
        assert read_rows(output)[1][0] == " Synthetic Person "
    else:
        assert widget._worker is None and output.read_text() == "protected"
