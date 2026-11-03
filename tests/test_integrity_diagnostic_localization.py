"""Diagnostic translation must never become part of the integrity decision/report."""

from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
import pickle
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTextEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.i18n import Translator
from data_mask_studio.gui.integrity_presentation import integrity_check_presentation, statistics_error_messages
from data_mask_studio.integrity import (
    CheckResult, IntegrityAuditor, IntegrityCheckKind, IntegrityExplanation, IntegrityReason, IntegrityStatus,
)
from data_mask_studio.integrity import auditor as auditor_module
from data_mask_studio.maintenance import StatisticsReason, MaintenanceStatus, safe_diagnostic_report
from test_maintenance_localization import overview, complete
from test_integrity import prepare_valid_environment, FixedKeyProvider, HMAC_KEY, VAULT_KEY
from test_composite_integrity import setup as seed_composite


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def test_real_integrity_descriptions_translate_after_completion(overview):
    app, widget, _, _ = overview
    result = complete(app, widget)
    widget.retranslate(Translator("en"))
    assert widget._last_diagnostic is result
    assert "The key is accessible and has a valid format." in widget.overview_output.toPlainText()


def test_real_integrity_check_labels_translate_without_changing_identifiers(overview):
    app, widget, _, _ = overview
    result = complete(app, widget)
    original = tuple(check.check_type for check in result.audit.checks)
    widget.retranslate(Translator("en"))
    assert "HMAC key availability" in widget.overview_output.toPlainText()
    assert "Composite mapping authentication, metadata and payload" in widget.overview_output.toPlainText()
    assert tuple(check.check_type for check in result.audit.checks) == original


def test_known_statistics_issue_translates_without_recollecting_statistics(overview):
    app, widget, paths, _ = overview
    paths.profiles_path.write_bytes(b"synthetic invalid profiles")
    result = complete(app, widget)
    widget.retranslate(Translator("en"))
    assert "Profile statistics unavailable; collection incomplete." in widget.overview_output.toPlainText()
    assert result.statistics.errors == ("Estatísticas de perfis indisponíveis; coleta incompleta.",)


def test_complete_mixed_audit_preserves_legacy_check_order_and_explicit_kind_identity(overview):
    app, widget, _, _ = overview
    result = complete(app, widget)
    expected = [
        (IntegrityCheckKind.HMAC_KEY, "Disponibilidade da chave HMAC"),
        (IntegrityCheckKind.AES_KEY, "Disponibilidade da chave AES"),
        (IntegrityCheckKind.SQLITE, "Integridade do SQLite"),
        (IntegrityCheckKind.SCHEMA, "Versão e estrutura do esquema"),
        (IntegrityCheckKind.AES_AUTHENTICATION, "Autenticação AES-GCM"),
        (IntegrityCheckKind.VARIATION_COMPATIBILITY, "Compatibilidade das variações"),
        (IntegrityCheckKind.OCCURRENCE_COUNTERS, "Contadores de ocorrências"),
        (IntegrityCheckKind.TABLE_REFERENCES, "Referências entre tabelas"),
        (IntegrityCheckKind.CODE_FORMAT, "Formato dos códigos"),
        (IntegrityCheckKind.TOKEN_RECOMPUTATION, "Recomputação dos tokens"),
        (IntegrityCheckKind.COMPOSITE_MAPPINGS, "Mappings compostos: autenticação, metadata e payload"),
        (IntegrityCheckKind.COMPOSITE_VARIATIONS, "Variações compostas: autenticação e payload"),
        (IntegrityCheckKind.COMPOSITE_TOKENS, "Recomputação dos tokens compostos"),
        (IntegrityCheckKind.COMPOSITE_COUNTERS, "Contadores compostos"),
        (IntegrityCheckKind.COMPOSITE_REFERENCES, "Referências compostas"),
        (IntegrityCheckKind.COMPOSITE_UNIQUENESS, "Unicidade de códigos e variações compostas"),
        (IntegrityCheckKind.PROFILES, "Validade estrutural dos perfis"),
        (IntegrityCheckKind.TEMPORARIES, "Arquivos temporários abandonados"),
    ]
    assert [(check.explanation.kind, check.check_type) for check in result.audit.checks] == expected


