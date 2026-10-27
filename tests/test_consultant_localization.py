"""Presentation-only localization over real, synthetic encrypted vault records."""

from dataclasses import replace
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.app import create_application
from data_mask_studio.composite_text import composite_text
from data_mask_studio.consultant import ConsultationResult, ConsultationStatus
from data_mask_studio.gui.consultant_widget import ConsultantWidget, _render_result
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from data_mask_studio.vault.composite_models import CompositeMapping, CompositeMappingCandidate
from test_navigation_localization import build_localized_window


ORIGINAL = 'João  & <b>Search</b> "Ação" {count} 😀'
CANONICAL = 'João & <b>Search</b> "Ação" {count} 😀'
HEADER = '<img src=x onerror="alert(1)"> Cabeçalho & Search'
LONG_VALUE = 'Long value — ação & <tag> 😀 ' * 50
MISSING = 'NOME-AAAAAAAAAAAA'


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def seed_vault(root):
    repository = VaultRepository(root / "vault.db", VaultCipher(b"L" * 32))
    generator = TokenGenerator(b"H" * 32)
    scalar = generator.generate("NOME", CANONICAL)
    literal = generator.generate("TEXT", "consultant.consult")
    long = generator.generate("LONG", LONG_VALUE)
    with repository.transaction() as transaction:
        transaction.upsert_batch([
            MappingCandidate(scalar, "NOME", ORIGINAL, HEADER, 2, CANONICAL,
                             Rule.COLLAPSE_WHITESPACE),
            MappingCandidate(literal, "TEXT", "consultant.consult", "Search", 4),
            MappingCandidate(long, "LONG", LONG_VALUE, "Descrição longa", 1),
        ])
    with repository.transaction() as transaction:
        transaction.upsert_batch([
            MappingCandidate(scalar, "NOME", CANONICAL, HEADER, 3, CANONICAL,
                             Rule.COLLAPSE_WHITESPACE),
        ])
    original = (' São José & <b>Search</b> ', 'English-looking Search 😀')
    canonical = (original[0].strip(), original[1])
    composite = CompositeMappingCandidate(
        generate_composite_token(b"H" * 32, "CORR", canonical), "CORR", canonical,
        original, (Rule.COLLAPSE_WHITESPACE, Rule.EXACT), 2,
    )
    composites = repository.composite_repository()
    composites.upsert_composite_mapping(composite)
    composites.upsert_composite_mapping(replace(composite, original_values=canonical, occurrences=3))
    unique = replace(composite, code=generate_composite_token(b"H" * 32, "UNIQ", canonical),
                     prefix="UNIQ")
    composites.upsert_composite_mapping(unique)
    return repository, (scalar, literal, long, composite.code, unique.code)


@pytest.fixture
def vault(tmp_path):
    return seed_vault(tmp_path)


def show_widget(repository, locale):
    app = create_application([])
    widget = ConsultantWidget(lambda: repository.as_read_only(), translator=Translator(locale))
    widget.resize(1000, 700)
    widget.show()
    app.processEvents()
    return app, widget


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_initial_controls_accessibility_and_no_implicit_consultation(locale):
    app = create_application([])
    widget = ConsultantWidget(lambda: pytest.fail("Idle UI must not open the vault"),
                              translator=Translator(locale))
    translator = Translator(locale)
    assert widget.consult_button.text() == translator.text("consultant.consult")
    assert widget.clear_button.text() == translator.text("consultant.clear")
    assert widget.copy_button.text() == translator.text("consultant.copy")
    assert widget.codes_input.accessibleName() == translator.text("consultant.input_accessible")
    assert widget.codes_input.accessibleDescription() == widget.codes_input.toolTip() == (
        translator.text("consultant.input_description"))
    assert widget.codes_input.placeholderText() == "CPF_ID-ABCDEFGHI234\nIP-BCDEFGHI234A"
    assert widget.results_output.accessibleName() == translator.text("consultant.results_accessible")
    assert widget.results_output.accessibleDescription() == widget.results_output.empty_text == (
        translator.text("consultant.empty"))
    assert widget.results_output.isReadOnly() and not widget.results_output.toPlainText()
    assert not widget.copy_button.isEnabled() and not widget._results
    assert widget.status_label.text() == ""
    assert widget.status_label.property("feedbackState") == "neutral"
    assert widget.warning_label.text() == translator.text("consultant.warning")
    assert widget.warning_label.property("feedbackState") == "warning"
    assert widget.warning_label.textFormat() == widget.status_label.textFormat() == Qt.TextFormat.PlainText
    app.clipboard().setText("Synthetic clipboard sentinel")
    widget.copy_result()
    widget.retranslate(Translator("en"))
    assert app.clipboard().text() == "Synthetic clipboard sentinel"


