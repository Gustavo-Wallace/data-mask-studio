"""Profile error presentation must not alter CRUD, locking or user edits."""

from dataclasses import asdict, FrozenInstanceError
import multiprocessing
import os
from pathlib import Path
import pickle
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.gui.anonymization_widget import _PROFILE_ERROR_IDS
from data_mask_studio.i18n import Translator, load_catalogs
from data_mask_studio.profiles import (
    ProfileError, ProfileExplanation as Explanation, ProfileReason as Reason,
    ProfileFormatError, ProfileStorageError, ProfileValidationError,
)
from data_mask_studio.profiles.locking import profile_write_lock
from test_dialog_consistency import modal_reply
from test_masking_localization import configure, masking_pages


NAME = "Existing Ω & {name}"


CASES = (
    (Reason.NAME_LENGTH, "O nome do perfil deve ter entre 3 e 60 caracteres.",
     "The profile name must contain between 3 and 60 characters."),
    (Reason.NAME_CONTROL_CHARACTERS, "O nome do perfil não pode conter caracteres de controle.",
     "The profile name cannot contain control characters."),
    (Reason.DUPLICATE_NAME, "Já existe um perfil com esse nome.", "A profile with this name already exists."),
    (Reason.PROFILE_NOT_FOUND, "O perfil selecionado não existe mais.", "The selected profile no longer exists."),
    (Reason.WRITE_BUSY, "Os perfis estão em uso ou sendo atualizados por outra instância. Tente novamente após a operação concluir.",
     "Profiles are in use or being updated by another instance. Try again after the operation finishes."),
    (Reason.SOURCE_MISMATCH, "Configuração incompatível com a inspeção de origem.",
     "The configuration does not match the source inspection."),
)


def create_profile(page, name=NAME):
    profile = page._profile_service.create(name, page._column_configs, inspection=page._inspection_result,
        composites=page.composite_section.configurations)
    page.refresh_profiles(profile.identifier)
    return profile


def confirm_yes(app):
    box = app.activeModalWidget()
    assert isinstance(box, QMessageBox)
    box.button(QMessageBox.StandardButton.Yes).click()


def hold_profile_lock(path, channel):
    os.environ["LOCALAPPDATA"] = str(path.parent / "isolated-child")
    with profile_write_lock(path):
        channel.send("locked")
        assert channel.poll(15), "Synthetic parent did not release the profile lock"
        assert channel.recv() == "release"
    channel.close()


@pytest.mark.parametrize("case,name,expected", [
    ("length", "x", "The profile name must contain between 3 and 60 characters."),
    ("control", "control\x01name", "The profile name cannot contain control characters."),
    ("duplicate", NAME, "A profile with this name already exists."),
    ("removed", "Renamed Ω", "The selected profile no longer exists."),
])
def test_real_profile_error_uses_explicit_english_and_retranslates(masking_pages, monkeypatch, case, name, expected):
    _, page, _, _, _ = masking_pages()
    configure(page)
    service = page._profile_service
    profile = service.create(NAME, page._column_configs, inspection=page._inspection_result,
                             composites=page.composite_section.configurations)
    page.refresh_profiles(profile.identifier)
    if case == "removed":
        service.delete(profile.identifier)
    monkeypatch.setattr(page, "_ask_profile_name", lambda **kwargs: (name, True))
    page.retranslate(Translator("en"))
    (page.rename_selected_profile if case == "removed" else page.save_as_profile)()
    assert page.status_label.text() == expected
    assert page.status_label.property("feedbackState") == "error"
    page.retranslate(Translator("pt-BR"))
    assert page.status_label.text() != expected
    page.retranslate(Translator("en"))
    assert page.status_label.text() == expected


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("reason,portuguese,english", CASES)
def test_all_known_reasons_localize_and_preserve_exception_evidence(masking_pages, locale, reason, portuguese, english):
    _, page, *_ = masking_pages()
    explanation = Explanation(reason)
    error = (ProfileStorageError if reason is Reason.WRITE_BUSY else ProfileValidationError)(
        portuguese, explanation=explanation)
    page._set_profile_error(error)
    page.retranslate(Translator(locale))
    assert page.status_label.text() == (portuguese if locale == "pt-BR" else english)
    assert page.status_label.property("feedbackState") == "error"
    assert page.status_label.textFormat() == Qt.TextFormat.PlainText
    assert str(error) == portuguese and error.args == (portuguese,) and error.explanation is explanation
    with pytest.raises(FrozenInstanceError):
        explanation.reason = Reason.DUPLICATE_NAME


