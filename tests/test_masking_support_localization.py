"""Secondary masking dialogs: explicit labels, native roles and unchanged data."""

from dataclasses import replace
import json

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.detection import (
    ColumnSuggestion, ConfidenceLevel, DetectionResult, SuggestedType, analyze_csv_columns,
)
from data_mask_studio.gui.composite_dialog import CompositeDialog
from data_mask_studio.gui.detection_dialog import DetectionDialog
from data_mask_studio.gui.profile_controls import ProfileNameDialog
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NORMALIZATION_OPTIONS, NormalizationRule
from data_mask_studio.processing.models import CompositeSource
from data_mask_studio.processing.planner import PlanningError
from data_mask_studio.profiles import ProfileValidationError
from test_dialog_consistency import modal_reply
from test_masking_localization import configure, masking_pages


NAME = 'Perfil Ω <b>literal</b> & configuration'


def reply_yes(app):
    box = app.activeModalWidget()
    assert isinstance(box, QMessageBox)
    box.button(QMessageBox.StandardButton.Yes).click()


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_profile_crud_uses_native_localized_dialogs_and_preserves_user_values(masking_pages, locale):
    app, page, _, _, translator = masking_pages(locale)
    configure(page)
    observed = []

    def enter_name():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, ProfileNameDialog)
        assert dialog.windowTitle() == translator.text("profile.save_title")
        assert dialog.cancelButtonText() == translator.text("dialog.button.cancel")
        dialog.setTextValue(NAME)
        observed.append(dialog.textValue())
        QTest.keyClick(dialog, Qt.Key.Key_Return)

    with modal_reply(page, enter_name):
        page.save_as_profile()
    service = page._profile_service
    original = service.list_profiles()[0]
    assert original.name == NAME and page.profile_combo.currentText() == NAME
    assert page.status_label.text() == translator.text("profile.saved", name=NAME)
    assert page.status_label.textFormat() == Qt.TextFormat.PlainText
    assert observed == [NAME]

    page._prefix_fields[1].setText("EDITED")
    page.validate_current_configuration()
    with modal_reply(page, lambda: reply_yes(app)):
        page.update_selected_profile()
    assert service.list_profiles()[0].columns[1].prefix == "EDITED"
    assert page.status_label.text() == translator.text("profile.updated", name=NAME)

    def rename():
        dialog = app.activeModalWidget()
        assert dialog.windowTitle() == translator.text("profile.rename_title")
        assert dialog.textValue() == NAME
        dialog.setTextValue(NAME + " renamed")
        dialog.accept()

    with modal_reply(page, rename):
        page.rename_selected_profile()
    assert service.list_profiles()[0].name == NAME + " renamed"
    page._prefix_fields[1].setText("MANUAL")
    with modal_reply(page, lambda: reply_yes(app)):
        page.apply_selected_profile()
    assert page._prefix_fields[1].text() == "EDITED"
    assert page._configuration_validated and page.generate_button.isEnabled()
    assert page.status_label.text() == translator.text("profile.applied", name=NAME + " renamed")

    before = service.repository.path.read_bytes(), service.repository.path.stat().st_mtime_ns
    page.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert (service.repository.path.read_bytes(), service.repository.path.stat().st_mtime_ns) == before
    assert page.profile_combo.currentText() == NAME + " renamed"
    with modal_reply(page, lambda: reply_yes(app)):
        page.delete_selected_profile()
    assert service.list_profiles() == [] and page.profile_combo.count() == 0
    assert page.status_label.property("feedbackState") == "success"


def test_profiles_persist_equivalent_configuration_in_both_languages(masking_pages, monkeypatch):
    persisted = []
    for locale in ("pt-BR", "en"):
        _, page, _, _, _ = masking_pages(locale)
        configure(page)
        monkeypatch.setattr(page, "_ask_profile_name", lambda **kwargs: (NAME, True))
        page.save_as_profile()
        document = json.loads(page._profile_service.repository.path.read_text(encoding="utf-8"))
        profile = document["profiles"][0]
        # Real services own random identifiers/timestamps; configuration must match.
        persisted.append({key: value for key, value in profile.items()
                          if key not in {"identifier", "created_at", "modified_at"}})
    assert persisted[0] == persisted[1]
    assert persisted[0]["name"] == NAME


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("name", ["x", "control\ncharacter", NAME])
def test_profile_service_validation_stays_authoritative_and_does_not_write(masking_pages, monkeypatch, locale, name):
    _, page, _, _, _ = masking_pages(locale)
    configure(page)
    service = page._profile_service
    service.create(NAME, page._column_configs, inspection=page._inspection_result,
                   composites=page.composite_section.configurations)
    before = service.repository.path.read_bytes()
    with pytest.raises(ProfileValidationError) as expected:
        service.create(name, page._column_configs, inspection=page._inspection_result,
                       composites=page.composite_section.configurations)
    monkeypatch.setattr(page, "_ask_profile_name", lambda **kwargs: (name, True))
    page.save_as_profile()
    assert page.status_label.text() == str(expected.value)
    assert page.status_label.property("feedbackState") == "error"
    assert service.repository.path.read_bytes() == before
    page.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert page.status_label.text() == str(expected.value)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape, "close"])