def test_real_consultation_equivalence_data_order_copy_and_read_only_vault(vault, monkeypatch):
    repository, codes = vault
    before = repository.database_path.read_bytes()
    read_only = repository.as_read_only()
    queries = []
    connect = read_only._connect_for_read

    def traced_connect():
        connection = connect()
        connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr(read_only, "_connect_for_read", traced_connect)
    monkeypatch.setattr(read_only, "transaction", lambda: pytest.fail("No consultation writes"))
    raw = f"  {codes[0].lower()} ; {codes[3]}, {codes[0]}\n{codes[1]};{codes[4]}\n{codes[2]}"
    results_by_locale = []
    copied = []
    for locale in ("en", "pt-BR"):
        app, widget = show_widget(read_only, locale)
        app.clipboard().setText("Synthetic clipboard sentinel")
        widget.codes_input.setPlainText(raw)
        widget.consult_button.click()
        results = widget._results
        results_by_locale.append(results)
        assert [result.code for result in results] == [codes[i] for i in (0, 3, 1, 4, 2)]
        assert all(result.status is ConsultationStatus.FOUND for result in results)
        assert [result.mapping.occurrence_count for result in results] == [5, 5, 4, 2, 1]
        assert widget.codes_input.toPlainText() == raw
        assert widget.status_label.property("feedbackState") == "success"
        assert widget.status_label.text() == Translator(locale).plural(
            "consultant.status.completed", 5, found=5)
        displayed = widget.results_output.toPlainText()
        for result in results:
            mapping = result.mapping
            for value in (mapping.code, mapping.prefix, mapping.first_seen, mapping.last_seen):
                assert value in displayed
            if isinstance(mapping, CompositeMapping):
                assert composite_text(mapping.canonical_values) in displayed
                for variation in mapping.variations:
                    assert composite_text(variation.original_values) in displayed
            else:
                assert mapping.source_header in displayed
                for variation in mapping.variations:
                    assert variation.original_value in displayed
        assert ORIGINAL in displayed and HEADER in displayed and LONG_VALUE in displayed
        assert "consultant.consult" in displayed  # A stored value, never a message ID.
        assert app.clipboard().text() == "Synthetic clipboard sentinel"
        widget.copy_button.click()
        copied.append(app.clipboard().text())
        assert copied[-1] == displayed  # Exactly the current plain-text report, as before.
        assert widget.status_label.text() == Translator(locale).text("consultant.status.copied")
    assert results_by_locale[0] == results_by_locale[1]
    assert copied[0] != copied[1]  # Only the report labels are language dependent.
    assert all(ORIGINAL in text and HEADER in text and LONG_VALUE in text for text in copied)
    assert queries and all(query.lstrip().split()[0].upper() in {"SELECT", "BEGIN", "PRAGMA"}
                           for query in queries)
    assert repository.database_path.read_bytes() == before


