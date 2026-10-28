"""Explicit cross-flow presentation must preserve operational and data contracts."""

import ast
import csv
from pathlib import Path
import sqlite3
import re
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QLocale, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.i18n import Translator, load_catalogs, validate_catalogs
from data_mask_studio.i18n.catalog import placeholder_names
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.batch import BatchFileStatus
from data_mask_studio.batch_restoration import BatchRestorationStatus
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.detection import analyze_csv_columns
from data_mask_studio.gui.detection_dialog import DetectionDialog
from data_mask_studio.preferences import PreferencesService
from test_navigation_localization import build_localized_window


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


@pytest.fixture
def main(tmp_path):
    app = create_application([])
    window = build_localized_window(tmp_path)
    window.show()
    app.processEvents()
    return app, window


CLOSE_GUARDS = (
    ("anonymization_widget", "stop_worker", "masking", "Wait for processing to be cancelled before closing."),
    ("batch_widget", "stop_workers", "batch", "Wait for the batch to be cancelled before closing."),
    ("backup_widget", "stop_worker", "backup", "Wait for backup or restoration to finish safely."),
    ("restoration_widget", "stop_worker", "csv", "Wait for CSV restoration to be cancelled."),
    ("html_restoration_widget", "stop_worker", "html", "Wait for HTML restoration to be cancelled."),
    ("batch_restoration_widget", "stop_workers", "batch_restoration", "Wait for batch restoration to be cancelled."),
    ("maintenance_widget", "stop_worker", "maintenance", "Wait for maintenance to finish safely."),
)


@pytest.mark.parametrize("position", range(len(CLOSE_GUARDS)))
def test_blocked_close_uses_explicit_locale_and_preserves_guard_order(main, monkeypatch, position):
    _, window = main
    calls = []
    with monkeypatch.context() as guard:
        for index, (page, method, _, _) in enumerate(CLOSE_GUARDS):
            def stop(current=index):
                calls.append(current)
                return current != position
            guard.setattr(getattr(window, page), method, stop)
        window.retranslate(Translator("en"))
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted() and calls == list(range(position + 1))
        assert window.anonymization_widget.status_label.text() == CLOSE_GUARDS[position][3]
        assert window.anonymization_widget.status_label.property("feedbackState") == "neutral"
        window.retranslate(Translator("pt-BR"))
        assert window.anonymization_widget.status_label.text() == Translator("pt-BR").text(
            "shell.close." + CLOSE_GUARDS[position][2])


def test_environment_restored_feedback_is_static_localized_and_retranslatable(main):
    _, window = main
    window.retranslate(Translator("en"))
    window._environment_restored()  # GUI notification only; never restore a backup.
    assert window.anonymization_widget.status_label.text() == "Local environment restored successfully."
    window.retranslate(Translator("pt-BR"))
    assert window.anonymization_widget.status_label.text() == "Ambiente local restaurado com sucesso."


def labels(window):
    return (
        (window.anonymization_widget.select_button, "masking.select_csv"),
        (window.batch_widget.start_button, "batch.start"),
        (window.file_restoration_widget.select_button, "restoration.file.select"),
        (window.batch_restoration_widget.start_button, "batch_restoration.start"),
        (window.consultant_widget.consult_button, "consultant.consult"),
        (window.backup_widget.create_button, "backup.create"),
        (window.maintenance_widget.refresh_button, "maintenance.overview.refresh"),
        (window.settings_widget.save_button, "settings.save"),
        (window.restoration_widget.generate_button, "restoration.csv.generate"),
        (window.html_restoration_widget.generate_button, "restoration.html.generate"),
        (window.maintenance_widget.validate_backup_button, "backup.validate"),
        (window.maintenance_widget.locate_button, "maintenance.temporary.locate"),
        (window.maintenance_widget.compact_button, "maintenance.compaction.start"),
    )