def test_localized_questions_keep_safe_roles_and_live_retranslation(masking_pages, locale, key):
    app, page, _, _, translator = masking_pages(locale)

    def reject():
        box = app.activeModalWidget()
        assert box is page._confirmation_dialog
        no = box.button(QMessageBox.StandardButton.No)
        yes = box.button(QMessageBox.StandardButton.Yes)
        assert box.defaultButton() is no and box.escapeButton() is no
        assert box.buttonRole(yes) == QMessageBox.ButtonRole.YesRole
        assert box.buttonRole(no) == QMessageBox.ButtonRole.NoRole
        assert yes.text() == translator.text("dialog.button.yes")
        assert box.text() == translator.text("profile.update_text", name=NAME)
        assert box.textFormat() == Qt.TextFormat.PlainText
        page.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
        assert box is page._confirmation_dialog and box.defaultButton() is no and box.escapeButton() is no
        assert box.text() == page._translator.text("profile.update_text", name=NAME)
        assert QCoreApplication.translate("QPlatformTheme", "&No") == "&Não"
        if key == "close":
            box.close()
        else:
            QTest.keyClick(box, key)

    with modal_reply(page, reject):
        answer = page._confirm("profile.update_title", "profile.update_text", name=NAME)
    assert answer == QMessageBox.StandardButton.No
    assert page._confirmation_dialog is None


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_profile_name_dialog_preserves_unsaved_input_focus_and_cancel(masking_pages, locale):
    app, page, _, _, _ = masking_pages(locale)

    def cancel():
        dialog = app.activeModalWidget()
        assert dialog is page._profile_name_dialog
        field = dialog.findChild(QLineEdit)
        field.setText(NAME)
        field.setFocus()
        app.processEvents()
        assert field.hasFocus()
        field.setSelection(2, 5)
        before = field.text(), field.selectedText(), field.cursorPosition()
        page.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
        assert (field.text(), field.selectedText(), field.cursorPosition()) == before
        assert field.hasFocus()
        assert dialog.labelText() == page._translator.text("profile.new_name")
        QTest.keyClick(dialog, Qt.Key.Key_Escape)

    with modal_reply(page, cancel):
        text, accepted = page._ask_profile_name(rename=True, text="Original user value")
    assert not accepted and text == NAME
    assert page._profile_name_dialog is None and page._profile_service.list_profiles() == []


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_profile_delete_cancel_keeps_real_document(masking_pages, locale, key):
    app, page, _, _, translator = masking_pages(locale)
    profile = page._profile_service.create(NAME, page._column_configs, inspection=page._inspection_result)
    page.refresh_profiles(profile.identifier)
    before = page._profile_service.repository.path.read_bytes()

    def cancel():
        box = app.activeModalWidget()
        assert box.windowTitle() == translator.text("profile.delete_title")
        assert box.text() == translator.text("profile.delete_text", name=NAME)
        no = box.button(QMessageBox.StandardButton.No)
        assert box.defaultButton() is no and box.escapeButton() is no
        assert box.button(QMessageBox.StandardButton.Yes).text() == translator.text("dialog.button.yes")
        QTest.keyClick(box, key)

    with modal_reply(page, cancel):
        page.delete_selected_profile()
    assert page._profile_service.repository.path.read_bytes() == before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_composite_editor_relabels_existing_rows_without_validation_or_data_changes(masking_pages, locale):
    app, page, _, _, translator = masking_pages(locale)
    configure(page)
    original = page.composite_section.configurations[0]
    validations = []
    dialog = CompositeDialog(page._inspection_result, validations.append, original, page, translator=translator)
    dialog.name_field.setText("  User Header  Ω  ")
    dialog.move_component(dialog.rows[0], 1)
    dialog.add_component(CompositeSource(SourceColumnRef("NOTES"), NormalizationRule.COLLAPSE_WHITESPACE))
    dialog.show()
    app.processEvents()
    field = dialog.name_field
    field.setFocus()
    field.setSelection(2, 4)
    before = (tuple(dialog.rows), tuple(row.component() for row in dialog.rows),
              field.text(), field.selectedText(), field.cursorPosition(), dialog._action, dialog._prefix)
    signals = []
    for row in dialog.rows:
        row.source.currentIndexChanged.connect(signals.append)
        row.normalization.currentIndexChanged.connect(signals.append)
    for target in ("en", "pt-BR", "en"):
        dialog.retranslate(Translator(target))
        app.processEvents()
        assert (tuple(dialog.rows), tuple(row.component() for row in dialog.rows),
                field.text(), field.selectedText(), field.cursorPosition(), dialog._action, dialog._prefix) == before
        assert field.hasFocus() and dialog.result_config is None
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Save).text() == dialog._translator.text("composite.editor.save")
        for row in dialog.rows:
            assert [row.normalization.itemData(i) for i in range(row.normalization.count())] == [
                rule for rule, _ in NORMALIZATION_OPTIONS
            ]
    assert not signals and not validations
    dialog.accept()
    assert dialog.result_config == replace(original, output_name="User Header  Ω", components=before[1])
    assert validations == [dialog.result_config]
    assert page.composite_section.configurations == (original,)


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_composite_editor_unavailable_sources_and_errors_remain_typed_and_safe(masking_pages, locale):
    _, page, _, _, translator = masking_pages(locale)
    configure(page)
    original = page.composite_section.configurations[0]
    missing = SourceColumnRef("MISSING <b>source</b>")
    candidate = replace(original, components=(CompositeSource(missing), original.components[1]))

    def invalid(_):
        raise PlanningError("Synthetic safe planner error <b>literal</b>.")

    dialog = CompositeDialog(page._inspection_result, invalid, candidate, page, translator=translator)
    assert dialog.rows[0].source.currentData() == missing
    assert dialog.rows[0].source.currentText() == translator.text("composite.editor.unavailable_source", source=missing.header)
    dialog.accept()
    assert dialog.result_config is None and dialog.error_label.textFormat() == Qt.TextFormat.PlainText
    text = dialog.error_label.text()
    dialog.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert dialog.error_label.text() == text and dialog.rows[0].source.currentData() == missing


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_composite_editor_creation_path_inherits_translator_and_cancel_is_non_mutating(masking_pages, locale):
    app, page, _, _, translator = masking_pages(locale)
    configure(page)
    before = page.composite_section.configurations

    def cancel():
        dialog = app.activeModalWidget()
        assert dialog is page.composite_section._editor_dialog
        assert dialog._translator is translator
        dialog.name_field.setText("Unsaved user header")
        row = dialog.rows[0]
        page.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
        assert dialog.rows[0] is row and dialog.name_field.text() == "Unsaved user header"
        QTest.keyClick(dialog, Qt.Key.Key_Escape)

    with modal_reply(page, cancel):
        page.composite_section.edit(0)
    assert page.composite_section.configurations == before
    assert page.composite_section._editor_dialog is None


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_detection_typed_labels_and_confidence_preserve_all_outputs(masking_pages, locale):
    app, page, _, _, translator = masking_pages(locale)
    rules = tuple(rule for rule, _ in NORMALIZATION_OPTIONS)
    confidences = tuple(ConfidenceLevel)
    suggestions = tuple(ColumnSuggestion(
        f"HEADER {kind.value}", kind, index % 2 == 0, f"P{index}", rules[index % len(rules)],
        confidences[index % len(confidences)], "Justificativa livre do serviço.", 10, 7,
    ) for index, kind in enumerate(SuggestedType))
    dialog = DetectionDialog(suggestions, 10, page, translator=translator)
    dialog.show()
    app.processEvents()
    dialog._acceptance_fields[0].setChecked(True)
    accepted = tuple(dialog._acceptance_fields)
    items = tuple(dialog.table.item(row, 2) for row in range(len(suggestions)))
    for target in (locale, "en", "pt-BR"):
        translator = Translator(target)
        dialog.retranslate(translator)
        assert dialog._suggestions is suggestions and tuple(dialog._acceptance_fields) == accepted
        assert dialog._acceptance_fields[0].isChecked()
        assert tuple(dialog.table.item(row, 2) for row in range(len(suggestions))) == items
        for row, suggestion in enumerate(suggestions):
            assert dialog.table.item(row, 1).text() == suggestion.header
            assert dialog.table.item(row, 2).text() == translator.text(f"detection.type.{suggestion.suggested_type.value}")
            assert dialog.table.item(row, 4).text() == suggestion.prefix
            assert dialog.table.item(row, 5).text() == translator.text(f"masking.normalization.{suggestion.normalization_rule.value}")
            assert dialog.table.item(row, 6).text() == translator.text(f"detection.confidence.{suggestion.confidence.value}")
            assert dialog.table.item(row, 7).text() == suggestion.reason
            assert dialog.table.cellWidget(row, 8).isEnabled() == (suggestion.suggested_type is not SuggestedType.UNKNOWN)
    emitted = []
    dialog.bulk_requested.connect(emitted.append)
    dialog.apply_high_button.click()
    assert emitted == [tuple(i for i, suggestion in enumerate(suggestions)
                             if suggestion.confidence is ConfidenceLevel.HIGH and suggestion.suggested_type is not SuggestedType.UNKNOWN)]


