"""Profile write locks remain independent of A02's active Qt batch state."""

import pytest
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from test_batch_profile_isolation import page, finish, pause_after_first, pump_until, validate
from test_profile_write_consistency import child_process, hold_write_lock, receive
from data_mask_studio.batch import BatchFileStatus
from data_mask_studio.environment import EnvironmentError, environment_lease


@pytest.fixture
def profile_gui(page, monkeypatch):
    app, window, batch, _, profile, _ = page
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "exec", lambda *a, **k: QMessageBox.StandardButton.Yes)
    individual = window.anonymization_widget
    individual.load_csv(str(batch.files[0].path))
    individual.refresh_profiles(profile.identifier)
    individual.apply_selected_profile()
    assert individual._configuration_validated
    notifications = []
    individual.profiles_changed.connect(lambda: notifications.append(True))
    return page, individual, notifications


@pytest.mark.parametrize("change", ["update", "delete"])
def test_profile_gui_crud_succeeds_during_batch_shared_environment_lease(profile_gui, monkeypatch, change):
    page, individual, notifications = profile_gui
    app, window, batch, profiles, profile, output = page
    root = profiles.repository.path.parent
    # Unlike a directory-less fake key provider, this exercises the real shared
    # environment lease in BatchService.process for the entire two-file run.
    window._key_provider.key_path = root / "secret.key"
    validate(page)
    pause = pause_after_first(page, monkeypatch, "processing")
    batch.start_processing()
    worker = batch._processing_worker
    try:
        pump_until(app, pause.reached.is_set)
        with pytest.raises(EnvironmentError):
            with environment_lease(root, exclusive=True):
                pytest.fail("The active batch must still hold its shared lease")
        first_output = batch.files[0].output_path
        if change == "update":
            individual._prefix_fields[0].setText("CHANGED")
            individual.validate_current_configuration()
            individual.update_profile_button.click()
            assert profiles.list_profiles()[0].columns[0].prefix == "CHANGED"
        else:
            individual.delete_profile_button.click()
            assert profiles.list_profiles() == []
        assert notifications == [True]
        assert individual.status_label.property("feedbackState") == "success"
        assert worker._profile == profile
        assert batch.files[0].status is BatchFileStatus.COMPLETED
        assert batch.files[0].output_path == first_output
    finally:
        pause.release.set()
        assert worker.wait(10000)
        app.processEvents()
    finish(app, batch, "_processing_worker")
    assert all(item.status is BatchFileStatus.COMPLETED for item in batch.files)
    assert all(item.output_path and item.output_path.is_file() for item in batch.files)
    assert "Arquivos concluídos: 2" in batch.summary_output.toPlainText()
    assert batch.overall_progress.value() == batch.overall_progress.maximum() == 2
    assert batch.open_output_button.isEnabled() and batch._output_directory == output
    if change == "update":
        assert batch._selected_profile().columns[0].prefix == "CHANGED"
        validate(page)
        assert batch.start_button.isEnabled()
    else:
        assert batch._selected_profile() is None
        assert not batch.validate_button.isEnabled()


def test_busy_profile_write_reports_safe_gui_error_without_success_notification(profile_gui):
    page, individual, notifications = profile_gui
    _, _, batch, profiles, profile, _ = page
    validate(page)
    individual._prefix_fields[0].setText("CHANGED")
    individual.validate_current_configuration()
    with child_process(hold_write_lock, (profiles.repository.path,)) as (process, channel):
        assert receive(channel) == ("locked",)
        individual.update_profile_button.click()
        assert "outra instância" in individual.status_label.text()
        assert individual.status_label.property("feedbackState") == "error"
        assert notifications == []
        assert profiles.list_profiles() == [profile]
        assert all(item.status is BatchFileStatus.COMPATIBLE for item in batch.files)
        channel.send("release")
        assert receive(channel) == ("released",)
        individual.update_profile_button.click()  # Explicit retry, not a silent overwrite.
        assert notifications == [True]
        assert profiles.list_profiles()[0].columns[0].prefix == "CHANGED"
        assert all(item.status is BatchFileStatus.PENDING for item in batch.files)
        assert not batch.start_button.isEnabled()
        channel.send("exit")
        process.join(5)
        assert process.exitcode == 0