@pytest.mark.parametrize("reverse", [False, True])
def test_retranslation_preserves_result_identity_data_selection_focus_scroll_and_query(vault, monkeypatch, reverse):
    repository, codes = vault
    app, widget = show_widget(repository, "pt-BR")
    widget.codes_input.setPlainText("\n".join(codes))
    widget.consult()
    results = widget._results
    objects = [(result, result.mapping, result.mapping.variations) for result in results]
    text = widget.results_output.toPlainText()
    query = f"  {codes[0].lower()} ; user query & <b>not a label</b>  "
    widget.codes_input.setPlainText(query)
    input_cursor = widget.codes_input.textCursor()
    input_cursor.setPosition(3)
    input_cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
    widget.codes_input.setTextCursor(input_cursor)
    # A supplementary Unicode character exercises Qt's UTF-16 cursor offsets.
    cursor = widget.results_output.document().find(ORIGINAL)
    assert not cursor.isNull()
    if reverse:
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        cursor.setPosition(end)
        cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
    widget.results_output.setTextCursor(cursor)
    widget.results_output.setFocus()
    app.processEvents()
    vertical = widget.results_output.verticalScrollBar()
    vertical.setValue(min(3, vertical.maximum()))
    scroll = vertical.value()
    input_events = QSignalSpy(widget.codes_input.textChanged)
    output_events = QSignalSpy(widget.results_output.textChanged)
    app.clipboard().setText("Synthetic clipboard sentinel")
    monkeypatch.setattr(widget._service, "consult", lambda _: pytest.fail("No re-query"))
    monkeypatch.setattr(widget._service, "_repository_factory", lambda: pytest.fail("No reopen"))
    monkeypatch.setattr(repository, "get_mapping_for_inspection", lambda _: pytest.fail("No reload"))
    for locale in ("en", "en", "pt-BR", "en", "pt-BR"):
        widget.retranslate(Translator(locale))
        app.processEvents()
        assert widget._results is results
        assert all(result is old and result.mapping is mapping and result.mapping.variations is variations
                   for result, (old, mapping, variations) in zip(results, objects, strict=True))
        assert widget.codes_input.toPlainText() == query
        assert widget.codes_input.textCursor().selectionStart() == 3
        assert widget.codes_input.textCursor().selectionEnd() == 8
        assert widget.results_output.textCursor().selectedText() == ORIGINAL
        assert (widget.results_output.textCursor().anchor() >
                widget.results_output.textCursor().position()) is reverse
        assert vertical.value() == scroll and widget.results_output.hasFocus()
        assert app.clipboard().text() == "Synthetic clipboard sentinel"
        assert widget.copy_button.isEnabled()
        assert widget.status_label.property("feedbackState") == "success"
    assert widget.results_output.toPlainText() == text
    assert input_events.count() == output_events.count() == 0


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_retranslation_does_not_duplicate_actions_or_revive_cleared_sensitive_results(vault, monkeypatch, locale):
    repository, codes = vault
    app, widget = show_widget(repository, locale)
    service = widget._service.consult
    calls = []

    def tracked(raw):
        calls.append(raw)
        return service(raw)

    monkeypatch.setattr(widget._service, "consult", tracked)
    for language in ("en", "pt-BR") * 3:
        widget.retranslate(Translator(language))
    widget.codes_input.setPlainText(codes[0])
    widget.consult_button.click()
    assert calls == [codes[0]]
    widget.copy_button.click()
    widget.retranslate(Translator(locale))
    assert widget.status_label.text() == Translator(locale).text("consultant.status.copied")
    clipboard = app.clipboard().text()
    widget.clear_button.click()
    app.processEvents()
    for language in ("en", "pt-BR"):
        widget.retranslate(Translator(language))
        assert not widget._results and not widget.results_output.toPlainText()
        assert not widget.status_label.text() and not widget.copy_button.isEnabled()
        assert widget.status_label.property("feedbackState") == "neutral"
        assert widget.codes_input.hasFocus()
    assert app.clipboard().text() == clipboard  # Clearing never changes clipboard policy.
    second = ConsultantWidget(lambda: repository, translator=Translator(locale))
    assert not second.codes_input.toPlainText() and not second.results_output.toPlainText()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("input_kind,state", [("empty", "error"), ("invalid", "warning"),
                                              ("missing", "warning"), ("mixed", "warning"),
                                              ("found", "success")])
