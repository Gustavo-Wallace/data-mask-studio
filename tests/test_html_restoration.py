import codecs
import importlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from data_mask_studio.html_restoration import (
    HTMLMissingCodeError,
    HTMLMissingCodePolicy,
    HTMLRestorationCancelled,
    HTMLRestorationError,
    HTMLRestorationSecurityError,
    analyze_html,
    inspect_html,
    restore_html,
    suggested_html_output_path,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.performance import (
    BALANCED_SETTINGS,
    BoundedCache,
    HTMLProcessingMetrics,
)
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

KEY = b"H" * 32
CPF_CODE = "CPF-ABCDEFGHI234"
NAME_CODE = "NOME-BCDEFGHI234A"
MISSING_CODE = "CPF-CDEFGHI234AB"
SPECIAL_CODE = "TEXT-DEFGHI234ABC"
CPF_ORIGINAL = "123.456.789-00"
NAME_ORIGINAL = "João da Silva"


def make_repository(tmp_path: Path) -> VaultRepository:
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(KEY))
    cpf = MappingCandidate(
        CPF_CODE,
        "CPF",
        CPF_ORIGINAL,
        "CPF",
        canonical_value="12345678900",
        normalization_rule=NormalizationRule.CPF,
    )
    cpf.add_variation("12345678900", NormalizationRule.CPF)
    name = MappingCandidate(NAME_CODE, "NOME", NAME_ORIGINAL, "Nome")
    with repository.transaction() as transaction:
        transaction.upsert_batch([cpf, name])
    return repository


def make_many_mappings(
    tmp_path: Path, amount: int
) -> tuple[VaultRepository, list[str]]:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    codes = [
        "SYN-" + "".join(
            alphabet[(index >> shift) & 31]
            for shift in range(55, -1, -5)
        )
        for index in range(amount)
    ]
    repository = VaultRepository(tmp_path / "many-vault.db", VaultCipher(KEY))
    candidates = [
        MappingCandidate(
            code,
            "SYN",
            f"synthetic-value-{index:05d}",
            "Synthetic",
        )
        for index, code in enumerate(codes)
    ]
    with repository.transaction() as transaction:
        transaction.upsert_batch(candidates)
    return repository, codes


def dashboard_html() -> str:
    return (
        "<!doctype html>\n"
        "<html><head><style>.card { color: #123; }</style></head>\n"
        f'<body data-cpf="{CPF_CODE}">\n'
        f"<p>{CPF_CODE}</p>\n"
        "<script>\n"
        f'const payload = {{"cpf":"{CPF_CODE}","name":"{NAME_CODE}"}};\n'
        'const escaped = "\\u00e3 &amp;";\n'
        "drawChart(payload);\n"
        "</script>\n"
        f"<span>{MISSING_CODE}</span><i>CPF-ABC</i><b>conteúdo comum</b>\n"
        "</body></html>\n"
    )


