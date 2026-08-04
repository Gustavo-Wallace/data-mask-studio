import os
import sqlite3
import time
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnConfig, TokenGenerator
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.maintenance import (
    MaintenanceCancelled,
    MaintenanceDiagnostics,
    MaintenanceError,
    MaintenanceStatus,
    VaultCompactor,
    cleanup_temporaries,
    locate_temporaries,
    safe_diagnostic_report,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
import data_mask_studio.maintenance.temporary_cleanup as cleanup_module
import data_mask_studio.maintenance.vault_compactor as compactor_module

HMAC_KEY = b"H" * 32
VAULT_KEY = b"V" * 32
SECRET_VALUE = "123.456.789-00"
CANONICAL_VALUE = "12345678900"


class FixedKeyProvider:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def get_key(self) -> bytes:
        return self.key


class UnavailableKeyProvider:
    def get_key(self) -> bytes:
        raise RuntimeError("unavailable")


def paths_for(tmp_path: Path) -> EnvironmentPaths:
    directory = tmp_path / "local"
    directory.mkdir()
    return EnvironmentPaths(
        directory,
        directory / "secret.key",
        directory / "vault_key.dpapi",
        directory / "vault.db",
        directory / "profiles.json",
    )


def prepare_environment(tmp_path: Path, *, bloat: bool = False) -> tuple[EnvironmentPaths, VaultRepository, str]:
    paths = paths_for(tmp_path)
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    code = TokenGenerator(HMAC_KEY).generate("CPF", CANONICAL_VALUE)
    second_code = TokenGenerator(HMAC_KEY).generate("NOME", "Ana")
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    code,
                    "CPF",
                    SECRET_VALUE,
                    "CPF",
                    occurrences=2,
                    canonical_value=CANONICAL_VALUE,
                    normalization_rule=NormalizationRule.CPF,
                ),
                MappingCandidate(
                    second_code,
                    "NOME",
                    "Ana",
                    "Nome",
                    canonical_value="Ana",
                    normalization_rule=NormalizationRule.EXACT,
                ),
            ]
        )
    ProfileService(ProfileRepository(paths.profiles_path)).create(
        "Perfil", [ColumnConfig("CPF", True, "CPF", NormalizationRule.CPF)]
    )
    if bloat:
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("CREATE TABLE disposable (payload BLOB)")
            connection.executemany(
                "INSERT INTO disposable VALUES (?)",
                [(os.urandom(4096),) for _ in range(100)],
            )
            connection.execute("DROP TABLE disposable")
    return paths, repository, code


def diagnostics(paths: EnvironmentPaths) -> MaintenanceDiagnostics:
    return MaintenanceDiagnostics(
        paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)
    )


def test_healthy_diagnostic_has_correct_aggregated_statistics(tmp_path: Path) -> None:
    paths, _, _ = prepare_environment(tmp_path)

    result = diagnostics(paths).run()

    assert result.status is MaintenanceStatus.HEALTHY
    assert result.statistics.schema_version == 3
    assert result.statistics.mapping_count == 2
    assert result.statistics.variation_count == 2
    assert result.statistics.total_occurrences == 3
    assert result.statistics.profile_count == 1
    assert result.statistics.prefix_count == 2
    assert dict(result.statistics.normalization_distribution) == {"cpf": 1, "exact": 1}
    assert result.statistics.first_entry is not None
    assert result.statistics.last_entry is not None


def test_diagnostic_attention_for_missing_database(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)

    result = diagnostics(paths).run()

    assert result.status is MaintenanceStatus.ATTENTION
    assert result.statistics.mapping_count == 0


def test_diagnostic_failure_for_unavailable_key_and_invalid_profile(tmp_path: Path) -> None:
    paths, _, _ = prepare_environment(tmp_path)
    paths.profiles_path.write_text('{"invalid": true}', encoding="utf-8")
    service = MaintenanceDiagnostics(
        paths, UnavailableKeyProvider(), FixedKeyProvider(VAULT_KEY)
    )

    result = service.run()

    assert result.status is MaintenanceStatus.FAILURE
    assert any(check.failures for check in result.audit.checks)