def test_typed_outcomes_and_consecutive_queries_keep_distinct_feedback(vault, locale, input_kind, state):
    repository, codes = vault
    _, widget = show_widget(repository, locale)
    widget.codes_input.setPlainText(codes[0])
    widget.consult()
    query = {"empty": "  ", "invalid": "not-a-token", "missing": MISSING,
             "mixed": f"{codes[0]}\n{MISSING}", "found": codes[0]}[input_kind]
    widget.codes_input.setPlainText(query)
    widget.consult()
    assert widget.status_label.property("feedbackState") == state
    assert widget.copy_button.isEnabled() is (input_kind != "empty")
    if input_kind != "found":
        if input_kind != "mixed":
            assert ORIGINAL not in widget.results_output.toPlainText()
        if input_kind in {"invalid", "missing"}:
            identifier = "invalid" if input_kind == "invalid" else "not_found"
            assert Translator(locale).text(f"consultant.result.{identifier}") in widget.results_output.toPlainText()
    widget.retranslate(Translator("pt-BR" if locale == "en" else "en"))
    assert widget.status_label.property("feedbackState") == state
    if input_kind == "empty":
        assert not widget._results and not widget.results_output.toPlainText()


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_invalid_input_never_opens_unavailable_vault(locale):
    create_application([])
    widget = ConsultantWidget(lambda: pytest.fail("Invalid input requires no vault"),
                              translator=Translator(locale))
    widget.codes_input.setPlainText("INVALID")
    widget.consult()
    assert widget._results[0].status is ConsultationStatus.INVALID
    assert widget._service.last_error is None


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("fault", ["unavailable", "missing", "corrupt_database", "scalar", "variation", "composite"])
def test_recovery_failures_remain_fail_closed_redacted_and_distinct_from_not_found(vault, tmp_path, locale, fault):
    repository, codes = vault
    code = codes[0]
    if fault == "unavailable":
        def factory():
            raise PermissionError(f"PRIVATE_PATH {ORIGINAL} KEY_SENTINEL")
    elif fault == "missing":
        factory = lambda: VaultRepository(tmp_path / "missing.db", VaultCipher(b"L" * 32), read_only=True)
    elif fault == "corrupt_database":
        invalid = tmp_path / "corrupt.db"
        invalid.write_bytes(b"not a database")
        factory = lambda: VaultRepository(invalid, VaultCipher(b"L" * 32), read_only=True)
    else:
        table = {"scalar": "vault_mappings", "variation": "vault_variations",
                 "composite": "composite_mappings"}[fault]
        if fault == "composite":
            code = codes[3]
        column = "canonical_encrypted_value" if fault == "scalar" else "encrypted_value"
        with sqlite3.connect(repository.database_path) as connection:
            connection.execute(f"UPDATE {table} SET {column} = X'00' WHERE code = ?", (code,))
        factory = lambda: repository.as_read_only()
    app = create_application([])
    widget = ConsultantWidget(factory, translator=Translator(locale))
    app.clipboard().setText("Synthetic clipboard sentinel")
    widget.codes_input.setPlainText(code)
    widget.consult()
    assert widget._results[0].status is ConsultationStatus.RECOVERY_FAILED
    assert widget._results[0].mapping is None and widget._service.last_error is not None
    for language in (locale, "pt-BR" if locale == "en" else "en"):
        widget.retranslate(Translator(language))
        displayed = widget.results_output.toPlainText()
        assert Translator(language).text("consultant.result.recovery_failed") in displayed
        assert Translator(language).text("consultant.result.not_found") not in displayed
        assert widget.status_label.property("feedbackState") == "error"
        assert all(value not in displayed + widget.status_label.text() for value in
                   (ORIGINAL, "PRIVATE_PATH", "KEY_SENTINEL", b"L".hex() * 32,
                    str(tmp_path), "encrypted_value", "Traceback"))
        assert app.clipboard().text() == "Synthetic clipboard sentinel"
    assert not (tmp_path / "missing.db").exists()


@pytest.mark.parametrize("rule", list(Rule))
@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_normalization_names_are_presentation_only(vault, monkeypatch, rule, locale):
    repository, codes = vault
    mapping = repository.get_mapping_for_inspection(codes[0])
    monkeypatch.setattr("data_mask_studio.normalization.normalize_value",
                        lambda *_: pytest.fail("Never normalize stored inspection values"))
    result = ConsultationResult(codes[0], ConsultationStatus.FOUND,
                                replace(mapping, normalization_rule=rule))
    displayed = _render_result(result, Translator(locale))
    assert Translator(locale).text(f"masking.normalization.{rule.value}") in displayed
    assert ORIGINAL in displayed and HEADER in displayed


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_untyped_fallback_is_literal_not_guessed_or_rich_text(locale):
    message = '<b>Mensagem do serviço & Search {count}</b>'
    result = ConsultationResult(MISSING, ConsultationStatus.FOUND, message=message)
    assert _render_result(result, Translator(locale)).endswith("\n" + message)
    empty = ConsultationResult(MISSING, ConsultationStatus.FOUND)
    assert _render_result(empty, Translator(locale)).endswith(
        "\n" + Translator(locale).text("consultant.unavailable"))
    create_application([])
    widget = ConsultantWidget(lambda: pytest.fail("No consultation"), translator=Translator(locale))
    widget._set_status(message, is_error=True, state="success")
    widget.retranslate(Translator("pt-BR" if locale == "en" else "en"))
    assert widget.status_label.text() == message
    assert widget.status_label.property("feedbackState") == "error"
    assert widget.status_label.textFormat() == Qt.TextFormat.PlainText


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("value", ['  Ação & <b>Search</b>  ', 'First line\nOutra linha\t😀',
                                  'consultant.consult {found} {{count}} %s'])
