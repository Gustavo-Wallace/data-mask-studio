"""Per-workflow file choosers keep their formats and cancellation contracts.

These captions belong to domain screens and are intentionally not migrated by
the shared-component localization block. Native dialog chrome remains Qt/OS
owned; intercepting the static entry points does not force non-native dialogs.
"""

import pytest
from PySide6.QtWidgets import QFileDialog, QLineEdit, QPlainTextEdit, QTextEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.profiles import ProfileRepository
from test_main_navigation import build_window


@pytest.fixture
def file_dialog_window(tmp_path, monkeypatch):
    local_appdata = tmp_path / "isolated-appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    app = create_application([])
    window = build_window(tmp_path)
    source = tmp_path / "synthetic # percent% unicodeç.csv"
    source.write_text("Name,Group\nSYNTHETIC,EXAMPLE\n", encoding="utf-8")
    window.anonymization_widget.load_csv(str(source))
    assert window.file_restoration_widget.load_file(source)
    window.restoration_widget.summary.setPlainText("Synthetic previous analysis")
    window.restoration_widget.progress_bar.setValue(67)

    transfer = window.anonymization_widget.transfer_controls
    transfer.set_mask_available(True)
    transfer.checkbox.setChecked(True)
    transfer.destination.setText(str(tmp_path / "manual package.dmspackage"))
    transfer.password.setText("synthetic package password")
    transfer.confirmation.setText("synthetic package password")
    package = window.restoration_widget.package_controls
    package.path.setText(str(tmp_path / "selected package.dmspackage"))
    package.password.setText("synthetic restore password")

    backup = window.backup_widget
    backup.destination_field.setText(str(tmp_path / "manual backup.dmsbackup"))
    backup.restore_file_field.setText(str(tmp_path / "selected backup.dmsbackup"))
    backup.restore_password_field.setText("synthetic backup password")
    backup.restore_summary.setPlainText("Synthetic previous backup validation")
    maintenance = window.maintenance_widget
    maintenance.backup_path_field.setText(str(tmp_path / "validated backup.dmsbackup"))
    maintenance.backup_result.setPlainText("Synthetic previous maintenance result")

    ProfileRepository(tmp_path / "local" / "profiles.json").save([])
    preferences = PreferencesService(local_appdata / "DataMaskStudio" / "preferences.json")
    assert preferences.save_language("en").error is None
    app.processEvents()
    return window


def _form_state(window):
    fields = tuple(
        (field, field.text(), field.echoMode())
        for field in window.findChildren(QLineEdit)
    )
    reports = tuple(
        (report, report.toPlainText())
        for widget_type in (QTextEdit, QPlainTextEdit)
        for report in window.findChildren(widget_type)
    )
    csv = window.restoration_widget
    return (
        fields, reports, window.anonymization_widget._inspection_result,
        csv._inspection, csv.progress_bar.value(), csv._last_output_path,
        csv.open_folder_button.isEnabled(),
        window.file_restoration_widget.stack.currentWidget(),
        window.anonymization_widget.transfer_controls._manual_destination,
    )


@pytest.mark.parametrize(
    "owner_path,handler,entry_point,caption,filter_text,initial_field",
    [
        ("anonymization_widget", "_select_csv", "getOpenFileName",
         "Selecionar arquivo CSV", "Arquivos CSV (*.csv)", None),
        ("file_restoration_widget", "_select_file", "getOpenFileName",
         "Selecionar arquivo mascarado",
         "Arquivos compatíveis (*.csv *.html *.htm);;Arquivos CSV (*.csv);;Arquivos HTML (*.html *.htm)",
         None),
        ("restoration_widget.package_controls", "_browse", "getOpenFileName",
         "Selecionar pacote de transferência", "Pacotes (*.dmspackage)", None),
        ("anonymization_widget.transfer_controls", "_browse", "getSaveFileName",
         "Salvar pacote de transferência", "Pacotes de transferência (*.dmspackage)", "destination"),
        ("backup_widget", "choose_restore_file", "getOpenFileName",
         "Selecionar backup", "Backup do Data Mask Studio (*.dmsbackup)", None),
        ("backup_widget", "choose_destination", "getSaveFileName",
         "Salvar backup criptografado", "Backup do Data Mask Studio (*.dmsbackup)", "destination_field"),
        ("maintenance_widget", "choose_backup", "getOpenFileName",
         "Selecionar backup", "Backup (*.dmsbackup)", None),
    ],
)
def test_file_chooser_filters_paths_and_cancel_preserve_domain_state(
    file_dialog_window, tmp_path, monkeypatch,
    owner_path, handler, entry_point, caption, filter_text, initial_field,
):
    window = file_dialog_window
    owner = window
    for attribute in owner_path.split("."):
        owner = getattr(owner, attribute)
    calls = []

    def cancel(*args, **kwargs):
        calls.append((args, kwargs))
        return "", ""

    monkeypatch.setattr(QFileDialog, entry_point, cancel)
    before = _form_state(window)
    files_before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    initial_path = getattr(owner, initial_field).text() if initial_field else ""
    getattr(owner, handler)()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (owner, caption, initial_path, filter_text)
    # No added options such as DontUseNativeDialog or changed format restrictions.
    assert kwargs == {}
    assert _form_state(window) == before
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == files_before
    assert window.anonymization_widget._worker is None
    assert window.restoration_widget._worker is None
    assert window.backup_widget._worker is None
    assert window.maintenance_widget._worker is None
    if owner is window.anonymization_widget:
        # Masking intentionally acknowledges cancellation without replacing input.
        assert owner.status_label.text() == "Seleção de arquivo cancelada."