@pytest.mark.parametrize("kind", [ProfileError, ProfileValidationError, ProfileStorageError, ProfileFormatError])
def test_existing_exception_args_causes_and_pickling_are_compatible(kind):
    assert kind().args == () and str(kind()) == "" and kind().explanation is None
    legacy = kind("literal", "second argument")
    assert legacy.args == ("literal", "second argument") and str(legacy) == str(Exception(*legacy.args))
    cause = OSError("PRIVATE_SYNTHETIC_DETAIL")
    try:
        raise kind("safe original", explanation=Explanation(Reason.WRITE_BUSY)) from cause
    except kind as error:
        assert error.__cause__ is cause and isinstance(error, ProfileError)
        restored = pickle.loads(pickle.dumps(error))
        assert type(restored) is kind and restored.args == error.args and restored.explanation == error.explanation


@pytest.mark.parametrize("metadata", [None, Explanation("future"), Explanation("duplicate_name"), Explanation([]), "invalid metadata"])
def test_unknown_metadata_and_known_words_without_reason_stay_literal(masking_pages, metadata):
    _, page, *_ = masking_pages()
    literal = "Já existe um perfil com esse nome. <literal> & {name}"
    error = ProfileValidationError(literal, explanation=metadata)
    page._set_profile_error(error)
    for locale in ("en", "pt-BR", "en"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == literal
        assert page.status_label.property("feedbackState") == "error"


@pytest.mark.parametrize("operation", ["create", "rename"])
@pytest.mark.parametrize("name,reason,legacy", [
    ("", Reason.NAME_LENGTH, CASES[0][1]),
    (" " * 8, Reason.NAME_LENGTH, CASES[0][1]),
    ("x" * 61, Reason.NAME_LENGTH, CASES[0][1]),
    ("control\x01name", Reason.NAME_CONTROL_CHARACTERS, CASES[1][1]),
    (NAME.casefold(), Reason.DUPLICATE_NAME, CASES[2][1]),
])
def test_real_name_validation_and_conflicts_keep_document_and_original_messages(masking_pages, operation, name, reason, legacy):
    _, page, *_ = masking_pages()
    configure(page)
    original = create_profile(page)
    other = create_profile(page, "Other Ω")
    service = page._profile_service
    before = service.repository.path.read_bytes(), service.repository.path.stat().st_mtime_ns
    with pytest.raises(ProfileValidationError) as caught:
        if operation == "create":
            service.create(name, page._column_configs, inspection=page._inspection_result,
                composites=page.composite_section.configurations)
        else:
            service.rename(other.identifier, name)
    assert str(caught.value) == legacy and caught.value.explanation == Explanation(reason)
    assert (service.repository.path.read_bytes(), service.repository.path.stat().st_mtime_ns) == before
    assert [item.name for item in service.list_profiles()] == [original.name, other.name]


@pytest.mark.parametrize("operation", ["update", "rename", "delete"])
def test_removed_profile_is_resolved_against_current_document(masking_pages, operation):
    _, page, *_ = masking_pages()
    profile = create_profile(page)
    service = page._profile_service
    service.delete(profile.identifier)
    before = service.repository.path.read_bytes()
    with pytest.raises(ProfileValidationError) as caught:
        if operation == "update":
            service.update(profile.identifier, page._column_configs, inspection=page._inspection_result)
        elif operation == "rename":
            service.rename(profile.identifier, "Renamed Ω")
        else:
            service.delete(profile.identifier)
    assert str(caught.value) == CASES[3][1] and caught.value.explanation == Explanation(Reason.PROFILE_NOT_FOUND)
    assert service.repository.path.read_bytes() == before and service.list_profiles() == []


@pytest.mark.parametrize("operation", ["create", "update"])
def test_mismatched_inspection_remains_rejected_with_unchanged_evidence(masking_pages, operation):
    _, page, source, *_ = masking_pages()
    profile = create_profile(page)
    service = page._profile_service
    configurations = [ColumnConfig("Different source", True, "FIELD")]
    before = service.repository.path.read_bytes()
    with pytest.raises(ProfileValidationError) as caught:
        if operation == "create":
            service.create("Mismatch", configurations, inspection=inspect_csv(source))
        else:
            service.update(profile.identifier, configurations, inspection=inspect_csv(source))
    assert str(caught.value) == CASES[5][1] and caught.value.explanation == Explanation(Reason.SOURCE_MISMATCH)
    assert service.repository.path.read_bytes() == before


@pytest.mark.parametrize("operation", ["create", "update", "rename", "delete"])
def test_real_process_contention_is_typed_and_does_not_change_document(masking_pages, operation):
    app, page, *_ = masking_pages()
    configure(page)
    profile = create_profile(page)
    service, before = page._profile_service, page._profile_service.repository.path.read_bytes()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=hold_profile_lock, args=(service.repository.path, child))
    process.start()
    child.close()
    try:
        assert parent.poll(10) and parent.recv() == "locked"
        with pytest.raises(ProfileStorageError) as caught:
            if operation == "create":
                service.create("New profile", page._column_configs, inspection=page._inspection_result)
            elif operation == "update":
                service.update(profile.identifier, page._column_configs, inspection=page._inspection_result)
            elif operation == "rename":
                service.rename(profile.identifier, "Renamed Ω")
            else:
                service.delete(profile.identifier)
        assert str(caught.value) == CASES[4][1] and caught.value.explanation == Explanation(Reason.WRITE_BUSY)
        assert service.repository.path.read_bytes() == before
        for locale in ("pt-BR", "en"):
            page._set_profile_error(caught.value)
            page.retranslate(Translator(locale))
            assert page.status_label.text() == Translator(locale).text("profile.error.write_busy")
            assert page.profile_combo.currentData() == profile.identifier
    finally:
        parent.send("release")
        process.join(10)
        if process.is_alive():
            process.terminate()  # Only the test-owned synthetic child.
            process.join(5)
        parent.close()
    assert process.exitcode == 0
    assert service.rename(profile.identifier, "After release").name == "After release"