def test_real_detection_application_is_equivalent_in_both_languages(masking_pages, tmp_path):
    configurations = []
    for locale in ("pt-BR", "en"):
        app, page, _, _, translator = masking_pages(locale)
        source = tmp_path / f"detection-{locale}.csv"
        source.write_text("cpf,email,observacao\n123.456.789-00,synthetic@example.com,Nota sintética\n", encoding="utf-8")
        page.load_csv(str(source))
        result = analyze_csv_columns(page._inspection_result)
        page._detection_completed(result)
        dialog = page._detection_dialog
        assert dialog._translator is translator and page._suggestions == result.suggestions
        before = tuple(replace(column) for column in page._column_configs)
        dialog.close()
        assert tuple(page._column_configs) == before
        dialog.show()
        with modal_reply(page, lambda: reply_yes(app)):
            dialog.apply_high_button.click()
        assert page._column_configs[0].prefix == "CPF" and page._column_configs[1].prefix == "EMAIL"
        configurations.append(tuple(replace(column) for column in page._column_configs))
        old = dialog
        page.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
        assert page._detection_dialog is old and page._suggestions == result.suggestions
        dialog.clear_button.click()
        assert page._suggestions == () and tuple(page._column_configs) == configurations[-1]
    assert configurations[0] == configurations[1]


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_rejected_detection_confirmations_preserve_manual_configuration(masking_pages, tmp_path, locale):
    app, page, _, _, translator = masking_pages(locale)
    source = tmp_path / "sensitive.csv"
    source.write_text("cpf,email\n123.456.789-00,synthetic@example.com\n", encoding="utf-8")
    page.load_csv(str(source))
    page.select_all_columns()
    page._prefix_fields[0].setText("MANUAL")
    result = analyze_csv_columns(page._inspection_result)
    page._detection_completed(result)
    before = tuple(replace(column) for column in page._column_configs)
    observed = []

    def refuse():
        box = app.activeModalWidget()
        observed.append(box.windowTitle())
        QTest.keyClick(box, Qt.Key.Key_Escape)

    with modal_reply(page, refuse):
        page._detection_dialog.apply_high_button.click()
    with modal_reply(page, refuse):
        page.apply_detection_suggestion(0)
    assert observed == [translator.text("detection.apply_title"), translator.text("detection.replace_title")]
    assert tuple(page._column_configs) == before
    assert page._suggestions == result.suggestions
    assert 0 in page._manually_changed_rows


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_detection_empty_and_unavailable_suggestions_do_not_emit(masking_pages, locale):
    _, page, _, _, translator = masking_pages(locale)
    for suggestions in ((), (ColumnSuggestion("UNKNOWN", SuggestedType.UNKNOWN, False, "", NormalizationRule.EXACT,
                                               ConfidenceLevel.UNDEFINED, "Sem evidências agregadas.", 0, 0),)):
        dialog = DetectionDialog(suggestions, 0, page, translator=translator)
        emitted = []
        dialog.bulk_requested.connect(emitted.append)
        dialog.individual_requested.connect(emitted.append)
        dialog.apply_high_button.click()
        dialog.apply_accepted_button.click()
        if suggestions:
            assert not dialog._acceptance_fields[0].isEnabled()
            assert not dialog.table.cellWidget(0, 8).isEnabled()
        else:
            assert dialog.table.empty_text == translator.text("detection.empty")
        assert not emitted