def test_html_analysis_counts_unique_repeated_found_missing_and_invalid(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "dashboard.html"
    source.write_text(dashboard_html(), encoding="utf-8")
    vault_before = repository.database_path.read_bytes()

    result = analyze_html(inspect_html(source), repository)

    assert result.unique_codes == 3
    assert result.total_occurrences == 5
    assert result.found_codes == 2
    assert result.missing_codes == 1
    assert result.invalid_similar_codes == 1
    assert result.prefixes == ("CPF", "NOME")
    assert repository.database_path.read_bytes() == vault_before


def test_analysis_and_restoration_batch_many_unique_codes_with_one_connection(
    tmp_path: Path,
) -> None:
    repository, codes = make_many_mappings(tmp_path, 850)
    source = tmp_path / "many.html"
    source.write_text(
        "<main>" + "".join(f"<i>{code}</i>" for code in codes) + "</main>",
        encoding="utf-8",
    )
    inspection = inspect_html(source)
    analysis_metrics = HTMLProcessingMetrics()
    restoration_metrics = HTMLProcessingMetrics()

    analysis = analyze_html(inspection, repository, metrics=analysis_metrics)
    destination = tmp_path / "many-restored.html"
    restored = restore_html(
        inspection, destination, repository, metrics=restoration_metrics
    )

    assert analysis.unique_codes == len(codes)
    assert analysis.found_codes == len(codes)
    assert restored.restored_occurrences == len(codes)
    assert analysis_metrics.vault.connections_opened == 1
    assert restoration_metrics.vault.connections_opened == 1
    assert analysis_metrics.vault.sqlite_queries == 6
    assert restoration_metrics.vault.sqlite_queries <= 8
    assert analysis_metrics.vault.decryptions == len(codes) * 2
    assert restoration_metrics.vault.decryptions == len(codes) * 2
    assert "synthetic-value-00000" in destination.read_text(encoding="utf-8")


def test_repeated_tokens_are_window_batched_with_bounded_cache(
    tmp_path: Path, monkeypatch
) -> None:
    restorer_module = importlib.import_module(
        "data_mask_studio.html_restoration.restorer"
    )
    repository, codes = make_many_mappings(tmp_path, 160)
    source = tmp_path / "repeated.html"
    source.write_text(
        "".join(
            f"<p>{codes[index % len(codes)]} {'x' * 80}</p>"
            for index in range(2_000)
        ),
        encoding="utf-8",
    )
    instances: list[BoundedCache] = []

    def cache_factory(limit: int):
        cache = BoundedCache(limit)
        instances.append(cache)
        return cache

    monkeypatch.setattr(
        restorer_module,
        "BALANCED_SETTINGS",
        replace(BALANCED_SETTINGS, restoration_cache_limit=32),
    )
    monkeypatch.setattr(restorer_module, "BoundedCache", cache_factory)
    metrics = HTMLProcessingMetrics()
    destination = tmp_path / "repeated-restored.html"

    result = restore_html(
        inspect_html(source), destination, repository, metrics=metrics
    )

    assert result.restored_occurrences == 2_000
    assert metrics.vault.connections_opened == 1
    assert metrics.vault.sqlite_queries < 20
    assert metrics.vault.cache_hits > 0
    assert instances and len(instances[0]) <= 32
    safe_metrics = str(metrics.to_safe_dict())
    assert codes[0] not in safe_metrics
    assert "synthetic-value" not in safe_metrics


def test_html_without_tokens_is_copied_identically(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "plain.html"
    content = "<!doctype html><p>Unicode: ação — &lt;seguro&gt;</p>"
    source.write_text(content, encoding="utf-8", newline="")
    metrics = HTMLProcessingMetrics()
    analysis = analyze_html(inspect_html(source), repository, metrics=metrics)
    destination = tmp_path / "plain-restored.html"

    result = restore_html(inspect_html(source), destination, repository)

    assert analysis.total_occurrences == 0
    assert result.total_occurrences == 0
    assert destination.read_text(encoding="utf-8") == content
    assert metrics.vault.sqlite_queries == 0


def test_restoration_replaces_visible_text_javascript_json_and_attributes(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "dashboard_anonimizado.html"
    original = dashboard_html()
    source.write_text(original, encoding="utf-8", newline="")
    source_before = source.read_bytes()
    vault_before = repository.database_path.read_bytes()
    destination = tmp_path / "dashboard_restaurado.html"

    result = restore_html(inspect_html(source), destination, repository)

    restored = destination.read_text(encoding="utf-8")
    expected = original.replace(CPF_CODE, CPF_ORIGINAL).replace(
        NAME_CODE, NAME_ORIGINAL
    )
    assert restored == expected
    assert '<body data-cpf="123.456.789-00">' in restored
    assert f'"cpf":"{CPF_ORIGINAL}"' in restored
    assert f'"name":"{NAME_ORIGINAL}"' in restored
    assert ".card { color: #123; }" in restored
    assert "drawChart(payload);" in restored
    assert 'const escaped = "\\u00e3 &amp;";' in restored
    assert "CPF-ABC" in restored
    assert MISSING_CODE in restored
    assert "conteúdo comum" in restored
    assert source.read_bytes() == source_before
    assert repository.database_path.read_bytes() == vault_before
    assert result.restored_occurrences == 4
    assert result.missing_occurrences == 1


def test_restoration_preserves_special_characters_exactly(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    special_value = "\"'backslash\\line\nUnicode ação <tag> & value"
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    SPECIAL_CODE,
                    "TEXT",
                    special_value,
                    "Synthetic",
                )
            ]
        )
    original = (
        f'<p data-value="{SPECIAL_CODE}">{SPECIAL_CODE}</p>'
        f'<script>const value = "{SPECIAL_CODE}";</script>'
    )
    source = tmp_path / "special.html"
    source.write_text(original, encoding="utf-8", newline="")
    destination = tmp_path / "special-restored.html"

    restore_html(inspect_html(source), destination, repository)

    assert destination.read_text(encoding="utf-8") == original.replace(
        SPECIAL_CODE, special_value
    )


def test_restoration_does_not_replace_partial_codes_or_cross_chunk_boundaries(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    prefix = "x" * (64 * 1024 - 10)
    source = tmp_path / "boundary.html"
    source.write_text(
        f"{prefix} {CPF_CODE} X{CPF_CODE} {CPF_CODE}Z", encoding="utf-8"
    )
    destination = tmp_path / "output.html"

    restore_html(inspect_html(source), destination, repository)

    output = destination.read_text(encoding="utf-8")
    assert f" {CPF_ORIGINAL} " in output
    assert f"X{CPF_CODE}" in output
    assert f"{CPF_CODE}Z" in output


def test_first_original_and_canonical_representation(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CPF_CODE}</p>", encoding="utf-8")
    inspection = inspect_html(source)
    first = tmp_path / "first.html"
    canonical = tmp_path / "canonical.html"

    restore_html(inspection, first, repository)
    restore_html(
        inspection,
        canonical,
        repository,
        representation_policy=RepresentationPolicy.CANONICAL,
    )

    assert CPF_ORIGINAL in first.read_text(encoding="utf-8")
    assert "12345678900" in canonical.read_text(encoding="utf-8")


def test_missing_code_keep_and_abort_policies(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{MISSING_CODE}</p>", encoding="utf-8")
    inspection = inspect_html(source)
    kept = tmp_path / "kept.html"

    restore_html(inspection, kept, repository)
    assert MISSING_CODE in kept.read_text(encoding="utf-8")

    aborted = tmp_path / "aborted.html"
    with pytest.raises(HTMLMissingCodeError) as raised:
        restore_html(
            inspection,
            aborted,
            repository,
            missing_code_policy=HTMLMissingCodePolicy.ABORT,
        )
    assert not aborted.exists()
    assert not list(tmp_path.glob(".aborted.html.*.tmp"))
    assert MISSING_CODE not in str(raised.value)


@pytest.mark.parametrize(
    ("encoding", "writer_encoding", "bom"),
    [
        ("utf-8", "utf-8", False),
        ("utf-8-sig", "utf-8-sig", True),
        ("windows-1252", "cp1252", False),
    ],
)
def test_supported_encoding_and_bom_are_preserved(
    tmp_path: Path, encoding: str, writer_encoding: str, bom: bool
) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / f"{encoding}.html"
    marker = " restauração" if encoding == "windows-1252" else ""
    source.write_text(
        f"<p>{CPF_CODE}</p>{marker}", encoding=writer_encoding
    )
    destination = tmp_path / f"{encoding}-restored.html"
    inspection = inspect_html(source)

    result = restore_html(inspection, destination, repository)

    assert inspection.encoding == encoding
    assert result.encoding == encoding
    assert destination.read_bytes().startswith(codecs.BOM_UTF8) is bom
    assert CPF_ORIGINAL in destination.read_text(encoding=writer_encoding)


def test_same_path_and_existing_destination_are_blocked(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CPF_CODE}</p>", encoding="utf-8")
    inspection = inspect_html(source)
    with pytest.raises(HTMLRestorationError):
        restore_html(inspection, source, repository)
    existing = tmp_path / "existing.html"
    existing.write_text("preserve", encoding="utf-8")
    with pytest.raises(HTMLRestorationError):
        restore_html(inspection, existing, repository)
    assert existing.read_text(encoding="utf-8") == "preserve"


def test_successful_restoration_uses_temporary_file(
    tmp_path: Path, monkeypatch
) -> None:
    restorer_module = importlib.import_module(
        "data_mask_studio.html_restoration.restorer"
    )
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CPF_CODE}</p>", encoding="utf-8")
    destination = tmp_path / "output.html"
    original_factory = restorer_module.tempfile.NamedTemporaryFile
    temporary_names: list[str] = []

    def recording_factory(*args, **kwargs):
        temporary_file = original_factory(*args, **kwargs)
        temporary_names.append(temporary_file.name)
        return temporary_file

    monkeypatch.setattr(
        restorer_module.tempfile, "NamedTemporaryFile", recording_factory
    )
    restore_html(inspect_html(source), destination, repository)

    assert len(temporary_names) == 1
    assert Path(temporary_names[0]).parent == destination.parent
    assert Path(temporary_names[0]).suffix == ".tmp"
    assert not Path(temporary_names[0]).exists()


def test_cancellation_removes_temporary_and_does_not_publish(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CPF_CODE}</p>", encoding="utf-8")
    destination = tmp_path / "output.html"

    with pytest.raises(HTMLRestorationCancelled):
        restore_html(
            inspect_html(source),
            destination,
            repository,
            should_cancel=lambda: True,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".output.html.*.tmp"))


def test_wrong_key_fails_safely_without_sensitive_data(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CPF_CODE}</p>", encoding="utf-8")
    wrong_key_repository = VaultRepository(
        repository.database_path, VaultCipher(b"W" * 32), read_only=True
    )
    destination = tmp_path / "output.html"

    with pytest.raises(HTMLRestorationSecurityError) as raised:
        restore_html(inspect_html(source), destination, wrong_key_repository)

    visible_error = str(raised.value)
    assert CPF_ORIGINAL not in visible_error
    assert KEY.hex() not in visible_error
    assert not destination.exists()


def test_tampered_encrypted_value_fails_safely_and_cleans_output(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    with sqlite3.connect(repository.database_path) as connection:
        encrypted = connection.execute(
            "SELECT encrypted_value FROM vault_variations WHERE code = ?",
            (CPF_CODE,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE vault_variations SET encrypted_value = ? WHERE code = ?",
            (bytes([encrypted[0] ^ 1]) + encrypted[1:], CPF_CODE),
        )
    source = tmp_path / "input.html"
    source.write_text(f"<p>{CPF_CODE}</p>", encoding="utf-8")
    destination = tmp_path / "output.html"

    with pytest.raises(HTMLRestorationSecurityError) as raised:
        restore_html(inspect_html(source), destination, repository)

    assert CPF_ORIGINAL not in str(raised.value)
    assert not destination.exists()
    assert not list(tmp_path.glob(".output.html.*.tmp"))


def test_output_name_suggestion() -> None:
    assert suggested_html_output_path("dashboard_anonimizado.html").name == (
        "dashboard_restaurado.html"
    )
    assert suggested_html_output_path("painel.html").name == "painel_restaurado.html"
    assert suggested_html_output_path("painel.htm").name == "painel_restaurado.htm"


def test_readme_mentions_html_restoration_compactly() -> None:
    english = Path("README.md").read_text(encoding="utf-8")
    portuguese = Path("README.pt-BR.md").read_text(encoding="utf-8")
    assert "local HTML files and dashboards" in english
    assert "“Restore HTML” tab" in english
    assert "arquivos HTML e dashboards locais" in portuguese
    assert "aba “Restaurar HTML”" in portuguese