REASONS = [
    (IntegrityReason.KEY_MISSING, "A chave local não foi encontrada."),
    (IntegrityReason.KEY_INACCESSIBLE, "A chave local não está acessível."),
    (IntegrityReason.KEY_VALID, "A chave está acessível e possui formato válido."),
    (IntegrityReason.SQLITE_VALID, "PRAGMA integrity_check retornou ok."),
    (IntegrityReason.SQLITE_INCONSISTENT, "O SQLite informou inconsistências internas."),
    (IntegrityReason.SCHEMA_INVALID, "O esquema não corresponde à versão esperada."),
    (IntegrityReason.SCHEMA_VALID, "Esquema 4 validado."),
    (IntegrityReason.INCONSISTENCIES, "Foram encontradas inconsistências."),
    (IntegrityReason.NO_INCONSISTENCIES, "Nenhuma inconsistência encontrada."),
    (IntegrityReason.COMPOSITE_INCONSISTENCIES, "Foram encontradas inconsistências ou registros não verificáveis."),
    (IntegrityReason.PROFILES_INVALID, "O arquivo de perfis é inválido."),
    (IntegrityReason.PROFILES_VALID, "0 perfil(is) validado(s)."),
    (IntegrityReason.TEMPORARIES_FOUND, "Foram encontrados artefatos temporários conhecidos."),
    (IntegrityReason.NO_TEMPORARIES, "Nenhum arquivo temporário encontrado."),
    (IntegrityReason.DATABASE_MISSING, "O cofre ainda não existe; não há registros para verificar."),
    (IntegrityReason.DATABASE_UNAVAILABLE, "Verificação não aplicável sem um cofre local."),
    (IntegrityReason.DATABASE_FAILED, "A verificação não pôde ser concluída com segurança."),
    (IntegrityReason.MIGRATION_PENDING, "O cofre precisa ser migrado antes da verificação de integridade."),
]


@pytest.mark.parametrize("reason,legacy", REASONS, ids=[reason.value for reason, _ in REASONS])
def test_original_audit_producers_attach_safe_metadata_without_changing_legacy_fields(
    tmp_path, monkeypatch, reason, legacy,
):
    paths, _, _ = prepare_valid_environment(tmp_path)
    hmac = FixedKeyProvider(HMAC_KEY)
    if reason is IntegrityReason.KEY_MISSING:
        hmac.key_path = paths.directory / "absent.key"
    elif reason is IntegrityReason.KEY_INACCESSIBLE:
        def inaccessible():
            raise RuntimeError("PRIVATE-KEY-DETAIL")
        monkeypatch.setattr(hmac, "get_key", inaccessible)
    elif reason in (IntegrityReason.DATABASE_MISSING, IntegrityReason.DATABASE_UNAVAILABLE):
        paths = replace(paths, vault_database_path=paths.directory / "absent.db")
    elif reason is IntegrityReason.DATABASE_FAILED:
        invalid = paths.directory / "invalid.db"
        invalid.write_bytes(b"not a SQLite vault")
        paths = replace(paths, vault_database_path=invalid)
    elif reason in (IntegrityReason.SCHEMA_INVALID, IntegrityReason.MIGRATION_PENDING):
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("PRAGMA user_version = " + ("99" if reason is IntegrityReason.SCHEMA_INVALID else "2"))
    elif reason is IntegrityReason.INCONSISTENCIES:
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("UPDATE vault_mappings SET canonical_nonce = X'00'")
    elif reason is IntegrityReason.COMPOSITE_INCONSISTENCIES:
        seed_composite(paths.directory)
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("UPDATE composite_mappings SET nonce = X'00'")
    elif reason is IntegrityReason.PROFILES_INVALID:
        paths.profiles_path.write_bytes(b"synthetic invalid profiles")
    elif reason is IntegrityReason.TEMPORARIES_FOUND:
        (paths.directory / ".profiles.json.ab12_cd3.tmp").write_bytes(b"synthetic owned residue")
    elif reason is IntegrityReason.SQLITE_INCONSISTENT:
        connect = auditor_module.connect_read_only

        class InconsistentPragma:
            def __init__(self, path):
                self.connection = connect(path)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, query, *args):
                if query == "PRAGMA integrity_check":
                    return [("synthetic structural failure",)]
                return self.connection.execute(query, *args)

        monkeypatch.setattr(auditor_module, "connect_read_only", InconsistentPragma)
    report = IntegrityAuditor(paths, hmac, FixedKeyProvider(VAULT_KEY)).run()
    matching = [check for check in report.checks if check.explanation.reason is reason]
    assert matching, reason
    for check in matching:
        assert type(check.check_type) is str and check.message == legacy
        assert isinstance(check.explanation.kind, IntegrityCheckKind)
        assert check == replace(check, explanation=None)  # Metadata cannot change result equality/hash.
        assert hash(check) == hash(replace(check, explanation=None))
        assert not hasattr(check.explanation, "__dict__")
        if reason is IntegrityReason.PROFILES_VALID:
            assert check.explanation.count == check.examined == 0
        elif reason is IntegrityReason.SCHEMA_VALID:
            assert check.explanation.schema_version == report.schema_version == 4
        else:
            assert check.explanation.count is check.explanation.schema_version is None
        for locale in ("en", "pt-BR"):
            translator = Translator(locale)
            name, message = integrity_check_presentation(check, translator)
            assert name == translator.text("maintenance.integrity.kind." + check.explanation.kind.value)
            identifier = "maintenance.integrity.reason." + reason.value
            expected = (translator.plural(identifier, 0) if reason is IntegrityReason.PROFILES_VALID else
                        translator.text(identifier, version=4) if reason is IntegrityReason.SCHEMA_VALID else
                        translator.text(identifier))
            assert message == expected and "PRIVATE-" not in message


