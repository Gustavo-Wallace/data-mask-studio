import hashlib
import sqlite3
from pathlib import Path

import pytest

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.integrity import (
    IntegrityAuditor,
    IntegrityCancelled,
    IntegrityStatus,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

HMAC_KEY = b"H" * 32
VAULT_KEY = b"V" * 32
CANONICAL = "12345678900"
ORIGINAL = "123.456.789-00"


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
        directory=directory,
        hmac_key_path=directory / "secret.key",
        vault_key_path=directory / "vault_key.dpapi",
        vault_database_path=directory / "vault.db",
        profiles_path=directory / "profiles.json",
    )


def prepare_valid_environment(
    tmp_path: Path,
) -> tuple[EnvironmentPaths, VaultRepository, str]:
    paths = paths_for(tmp_path)
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    code = TokenGenerator(HMAC_KEY).generate("CPF", CANONICAL)
    candidate = MappingCandidate(
        code,
        "CPF",
        ORIGINAL,
        "CPF do cliente",
        canonical_value=CANONICAL,
        normalization_rule=NormalizationRule.CPF,
    )
    with repository.transaction() as transaction:
        transaction.upsert_batch([candidate])
    return paths, repository, code


def auditor(paths: EnvironmentPaths) -> IntegrityAuditor:
    return IntegrityAuditor(
        paths,
        FixedKeyProvider(HMAC_KEY),
        FixedKeyProvider(VAULT_KEY),
    )


def check(report, name: str):
    return next(item for item in report.checks if item.check_type == name)


def database_hashes(paths: EnvironmentPaths) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths.directory.glob("vault.db*")
    }


def test_valid_environment_is_intact_and_audit_is_read_only(tmp_path: Path) -> None:
    paths, _, _ = prepare_valid_environment(tmp_path)
    before = database_hashes(paths)
    progress: list[tuple[int, int]] = []

    report = auditor(paths).run(progress_callback=lambda done, total: progress.append((done, total)))

    assert report.status is IntegrityStatus.INTACT
    assert report.schema_version == 3
    assert check(report, "Integridade do SQLite").status is IntegrityStatus.INTACT
    assert check(report, "Recomputação dos tokens").failures == 0
    assert database_hashes(paths) == before
    assert progress[-1] == (12, 12)


def test_inconsistent_token_is_detected(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    wrong_code = TokenGenerator(b"X" * 32).generate("CPF", CANONICAL)
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    wrong_code,
                    "CPF",
                    ORIGINAL,
                    "CPF",
                    canonical_value=CANONICAL,
                    normalization_rule=NormalizationRule.CPF,
                )
            ]
        )

    result = check(auditor(paths).run(), "Recomputação dos tokens")

    assert result.status is IntegrityStatus.FAILURE
    assert result.failures == 1
    assert wrong_code not in result.identifiers


def test_incompatible_variation_and_counter_are_detected(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    code = TokenGenerator(HMAC_KEY).generate("ID", "123")
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    code,
                    "ID",
                    "different",
                    "Identificador",
                    canonical_value="123",
                    normalization_rule=NormalizationRule.EXACT,
                )
            ]
        )
    with sqlite3.connect(paths.vault_database_path) as connection:
        connection.execute(
            "UPDATE vault_mappings SET total_occurrences = total_occurrences + 2"
        )

    report = auditor(paths).run()

    assert check(report, "Compatibilidade das variações").failures == 1
    assert check(report, "Contadores de ocorrências").failures == 1


def test_invalid_reference_is_detected(tmp_path: Path) -> None:
    paths, _, _ = prepare_valid_environment(tmp_path)
    with sqlite3.connect(paths.vault_database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("UPDATE vault_variations SET code = 'ORPHAN-ABCDEFGHI234'")

    result = check(auditor(paths).run(), "Referências entre tabelas")

    assert result.status is IntegrityStatus.FAILURE
    assert result.failures >= 1


def test_invalid_profile_and_unavailable_key_are_reported(tmp_path: Path) -> None:
    paths, _, _ = prepare_valid_environment(tmp_path)
    paths.profiles_path.write_text('{"schema_version": 999}', encoding="utf-8")
    integrity_auditor = IntegrityAuditor(
        paths,
        UnavailableKeyProvider(),
        FixedKeyProvider(VAULT_KEY),
    )

    report = integrity_auditor.run()

    assert check(report, "Disponibilidade da chave HMAC").status is IntegrityStatus.FAILURE
    assert check(report, "Validade estrutural dos perfis").status is IntegrityStatus.FAILURE


def test_tampered_encrypted_content_is_detected(tmp_path: Path) -> None:
    paths, _, _ = prepare_valid_environment(tmp_path)
    with sqlite3.connect(paths.vault_database_path) as connection:
        value = connection.execute(
            "SELECT canonical_encrypted_value FROM vault_mappings"
        ).fetchone()[0]
        tampered = bytes([value[0] ^ 1]) + bytes(value[1:])
        connection.execute(
            "UPDATE vault_mappings SET canonical_encrypted_value = ?", (tampered,)
        )

    result = check(auditor(paths).run(), "Autenticação AES-GCM")

    assert result.status is IntegrityStatus.FAILURE
    assert result.failures >= 1


def test_corrupt_sqlite_is_reported_as_integrity_failure(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    paths.vault_database_path.write_bytes(b"not-a-sqlite-database")

    report = auditor(paths).run()

    assert check(report, "Integridade do SQLite").status is IntegrityStatus.FAILURE


def test_safe_report_never_contains_secrets_or_complete_codes(tmp_path: Path) -> None:
    paths, _, code = prepare_valid_environment(tmp_path)

    text = auditor(paths).run().to_safe_text()

    assert ORIGINAL not in text
    assert CANONICAL not in text
    assert code not in text
    assert HMAC_KEY.hex() not in text
    assert VAULT_KEY.hex() not in text
    assert "nonce" not in text.casefold()


def test_abandoned_temporary_file_requires_attention(tmp_path: Path) -> None:
    paths, _, _ = prepare_valid_environment(tmp_path)
    (paths.directory / ".secret-abandoned.tmp").write_bytes(b"local")

    result = check(auditor(paths).run(), "Arquivos temporários abandonados")

    assert result.status is IntegrityStatus.ATTENTION
    assert result.failures == 1


def test_audit_can_be_cancelled_without_modifying_vault(tmp_path: Path) -> None:
    paths, _, _ = prepare_valid_environment(tmp_path)
    before = database_hashes(paths)

    with pytest.raises(IntegrityCancelled):
        auditor(paths).run(should_cancel=lambda: True)

    assert database_hashes(paths) == before