def worker_references(window):
    return tuple(getattr(page, attribute) for page, attribute in (
        (window.anonymization_widget, "_worker"), (window.anonymization_widget, "_detection_worker"),
        (window.batch_widget, "_validation_worker"), (window.batch_widget, "_processing_worker"),
        (window.restoration_widget, "_worker"), (window.html_restoration_widget, "_worker"),
        (window.batch_restoration_widget, "_analysis_worker"),
        (window.batch_restoration_widget, "_processing_worker"),
        (window.backup_widget, "_worker"), (window.maintenance_widget, "_worker")))


@pytest.mark.parametrize("page_index", range(8))
def test_all_pages_retranslate_in_place_with_live_inputs_semantic_choices_and_focus(main, tmp_path, monkeypatch, page_index):
    app, window = main
    source = tmp_path / "Nome & literal.csv"
    source.write_text("Nome,Note\nAlice,Synthetic\nBob,Sample\n", encoding="utf-8")
    mask = window.anonymization_widget
    mask.load_csv(str(source))
    mask._action_fields[0].setCurrentIndex(mask._action_fields[0].findData(ColumnAction.MASK.value))
    mask._prefix_fields[0].setText("NAME")
    mask._output_name_fields[0].setText("Nome & <literal>")
    mask.profile_toggle.setChecked(True)
    window.batch_widget.add_paths([source])
    window.batch_widget.output_field.setText(str(tmp_path / "batch-output"))
    assert window.file_restoration_widget.load_file(source)
    restoration = window.restoration_widget
    restoration._checkboxes[0].setChecked(True)
    restoration.source_combo.setCurrentIndex(restoration.source_combo.findData("package"))
    restoration.package_controls.path.setText(str(tmp_path / "synthetic.dmspackage"))
    restoration.package_controls.password.setText("synthetic password")
    restoration.representation_combo.setCurrentIndex(restoration.representation_combo.findData("canonical"))
    window.html_restoration_widget.representation_combo.setCurrentIndex(
        window.html_restoration_widget.representation_combo.findData("canonical"))
    window.batch_restoration_widget.add_paths([source])
    window.batch_restoration_widget.output_field.setText(str(tmp_path / "restore-output"))
    window.consultant_widget.codes_input.setPlainText("NAME-ABCDEFGHIJKL\n<literal> & texto")
    window.backup_widget.destination_field.setText(str(tmp_path / "synthetic.dmsbackup"))
    window.backup_widget.create_password_field.setText("synthetic backup password")
    window.backup_widget.confirm_password_field.setText("synthetic backup password")
    window.maintenance_widget.backup_password_field.setText("synthetic validation password")
    window.set_current_page(page_index)
    app.processEvents()
    focus = (mask._prefix_fields[0], window.batch_widget.output_field, restoration.package_controls.password,
        window.batch_restoration_widget.output_field, window.consultant_widget.codes_input,
        window.backup_widget.create_password_field, window.maintenance_widget.refresh_button,
        window.settings_widget.language_combo)[page_index]
    focus.setFocus()
    app.processEvents()
    focus_before = app.focusWidget()
    assert focus_before is focus
    fields = window.findChildren(QLineEdit)
    combos = window.findChildren(QComboBox)
    values = tuple((field.text(), field.echoMode()) for field in fields)
    choices = tuple(combo.currentData() for combo in combos)
    identity = window.page_widgets, tuple(window.page_shells), tuple(control for control, _ in labels(window))
    models = mask._inspection_result, tuple(mask._column_configs), tuple(window.batch_widget.files), restoration._inspection
    workers = worker_references(window)
    selected = tuple(box.isChecked() for box in restoration._checkboxes)
    spans = [(shell.scroll_area.horizontalScrollBar().value(), shell.scroll_area.verticalScrollBar().value())
             for shell in window.page_shells]
    spies = [QSignalSpy(window.navigation.current_changed), QSignalSpy(mask.profiles_changed),
        QSignalSpy(mask.environment_may_change), QSignalSpy(window.backup_widget.environment_restored)]
    spies.extend(QSignalSpy(combo.currentIndexChanged) for combo in combos)
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]

    def forbidden(*args, **kwargs):
        pytest.fail("Cross-page retranslation must not read environment/files, open dialogs or start work")

    with monkeypatch.context() as guard:
        guard.setattr(sqlite3, "connect", forbidden)
        guard.setattr(window._profile_service, "list_profiles", forbidden)
        for name in ("open", "stat", "iterdir", "unlink", "rglob"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            window.retranslate(translator)
            app.processEvents()
            assert all(control.text() == translator.text(identifier) for control, identifier in labels(window))
            assert all(page._translator is translator for page in window.page_widgets)
            assert window.restoration_widget._translator is window.html_restoration_widget._translator is translator
            assert (window.page_widgets, tuple(window.page_shells), tuple(control for control, _ in labels(window))) == identity
            assert tuple((field.text(), field.echoMode()) for field in fields) == values
            assert tuple(combo.currentData() for combo in combos) == choices
            assert models == (mask._inspection_result, tuple(mask._column_configs), tuple(window.batch_widget.files), restoration._inspection)
            assert tuple(box.isChecked() for box in restoration._checkboxes) == selected
            assert worker_references(window) == workers
            assert window.current_page_index() == page_index and window.navigation.buttons[page_index].isChecked()
            assert app.focusWidget() is focus_before
            # Scroll positions are preserved subject to the translated layout's available range.
            for shell, (horizontal, vertical) in zip(window.page_shells, spans, strict=True):
                assert shell.scroll_area.horizontalScrollBar().value() == min(horizontal, shell.scroll_area.horizontalScrollBar().maximum())
                assert shell.scroll_area.verticalScrollBar().value() == min(vertical, shell.scroll_area.verticalScrollBar().maximum())
    assert all(spy.count() == 0 for spy in spies)


@pytest.mark.parametrize("preference", ["system", "en", "pt-BR"])
def test_saved_language_never_activates_globally_at_normal_startup(tmp_path, preference):
    app = create_application([])
    service = PreferencesService(tmp_path / "preferences.json",
        ui_languages_provider=lambda: pytest.fail("No automatic locale lookup"))
    assert service.save_language(preference).error is None
    before = service.path.read_bytes(), service.path.stat().st_mtime_ns
    locale = QLocale().name()
    window = build_localized_window(tmp_path, preferences_service=service)
    assert all(control.text() == Translator("pt-BR").text(identifier) for control, identifier in labels(window))
    assert window._translator.locale == "pt-BR" and window.current_page_index() == 0
    assert QLocale().name() == locale and QCoreApplication.translate("QPlatformTheme", "&Yes") == "&Sim"
    assert (service.path.read_bytes(), service.path.stat().st_mtime_ns) == before


def test_all_literal_gui_ids_plural_forms_placeholders_and_supported_locales_are_valid():
    catalogs = load_catalogs()
    validate_catalogs(catalogs)
    assert set(catalogs) == {"pt-BR", "en"} and set(catalogs["en"]) == set(catalogs["pt-BR"])
    prefixes = {identifier.split(".")[0] for identifier in catalogs["en"]}
    checked = set()
    for path in (Path(__file__).resolve().parents[1] / "src/data_mask_studio/gui").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            value = node.value
            if (value.split(".")[0] not in prefixes or "." not in value
                    or value.endswith(".") or not all(character.isascii() and (character.isalnum() or character in "._") for character in value)):
                continue
            assert value in catalogs["en"] or all(f"{value}.{form}" in catalogs["en"] for form in ("one", "other")), (path.name, node.lineno, value)
            checked.add(value)
    assert len(checked) > 400
    for identifier, template in catalogs["en"].items():
        assert placeholder_names(template) == placeholder_names(catalogs["pt-BR"][identifier])
        if identifier.endswith(".one"):
            base = identifier.removesuffix(".one")
            assert base + ".other" in catalogs["en"]
            parameters = {name: "<literal> & {x}" for name in placeholder_names(template) - {"count"}}
            for locale in ("en", "pt-BR"):
                for count in (0, 1, 2):
                    assert Translator(locale).plural(base, count, **parameters)


def test_cross_page_retranslation_keeps_real_active_worker_and_navigation_lock(main, monkeypatch):
    app, window = main
    widget = window.maintenance_widget
    window.set_current_page(window.page_index(widget))
    entered, release = Event(), Event()
    run = widget._diagnostics.run
    calls = []

    def paused(**kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return run(**kwargs)

    monkeypatch.setattr(widget._diagnostics, "run", paused)
    widget.start_diagnostic()
    worker = widget._worker
    try:
        assert entered.wait(5) and worker.isRunning()
        worker_ids = worker_references(window)
        enabled = tuple(window.navigation.page_enabled(index) for index in range(8))
        widget.cancel_button.setFocus()
        app.processEvents()
        for locale in ("en", "pt-BR", "en"):
            window.retranslate(Translator(locale))
            assert widget._worker is worker and worker.isRunning() and worker_references(window) == worker_ids
            assert tuple(window.navigation.page_enabled(index) for index in range(8)) == enabled
            assert app.focusWidget() is widget.cancel_button
            assert all(control.text() == Translator(locale).text(identifier) for control, identifier in labels(window))
        widget.cancel()
        window.retranslate(Translator("pt-BR"))
        assert worker.cancellation_requested and not widget.cancel_button.isEnabled()
    finally:
        release.set()
        assert worker.wait(10000)
        app.processEvents()
    assert calls == [True] and widget._worker is None and widget._last_diagnostic is None
    assert widget.progress.value() == 0 and not widget.copy_report_button.isEnabled()


def test_main_window_mask_then_local_restore_has_identical_outputs_and_decisions_in_both_locales(main, tmp_path):
    app, window = main
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome,Note\nAlice,Synthetic\nBob,Sample\n", encoding="utf-8")
    source_before = source.read_bytes()
    masked, restored = [], []
    for locale in ("pt-BR", "en"):
        window.retranslate(Translator(locale))
        mask = window.anonymization_widget
        mask.load_csv(str(source))
        mask._action_fields[0].setCurrentIndex(mask._action_fields[0].findData(ColumnAction.MASK.value))
        mask._prefix_fields[0].setText("NAME")
        mask.validate_current_configuration()
        assert mask._configuration_validated and mask._build_current_plan().requires_masking
        destination = tmp_path / f"masked-{locale}.csv"
        mask._start_processing(destination, overwrite=False)
        worker = mask._worker
        assert worker is not None and worker.wait(15000)
        app.processEvents()
        assert mask._worker is None and mask._last_output_path == destination
        masked.append(destination.read_bytes())
        assert window.file_restoration_widget.load_file(destination)
        restoration = window.restoration_widget
        restoration._checkboxes[0].setChecked(True)
        output = tmp_path / f"restored-{locale}.csv"
        restoration.start_restoration(output, overwrite=False)
        worker = restoration._worker
        assert worker is not None and worker.wait(15000)
        app.processEvents()
        assert restoration._worker is None and restoration._last_output_path == output
        assert restoration.status_label.property("feedbackState") == "success"
        restored.append(output.read_bytes())
        with output.open(encoding="utf-8-sig", newline="") as stream:
            assert list(csv.reader(stream)) == [["Nome", "Note"], ["Alice", "Synthetic"], ["Bob", "Sample"]]
    assert masked[0] == masked[1] and restored[0] == restored[1] and source.read_bytes() == source_before
    assert window._vault_repository_factory().count() == 2


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("owner_path,handler,entry_point,caption_id,filter_id,patterns,initial", [
    ("anonymization_widget", "_select_csv", "getOpenFileName", "masking.file.select_title", "masking.file.filter", ("*.csv",), None),
    ("file_restoration_widget", "_select_file", "getOpenFileName", "restoration.file.select", "restoration.file.filter",
        ("*.csv *.html *.htm", "*.csv", "*.html *.htm"), None),
    ("restoration_widget.package_controls", "_browse", "getOpenFileName", "restoration.package.select_title", "restoration.package.filter", ("*.dmspackage",), None),
    ("anonymization_widget.transfer_controls", "_browse", "getSaveFileName", "masking.package.save_title", "masking.package.file_filter", ("*.dmspackage",), "destination"),
    ("backup_widget", "choose_restore_file", "getOpenFileName", "backup.select", "backup.file_filter", ("*.dmsbackup",), None),
    ("backup_widget", "choose_destination", "getSaveFileName", "backup.dialog.save", "backup.file_filter", ("*.dmsbackup",), "destination_field"),
    ("maintenance_widget", "choose_backup", "getOpenFileName", "backup.select", None, ("*.dmsbackup",), None),
])
def test_cross_flow_file_dialog_captions_filters_and_cancel_are_locale_explicit(main, monkeypatch,
    locale, owner_path, handler, entry_point, caption_id, filter_id, patterns, initial):
    _, window = main
    translator = Translator(locale)
    window.retranslate(translator)
    owner = window
    for part in owner_path.split("."):
        owner = getattr(owner, part)
    observed = []
    def cancel(*args, **kwargs):
        observed.append((args, kwargs))
        return "", ""
    monkeypatch.setattr(QFileDialog, entry_point, cancel)
    fields = window.findChildren(QLineEdit)
    before = [(field.text(), field.echoMode()) for field in fields]
    workers = worker_references(window)
    initial_path = getattr(owner, initial).text() if initial else ""
    getattr(owner, handler)()
    assert len(observed) == 1
    args, kwargs = observed[0]
    assert args[:3] == (owner, translator.text(caption_id), initial_path) and not kwargs
    assert args[3] == (translator.text(filter_id) if filter_id else "Backup (*.dmsbackup)")
    assert tuple(re.findall(r"\(([^)]+)\)", args[3])) == patterns
    assert [(field.text(), field.echoMode()) for field in fields] == before and worker_references(window) == workers


def test_freeform_batch_reasons_and_detection_evidence_are_not_heuristically_translated(main, tmp_path):
    _, window = main
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome,CPF\nAlice,123.456.789-00\nBob,987.654.321-00\n", encoding="utf-8")
    batch, restore = window.batch_widget, window.batch_restoration_widget
    batch.add_paths([source])
    restore.add_paths([source])
    mask_item, restore_item = batch.files[0], restore.files[0]
    mask_item.status = BatchFileStatus.INCOMPATIBLE
    restore_item.status = BatchRestorationStatus.REVIEW_REQUIRED
    mask_item.result_message = "Coluna desconhecida: Nome & <literal>. {count}"
    restore_item.result_message = "Há códigos ausentes que exigem revisão da política."
    batch._refresh_table()
    restore._refresh_file_table()
    inspection = inspect_csv(source)
    detection = analyze_csv_columns(inspection)
    assert detection.suggestions and all(item.reason for item in detection.suggestions)
    mask = window.anonymization_widget
    dialog = DetectionDialog(detection.suggestions, detection.rows_analyzed, mask)
    mask._detection_dialog = dialog
    reasons = tuple(item.reason for item in detection.suggestions)
    for locale in ("en", "pt-BR", "en"):
        window.retranslate(Translator(locale))
        assert batch.files[0] is mask_item and restore.files[0] is restore_item
        assert batch.file_table.item(0, 4).text() == mask_item.result_message
        assert restore.file_table.item(0, 7).text() == restore_item.result_message
        assert mask_item.status is BatchFileStatus.INCOMPATIBLE and restore_item.status is BatchRestorationStatus.REVIEW_REQUIRED
        assert tuple(dialog.table.item(row, 7).text() for row in range(len(reasons))) == reasons
        assert dialog._suggestions is detection.suggestions and mask._detection_dialog is dialog