@pytest.mark.parametrize("kind", list(IntegrityCheckKind))
def test_all_check_kinds_have_explicit_catalog_labels_without_interpreting_legacy_identifiers(kind):
    check = CheckResult("literal legacy identifier", IntegrityStatus.INTACT, 1, 0, "literal description",
        explanation=IntegrityExplanation(IntegrityReason.NO_INCONSISTENCIES, kind))
    for locale in ("pt-BR", "en"):
        translator = Translator(locale)
        name, message = integrity_check_presentation(check, translator)
        assert name == translator.text("maintenance.integrity.kind." + kind.value)
        assert message == translator.text("maintenance.integrity.reason.no_inconsistencies")
        assert check.check_type == "literal legacy identifier" and check.message == "literal description"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_profile_counts_use_plural_contract_without_changing_legacy_message(count):
    check = CheckResult("Validade estrutural dos perfis", IntegrityStatus.INTACT, count, 0,
        f"{count} perfil(is) validado(s).", explanation=IntegrityExplanation(
            IntegrityReason.PROFILES_VALID, IntegrityCheckKind.PROFILES, count=count))
    for locale in ("pt-BR", "en"):
        assert integrity_check_presentation(check, Translator(locale))[1] == Translator(locale).plural(
            "maintenance.integrity.reason.profiles_valid", count)
    assert check.message == f"{count} perfil(is) validado(s)."


@pytest.mark.parametrize("explanation", [None, "unsupported", IntegrityExplanation("future_reason"),
    IntegrityExplanation(IntegrityReason.PROFILES_VALID),
    IntegrityExplanation(IntegrityReason.PROFILES_VALID, count=True),
    IntegrityExplanation(IntegrityReason.PROFILES_VALID, count=2),
    IntegrityExplanation(IntegrityReason.SCHEMA_VALID),
    IntegrityExplanation(IntegrityReason.SCHEMA_VALID, schema_version=-1)])
def test_unclassified_or_unsupported_explanations_are_never_inferred_from_text(explanation):
    check = CheckResult("maintenance.integrity.kind.hmac_key", IntegrityStatus.ATTENTION, 1, 0,
        "Nenhuma inconsistência encontrada. <b>& {count}", ("record#99",), explanation=explanation)
    for locale in ("en", "pt-BR"):
        assert integrity_check_presentation(check, Translator(locale)) == (check.check_type, check.message)


def test_metadata_is_immutable_optional_pickle_safe_and_contains_only_safe_scalar_fields():
    explanation = IntegrityExplanation(IntegrityReason.SCHEMA_VALID, IntegrityCheckKind.SCHEMA, schema_version=4)
    with pytest.raises(FrozenInstanceError):
        explanation.schema_version = 99
    assert set(asdict(explanation)) == {"reason", "kind", "count", "schema_version"}
    check = CheckResult("legacy", IntegrityStatus.INTACT, 2, 0, "legacy message", (), explanation)
    assert pickle.loads(pickle.dumps(check)).explanation == explanation
    assert CheckResult("legacy", IntegrityStatus.INTACT, 2, 0, "legacy message").explanation is None


@pytest.mark.parametrize("reason,legacy", [
    (StatisticsReason.VAULT_UNAVAILABLE, "Estatísticas do cofre indisponíveis; coleta incompleta."),
    (StatisticsReason.PROFILES_UNAVAILABLE, "Estatísticas de perfis indisponíveis; coleta incompleta."),
    (StatisticsReason.STORAGE_UNAVAILABLE, "Estatísticas de armazenamento indisponíveis; coleta incompleta."),
])
def test_statistics_issues_are_typed_at_the_existing_failure_point(overview, monkeypatch, reason, legacy):
    app, widget, paths, _ = overview
    if reason is StatisticsReason.PROFILES_UNAVAILABLE:
        paths.profiles_path.write_bytes(b"synthetic invalid profiles")
    else:
        def unavailable(*args, **kwargs):
            raise OSError("PRIVATE-STATISTICS-DETAIL")
        monkeypatch.setattr("data_mask_studio.maintenance.diagnostics." + (
            "create_sqlite_snapshot" if reason is StatisticsReason.VAULT_UNAVAILABLE else "_free_space"), unavailable)
    result = complete(app, widget)
    stats = result.statistics
    assert result.status is MaintenanceStatus.FAILURE
    assert stats.errors == (legacy,) and stats.error_reasons == (reason,)
    assert stats == replace(stats, error_reasons=())
    for locale in ("en", "pt-BR"):
        widget.retranslate(Translator(locale))
        assert Translator(locale).text("maintenance.overview.statistics." + reason.value) in widget.overview_output.toPlainText()
        assert "PRIVATE-" not in widget.overview_output.toPlainText()
    for reasons in ((), (None,), ("future_reason",)):
        unknown = replace(stats, error_reasons=reasons)
        assert statistics_error_messages(unknown, Translator("en")) == (legacy,)