@pytest.mark.parametrize("handler", ["save_as_profile", "update_selected_profile", "rename_selected_profile", "delete_selected_profile", "refresh_profiles"])
def test_unknown_service_error_fallback_stays_literal_for_every_handler(masking_pages, monkeypatch, handler):
    app, page, *_ = masking_pages()
    configure(page)
    create_profile(page)
    service = page._profile_service
    before = service.repository.path.read_bytes()
    method = {"save_as_profile": "create", "update_selected_profile": "update", "rename_selected_profile": "rename",
              "delete_selected_profile": "delete", "refresh_profiles": "list_profiles"}[handler]
    literal = "Unsupported service evidence <literal> & {name}"
    def fail(*args, **kwargs):
        raise ProfileStorageError(literal)
    monkeypatch.setattr(service, method, fail)
    monkeypatch.setattr(page, "_ask_profile_name", lambda **kwargs: ("New name", True))
    if handler in ("update_selected_profile", "delete_selected_profile"):
        with modal_reply(page, lambda: confirm_yes(app)):
            getattr(page, handler)()
    else:
        getattr(page, handler)()
    for locale in ("en", "pt-BR"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == literal
    assert service.repository.path.read_bytes() == before


def test_retranslation_preserves_unsaved_edit_selection_focus_results_and_has_no_io(masking_pages, monkeypatch):
    app, page, _, repository, _ = masking_pages()
    configure(page)
    profile = create_profile(page)
    page._prefix_fields[1].setText("UNSAVED")
    page._output_name_fields[0].setText("User output Ω & {name}")
    page.profile_toggle.setChecked(True)
    page.resize(1250, 1100)
    page.show()
    app.processEvents()
    page._prefix_fields[1].setFocus()
    page._prefix_fields[1].setSelection(1, 4)
    app.processEvents()
    page._set_profile_error(ProfileValidationError(CASES[2][1], explanation=Explanation(Reason.DUPLICATE_NAME)))
    original = [asdict(item) for item in page._column_configs]
    fields = tuple(page._prefix_fields), tuple(page._output_name_fields), tuple(page._action_fields)
    selected = page.profile_combo.currentData(), page.profile_combo.currentText()
    focus = app.focusWidget()
    edit = focus.text(), focus.selectedText(), focus.cursorPosition()
    state = page._configuration_dirty, page._configuration_validated, page._last_output_path
    progress = page.progress_bar.value(), page.progress_bar.minimum(), page.progress_bar.maximum()
    workers = page._worker, page._detection_worker
    before = page._profile_service.repository.path.read_bytes(), page._profile_service.repository.path.stat().st_mtime_ns
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    changed = QSignalSpy(page.profiles_changed)
    selection = QSignalSpy(page.profile_combo.currentIndexChanged)
    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not perform profile CRUD, IO or planning")
    with monkeypatch.context() as guard:
        for name in ("list_profiles", "create", "update", "rename", "delete", "apply", "build_plan", "build_configuration_plan"):
            guard.setattr(page._profile_service, name, forbidden)
        guard.setattr(page, "_build_current_plan", forbidden)
        guard.setattr(page, "validate_current_configuration", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        for name in ("open", "stat", "iterdir", "unlink"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            page.retranslate(translator)
            app.processEvents()
            assert page.status_label.text() == translator.text("profile.error.duplicate_name")
            assert [asdict(item) for item in page._column_configs] == original
            assert (tuple(page._prefix_fields), tuple(page._output_name_fields), tuple(page._action_fields)) == fields
            assert (page.profile_combo.currentData(), page.profile_combo.currentText()) == selected == (profile.identifier, NAME)
            assert app.focusWidget() is focus and (focus.text(), focus.selectedText(), focus.cursorPosition()) == edit
            assert (page._configuration_dirty, page._configuration_validated, page._last_output_path) == state
            assert (page.progress_bar.value(), page.progress_bar.minimum(), page.progress_bar.maximum()) == progress
            assert (page._worker, page._detection_worker) == workers
    assert changed.count() == selection.count() == 0
    assert (page._profile_service.repository.path.read_bytes(), page._profile_service.repository.path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_real_incompatible_application_is_localized_and_preserves_unsaved_edits(masking_pages, locale):
    app, page, *_ = masking_pages(locale)
    configure(page)
    profile = page._profile_service.create("Unrelated Ω", [ColumnConfig("Unrelated field", True, "FIELD")])
    page.refresh_profiles(profile.identifier)
    page._prefix_fields[1].setText("UNSAVED")
    before = [asdict(item) for item in page._column_configs], page._profile_service.repository.path.read_bytes()
    with modal_reply(page, lambda: confirm_yes(app)):
        page.apply_selected_profile()
    assert page.status_label.text() == Translator(locale).text("profile.incompatible")
    assert [asdict(item) for item in page._column_configs] == before[0]
    assert page._profile_service.repository.path.read_bytes() == before[1]
    target = Translator("en" if locale == "pt-BR" else "pt-BR")
    page.retranslate(target)
    assert page.status_label.text() == target.text("profile.incompatible")
    assert [asdict(item) for item in page._column_configs] == before[0]


def test_catalog_parity_and_all_reason_ids_exist():
    catalogs = load_catalogs()
    assert set(_PROFILE_ERROR_IDS) == set(Reason)
    assert set(catalogs["en"]) == set(catalogs["pt-BR"])
    assert set(_PROFILE_ERROR_IDS.values()) <= set(catalogs["en"])


@pytest.mark.parametrize("name", [None, 123, []])
def test_unclassified_invalid_input_keeps_legacy_error_without_writes(masking_pages, name):
    _, page, *_ = masking_pages()
    create_profile(page)
    service = page._profile_service
    before = service.repository.path.read_bytes()
    with pytest.raises(ProfileValidationError) as caught:
        service.create(name, page._column_configs, inspection=page._inspection_result)
    assert str(caught.value) == "O nome do perfil é inválido." and caught.value.explanation is None
    page._set_profile_error(caught.value)
    page.retranslate(Translator("en"))
    assert page.status_label.text() == str(caught.value)
    assert service.repository.path.read_bytes() == before


def test_live_confirmation_and_existing_error_survive_retranslation_without_crud(masking_pages, monkeypatch):
    app, page, *_ = masking_pages()
    configure(page)
    profile = create_profile(page)
    page._prefix_fields[1].setText("UNSAVED")
    error = ProfileValidationError(CASES[2][1], explanation=Explanation(Reason.DUPLICATE_NAME))
    page._set_profile_error(error)
    before = [asdict(item) for item in page._column_configs], page._profile_service.repository.path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation of a live confirmation may not perform profile operations")
    for method in ("list_profiles", "create", "update", "rename", "delete", "apply"):
        monkeypatch.setattr(page._profile_service, method, forbidden)
    def reject():
        box = app.activeModalWidget()
        assert box is page._confirmation_dialog
        no = box.button(QMessageBox.StandardButton.No)
        yes = box.button(QMessageBox.StandardButton.Yes)
        for locale in ("en", "pt-BR", "en"):
            page.retranslate(Translator(locale))
            assert box is page._confirmation_dialog and box.defaultButton() is no and box.escapeButton() is no
            assert box.standardButton(yes) is QMessageBox.StandardButton.Yes
            assert box.text() == Translator(locale).text("profile.update_text", name=NAME)
            assert page.status_label.text() == Translator(locale).text("profile.error.duplicate_name")
            assert [asdict(item) for item in page._column_configs] == before[0]
            assert page.profile_combo.currentData() == profile.identifier
        no.click()
    with modal_reply(page, reject):
        answer = page._confirm("profile.update_title", "profile.update_text", name=NAME)
    assert answer == QMessageBox.StandardButton.No and page._confirmation_dialog is None
    assert page._profile_service.repository.path.read_bytes() == before[1]


def test_profile_error_retranslation_keeps_real_published_output_and_completed_result(masking_pages, monkeypatch):
    app, page, source, repository, _ = masking_pages()
    configure(page)
    create_profile(page)
    output = source.with_name("synthetic_masked.csv")
    page._start_processing(output, overwrite=False)
    worker = page._worker
    assert worker is not None and worker.wait(10000)
    app.processEvents()
    app.processEvents()
    assert page._worker is None and output.is_file()
    completed = page._completed_result
    assert completed is not None and completed.output_path == output
    before = output.read_bytes(), page._profile_service.repository.path.read_bytes(), repository.database_path.read_bytes()
    progress = page.progress_bar.value(), page.progress_bar.minimum(), page.progress_bar.maximum()
    monkeypatch.setattr(page, "_ask_profile_name", lambda **kwargs: (NAME, True))
    page.save_as_profile()
    for locale in ("en", "pt-BR", "en"):
        page.retranslate(Translator(locale))
        assert page.status_label.text() == Translator(locale).text("profile.error.duplicate_name")
        assert page.status_label.property("feedbackState") == "error"
        assert page._completed_result is completed and page._last_output_path == output
        assert page.open_folder_button.isEnabled() and str(output) in page.output_path_label.text()
        assert (page.progress_bar.value(), page.progress_bar.minimum(), page.progress_bar.maximum()) == progress
        assert (output.read_bytes(), page._profile_service.repository.path.read_bytes(), repository.database_path.read_bytes()) == before