def test_literal_values_are_copied_exactly_without_rich_text_or_interpretation(tmp_path, locale, value):
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"L" * 32))
    code = TokenGenerator(b"H" * 32).generate("TEXT", value)
    with repository.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(code, "TEXT", value, "Nome & <tag>")])
    app, widget = show_widget(repository, locale)
    widget.codes_input.setPlainText(code)
    widget.consult()
    widget.copy_result()
    assert value in widget.results_output.toPlainText()
    assert app.clipboard().text() == widget.results_output.toPlainText()
    clipboard = app.clipboard().text()
    widget.retranslate(Translator("pt-BR" if locale == "en" else "en"))
    assert value in widget.results_output.toPlainText()
    assert app.clipboard().text() == clipboard


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_typed_composite_details_preserve_counts_order_ambiguity_and_original_visibility(vault, locale):
    repository, codes = vault
    translator = Translator(locale)
    for index, unique in ((3, False), (4, True)):
        mapping = repository.get_mapping_for_inspection(codes[index])
        before = mapping
        rendered = _render_result(ConsultationResult(mapping.code, ConsultationStatus.FOUND, mapping), translator)
        assert translator.text("consultant.result.component_count", count=2) in rendered
        assert translator.text("consultant.result.total", count=2 if unique else 5) in rendered
        assert translator.text("consultant.result.restore_unique" if unique
                               else "consultant.result.restore_ambiguous") in rendered
        assert rendered.index(translator.text("consultant.result.component", index=1,
                              normalization=translator.text("masking.normalization.collapse_whitespace"))) < (
            rendered.index(translator.text("consultant.result.component", index=2,
                           normalization=translator.text("masking.normalization.exact"))))
        assert "Source header:" not in rendered and "Cabeçalho de origem:" not in rendered
        assert "identity_version" not in rendered and "payload_version" not in rendered
        assert mapping is before
    scalar = repository.get_mapping_for_inspection(codes[0])
    scalar = replace(scalar, canonical_value="SYNTHETIC_NOT_PREVIOUSLY_DISPLAYED")
    rendered = _render_result(ConsultationResult(scalar.code, ConsultationStatus.FOUND, scalar), translator)
    assert "SYNTHETIC_NOT_PREVIOUSLY_DISPLAYED" not in rendered


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_main_window_injects_translator_and_relabels_existing_page_only(tmp_path, locale, monkeypatch):
    app = create_application([])
    window = build_localized_window(tmp_path, Translator(locale))
    widget = window.consultant_widget
    window.show()
    window.set_current_page(window.page_index(widget))
    app.processEvents()
    shell = window.page_shells[window.page_index(widget)]
    assert window.page_stack.count() == 8
    assert shell.header.title_label.text() == Translator(locale).text("navigation.browse_vault")
    assert shell.header.description_label.text() == Translator(locale).text("navigation.description.browse_vault")
    assert widget.consult_button.text() == Translator(locale).text("consultant.consult")
    widget.codes_input.setPlainText("  Search & ação <literal>  ")
    widget.codes_input.setFocus()
    monkeypatch.setattr(widget._service, "_repository_factory", lambda: pytest.fail("No query"))
    other = Translator("pt-BR" if locale == "en" else "en")
    window.retranslate(other)
    assert window.consultant_widget is widget and shell.content is widget
    assert widget.consult_button.text() == other.text("consultant.consult")
    assert widget.codes_input.toPlainText() == "  Search & ação <literal>  "
    assert widget.codes_input.hasFocus()
    assert window.current_page_index() == window.page_index(widget)


def test_normal_startup_stays_portuguese_with_saved_english_preference(tmp_path):
    create_application([])
    preferences = PreferencesService(tmp_path / "preferences.json",
                                     ui_languages_provider=lambda: pytest.fail("No global activation"))
    assert preferences.save_language("en").error is None
    before = preferences.path.read_bytes()
    window = build_localized_window(tmp_path, preferences_service=preferences)
    assert window.consultant_widget.consult_button.text() == "Consultar"
    assert window.page_shells[window.page_index(window.consultant_widget)].header.title_label.text() == "Consultar cofre"
    assert preferences.path.read_bytes() == before