def test_multiple_statistics_issues_keep_original_order_and_parallel_optional_reasons(overview, monkeypatch):
    app, widget, paths, _ = overview
    paths.profiles_path.write_bytes(b"synthetic invalid profiles")

    def unavailable(*args, **kwargs):
        raise OSError("PRIVATE-STATISTICS-DETAIL")

    monkeypatch.setattr("data_mask_studio.maintenance.diagnostics.create_sqlite_snapshot", unavailable)
    monkeypatch.setattr("data_mask_studio.maintenance.diagnostics._free_space", unavailable)
    result = complete(app, widget)
    assert result.statistics.errors == (
        "Estatísticas do cofre indisponíveis; coleta incompleta.",
        "Estatísticas de perfis indisponíveis; coleta incompleta.",
        "Estatísticas de armazenamento indisponíveis; coleta incompleta.",
    )
    assert result.statistics.error_reasons == (
        StatisticsReason.VAULT_UNAVAILABLE, StatisticsReason.PROFILES_UNAVAILABLE, StatisticsReason.STORAGE_UNAVAILABLE)


@pytest.mark.parametrize("condition", ["healthy", "attention", "failure"])
def test_live_translation_preserves_diagnostic_and_exact_canonical_clipboard_bytes(overview, monkeypatch, condition):
    app, widget, paths, _ = overview
    if condition == "attention":
        (paths.directory / ".profiles.json.ab12_cd3.tmp").write_bytes(b"synthetic owned residue")
    elif condition == "failure":
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("UPDATE composite_mappings SET nonce = X'00'")
    result = complete(app, widget)
    assert result.status is {"healthy": MaintenanceStatus.HEALTHY, "attention": MaintenanceStatus.ATTENTION,
                            "failure": MaintenanceStatus.FAILURE}[condition]
    legacy = replace(result, statistics=replace(result.statistics, error_reasons=()),
        audit=replace(result.audit, checks=tuple(replace(check, explanation=None) for check in result.audit.checks)))
    canonical = safe_diagnostic_report(legacy).encode("utf-8")
    assert safe_diagnostic_report(result).encode("utf-8") == canonical
    before = asdict(result)
    references = [identifier for check in result.audit.checks for identifier in check.identifiers]
    widget.overview_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
    if references:
        cursor = widget.overview_output.document().find(references[0])
        widget.overview_output.setTextCursor(cursor)
    widget.overview_output.setFocus()
    app.processEvents()
    worker, progress, severity = widget._worker, widget.progress.value(), widget.status_label.property("feedbackState")
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en", "pt-BR")]

    def forbidden(*args, **kwargs):
        pytest.fail("Translation must not audit or access the filesystem/SQLite")

    for translator in translators:
        scroll = (widget.overview_output.verticalScrollBar().value(), widget.overview_output.horizontalScrollBar().value())
        with monkeypatch.context() as patch:
            patch.setattr(widget._diagnostics, "run", forbidden)
            patch.setattr(widget._diagnostics, "_statistics", forbidden)
            patch.setattr(widget._diagnostics._auditor, "run", forbidden)
            patch.setattr(sqlite3, "connect", forbidden)
            for name in ("open", "stat", "iterdir", "rglob"):
                patch.setattr(Path, name, forbidden)
            widget.retranslate(translator)
            assert widget._last_diagnostic is result and widget._last_audit is result.audit
            assert asdict(result) == before and widget._worker is worker
            assert widget.progress.value() == progress and widget.status_label.property("feedbackState") == severity
            assert widget.copy_report_button.isEnabled() and widget.overview_output.hasFocus()
            # Qt must clamp a scroll position when a translation shortens its range.
            assert (widget.overview_output.verticalScrollBar().value(), widget.overview_output.horizontalScrollBar().value()) == (
                min(scroll[0], widget.overview_output.verticalScrollBar().maximum()),
                min(scroll[1], widget.overview_output.horizontalScrollBar().maximum()))
            if references:
                assert widget.overview_output.textCursor().selectedText() == references[0]
        # Canonical reporting deliberately retains its existing application-version
        # lookup. Do not change that contract to impose retranslation's no-I/O rule.
        widget.copy_report()
        assert app.clipboard().text().encode("utf-8") == canonical
        assert safe_diagnostic_report(result).encode("utf-8") == canonical