def test_safe_report_does_not_contain_values_or_complete_codes(tmp_path: Path) -> None:
    paths, _, code = prepare_environment(tmp_path)

    report = safe_diagnostic_report(diagnostics(paths).run())

    assert SECRET_VALUE not in report
    assert CANONICAL_VALUE not in report
    assert code not in report
    assert HMAC_KEY.hex() not in report
    assert VAULT_KEY.hex() not in report


def test_locate_and_cleanup_only_known_old_temporaries(tmp_path: Path) -> None:
    local = tmp_path / "local"
    output = tmp_path / "output"
    local.mkdir()
    output.mkdir()
    temporary = local / ".profile-123.tmp"
    temporary.write_bytes(b"temporary")
    directory = output / ".dms-restore-old"
    directory.mkdir()
    (directory / "payload").write_bytes(b"temporary")
    final_csv = output / "dados_restaurado.csv"
    final_csv.write_text("final", encoding="utf-8")
    backup = local / "backup.dmsbackup"
    backup.write_bytes(b"backup")
    old = time.time() - 7200
    os.utime(temporary, (old, old))
    os.utime(directory, (old, old))

    items = locate_temporaries(local, [output])
    for item in items:
        item.selected = True
    result = cleanup_temporaries(items, local, [output])

    assert result.removed == 2
    assert not temporary.exists()
    assert not directory.exists()
    assert final_csv.is_file()
    assert backup.is_file()


def test_recent_or_in_use_temporary_is_preserved(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local"
    local.mkdir()
    recent = local / ".recent.tmp"
    recent.write_bytes(b"recent")
    old_locked = local / ".locked.tmp"
    old_locked.write_bytes(b"locked")
    old = time.time() - 7200
    os.utime(old_locked, (old, old))
    monkeypatch.setattr(
        cleanup_module,
        "_probably_in_use",
        lambda path: path.name == ".locked.tmp",
    )

    items = locate_temporaries(local)
    for item in items:
        item.selected = True
    result = cleanup_temporaries(items, local)

    assert result.removed == 0
    assert recent.exists() and old_locked.exists()


def test_compaction_preserves_logical_content_and_reduces_bloat(tmp_path: Path) -> None:
    paths, repository, code = prepare_environment(tmp_path, bloat=True)
    before = repository.get_decrypted_mapping(code)

    result = VaultCompactor(
        paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)
    ).compact()

    after = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).get_decrypted_mapping(code)
    assert after == before
    assert result.size_after < result.size_before
    assert result.recovered_bytes == result.size_before - result.size_after
    assert not list(paths.directory.glob(".vault.db.*.tmp"))


def test_compaction_rolls_back_original_after_post_swap_failure(
    tmp_path: Path, monkeypatch
) -> None:
    paths, repository, code = prepare_environment(tmp_path, bloat=True)
    expected = repository.get_decrypted_mapping(code)

    def fail(_database: Path) -> None:
        raise MaintenanceError("forced")

    monkeypatch.setattr(compactor_module, "_optimize", fail)
    with pytest.raises(MaintenanceError):
        VaultCompactor(
            paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)
        ).compact()

    restored = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).get_decrypted_mapping(code)
    assert restored == expected
    assert not list(paths.directory.glob(".vault.db.*.tmp"))


def test_compaction_cancellation_preserves_original(tmp_path: Path) -> None:
    paths, repository, code = prepare_environment(tmp_path, bloat=True)
    expected = repository.get_decrypted_mapping(code)

    with pytest.raises(MaintenanceCancelled):
        VaultCompactor(
            paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)
        ).compact(should_cancel=lambda: True)

    restored = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).get_decrypted_mapping(code)
    assert restored == expected
    assert not list(paths.directory.glob(".vault.db.*.tmp"))
