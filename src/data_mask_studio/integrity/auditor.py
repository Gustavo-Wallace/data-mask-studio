import hashlib
import hmac
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.integrity.exceptions import IntegrityCancelled, IntegrityError
from data_mask_studio.integrity.models import AuditReport, CheckResult, IntegrityStatus
from data_mask_studio.normalization import NormalizationError, NormalizationRule, normalize_value
from data_mask_studio.profiles import ProfileError, ProfileRepository
from data_mask_studio.security import KeyProvider
from data_mask_studio.security.key_provider import KEY_SIZE
from data_mask_studio.vault import VaultCipher, VaultEncryptionError
from data_mask_studio.vault.database import SCHEMA_VERSION, connect_read_only

ProgressCallback = Callable[[int, int], None]
CancellationCheck = Callable[[], bool]
_CURRENT_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,23}-[A-Z2-7]{12}$")
_LEGACY_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,23}-[A-F0-9]{24}$")
_EXPECTED_COLUMNS = {
    "vault_mappings": {
        "code",
        "prefix",
        "canonical_encrypted_value",
        "canonical_nonce",
        "source_header",
        "normalization_rule",
        "first_seen",
        "last_seen",
        "total_occurrences",
    },
    "vault_variations": {
        "identifier",
        "code",
        "encrypted_value",
        "nonce",
        "normalization_rule",
        "first_seen",
        "last_seen",
        "occurrence_count",
    },
}


class IntegrityAuditor:
    def __init__(
        self,
        paths: EnvironmentPaths,
        hmac_key_provider: KeyProvider,
        vault_key_provider: KeyProvider,
    ) -> None:
        self._paths = paths
        self._hmac_key_provider = hmac_key_provider
        self._vault_key_provider = vault_key_provider

    def run(
        self,
        *,
        should_cancel: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> AuditReport:
        started_at = datetime.now(timezone.utc)
        cancelled = should_cancel or (lambda: False)
        progress = progress_callback or (lambda _completed, _total: None)
        checks: list[CheckResult] = []
        total_steps = 12

        hmac_key = self._check_key(
            "Disponibilidade da chave HMAC",
            self._hmac_key_provider,
            checks,
        )
        progress(1, total_steps)
        self._raise_if_cancelled(cancelled)
        vault_key = self._check_key(
            "Disponibilidade da chave AES",
            self._vault_key_provider,
            checks,
        )
        progress(2, total_steps)
        self._raise_if_cancelled(cancelled)

        schema_version: int | None = None
        if not self._paths.vault_database_path.is_file():
            checks.extend(self._missing_database_checks())
        else:
            database_checks, schema_version = self._audit_database(
                hmac_key,
                vault_key,
                cancelled,
                lambda completed: progress(completed, total_steps),
            )
            checks.extend(database_checks)

        self._raise_if_cancelled(cancelled)
        checks.append(self._check_profiles())
        progress(11, total_steps)
        self._raise_if_cancelled(cancelled)
        checks.append(self._check_temporary_files())
        progress(12, total_steps)
        return AuditReport(
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            database_path=self._paths.vault_database_path,
            profiles_path=self._paths.profiles_path,
            schema_version=schema_version,
            checks=tuple(checks),
        )

    def _check_key(
        self,
        name: str,
        provider: KeyProvider,
        checks: list[CheckResult],
    ) -> bytes | None:
        key_path = getattr(provider, "key_path", None)
        if key_path is not None and not Path(key_path).is_file():
            checks.append(_failed(name, "A chave local não foi encontrada."))
            return None
        try:
            key = provider.get_key()
            if len(key) != KEY_SIZE:
                raise ValueError
        except Exception:
            checks.append(_failed(name, "A chave local não está acessível."))
            return None
        checks.append(_intact(name, 1, "A chave está acessível e possui formato válido."))
        return key

    def _audit_database(
        self,
        hmac_key: bytes | None,
        vault_key: bytes | None,
        should_cancel: CancellationCheck,
        progress: Callable[[int], None],
    ) -> tuple[list[CheckResult], int | None]:
        checks: list[CheckResult] = []
        connection: sqlite3.Connection | None = None
        snapshot_directory: tempfile.TemporaryDirectory[str] | None = None
        try:
            snapshot_directory = tempfile.TemporaryDirectory(
                prefix="data-mask-studio-integrity-"
            )
            snapshot_path = Path(snapshot_directory.name) / "vault.db"
            for suffix in ("", "-wal", "-shm"):
                source = Path(f"{self._paths.vault_database_path}{suffix}")
                if source.is_file():
                    shutil.copy2(source, Path(f"{snapshot_path}{suffix}"))
            connection = connect_read_only(snapshot_path)
            connection.set_progress_handler(lambda: 1 if should_cancel() else 0, 1000)
            integrity_rows = [
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            ]
            if integrity_rows == ["ok"]:
                checks.append(_intact("Integridade do SQLite", 1, "PRAGMA integrity_check retornou ok."))
            else:
                checks.append(
                    _failed(
                        "Integridade do SQLite",
                        "O SQLite informou inconsistências internas.",
                        failures=len(integrity_rows),
                    )
                )
            progress(3)
            self._raise_if_cancelled(should_cancel)

            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            schema_failures = self._schema_failures(connection, schema_version)
            checks.append(
                _failed(
                    "Versão e estrutura do esquema",
                    "O esquema não corresponde à versão esperada.",
                    failures=schema_failures,
                )
                if schema_failures
                else _intact(
                    "Versão e estrutura do esquema",
                    2,
                    f"Esquema {schema_version} validado.",
                )
            )
            progress(4)
            self._raise_if_cancelled(should_cancel)

            rows = connection.execute(
                "SELECT code, prefix, canonical_encrypted_value, canonical_nonce, "
                "source_header, normalization_rule, total_occurrences "
                "FROM vault_mappings ORDER BY code"
            ).fetchall()
            variations = connection.execute(
                "SELECT v.identifier, v.code, v.encrypted_value, v.nonce, "
                "v.normalization_rule, v.occurrence_count, m.prefix, m.source_header "
                "FROM vault_variations AS v LEFT JOIN vault_mappings AS m "
                "ON m.code = v.code ORDER BY v.identifier"
            ).fetchall()
            foreign_key_failures = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            data_checks = self._check_records(
                rows,
                variations,
                hmac_key,
                vault_key,
                should_cancel,
                foreign_key_failures,
            )
            checks.extend(data_checks)
            for completed in range(5, 11):
                progress(completed)
            return checks, schema_version
        except sqlite3.OperationalError as error:
            if should_cancel() or "interrupted" in str(error).casefold():
                raise IntegrityCancelled("A verificação de integridade foi cancelada.") from error
            return self._database_failure_checks(), None
        except (sqlite3.Error, OSError, ValueError):
            return self._database_failure_checks(), None
        finally:
            if connection is not None:
                connection.set_progress_handler(None, 0)
                connection.close()
            if snapshot_directory is not None:
                snapshot_directory.cleanup()

    def _schema_failures(
        self, connection: sqlite3.Connection, schema_version: int
    ) -> int:
        failures = int(schema_version != SCHEMA_VERSION)
        for table, expected in _EXPECTED_COLUMNS.items():
            actual = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            failures += int(actual != expected)
        return failures

    def _check_records(
        self,
        mappings: list[sqlite3.Row],
        variations: list[sqlite3.Row],
        hmac_key: bytes | None,
        vault_key: bytes | None,
        should_cancel: CancellationCheck,
        foreign_key_failures: int,
    ) -> list[CheckResult]:
        cipher = VaultCipher(vault_key) if vault_key is not None else None
        generator = TokenGenerator(hmac_key) if hmac_key is not None else None
        authentication_failures: list[str] = []
        normalization_failures: list[str] = []
        counter_failures: list[str] = []
        code_failures: list[str] = []
        token_failures: list[str] = []
        relationship_failures: list[str] = []
        relationship_failures.extend(
            f"foreign-key#{index}"
            for index in range(1, foreign_key_failures + 1)
        )
        canonical_values: dict[str, str] = {}
        variation_counts: dict[str, int] = {}

        for index, row in enumerate(mappings, start=1):
            self._raise_if_cancelled(should_cancel)
            reference = f"mapping#{index}"
            code = str(row["code"])
            prefix = str(row["prefix"])
            rule_text = str(row["normalization_rule"])
            if not _valid_code(code, prefix):
                code_failures.append(reference)
            if int(row["total_occurrences"]) <= 0:
                counter_failures.append(reference)
            if cipher is None:
                authentication_failures.append(reference)
                continue
            try:
                canonical = cipher.decrypt_mapping(
                    code,
                    prefix,
                    str(row["source_header"]),
                    rule_text,
                    bytes(row["canonical_encrypted_value"]),
                    bytes(row["canonical_nonce"]),
                )
                NormalizationRule(rule_text)
            except (VaultEncryptionError, ValueError):
                authentication_failures.append(reference)
                continue
            canonical_values[code] = canonical
            if generator is None or not _token_matches(
                generator, hmac_key, code, prefix, canonical
            ):
                token_failures.append(reference)

        mapping_metadata = {str(row["code"]): row for row in mappings}
        for row in variations:
            self._raise_if_cancelled(should_cancel)
            identifier = int(row["identifier"])
            reference = f"variation#{identifier}"
            code = str(row["code"])
            mapping = mapping_metadata.get(code)
            if mapping is None or row["prefix"] is None:
                relationship_failures.append(reference)
                continue
            count = int(row["occurrence_count"])
            if count <= 0:
                counter_failures.append(reference)
            variation_counts[code] = variation_counts.get(code, 0) + count
            if cipher is None:
                authentication_failures.append(reference)
                continue
            rule_text = str(row["normalization_rule"])
            try:
                rule = NormalizationRule(rule_text)
                original = cipher.decrypt_variation(
                    identifier,
                    code,
                    str(row["prefix"]),
                    str(row["source_header"]),
                    rule,
                    bytes(row["encrypted_value"]),
                    bytes(row["nonce"]),
                )
            except (VaultEncryptionError, ValueError):
                authentication_failures.append(reference)
                continue
            canonical = canonical_values.get(code)
            if canonical is None:
                continue
            try:
                compatible = hmac.compare_digest(
                    normalize_value(original, rule).encode("utf-8"),
                    canonical.encode("utf-8"),
                )
            except NormalizationError:
                compatible = False
            if not compatible:
                normalization_failures.append(reference)

        for index, row in enumerate(mappings, start=1):
            code = str(row["code"])
            if variation_counts.get(code, 0) != int(row["total_occurrences"]):
                counter_failures.append(f"mapping#{index}")
            if variation_counts.get(code, 0) == 0:
                relationship_failures.append(f"mapping#{index}")

        return [
            _result("Autenticação AES-GCM", len(mappings) + len(variations), authentication_failures),
            _result("Compatibilidade das variações", len(variations), normalization_failures),
            _result("Contadores de ocorrências", len(mappings) + len(variations), counter_failures),
            _result("Referências entre tabelas", len(mappings) + len(variations), relationship_failures),
            _result("Formato dos códigos", len(mappings), code_failures),
            _result("Recomputação dos tokens", len(mappings), token_failures),
        ]

    def _check_profiles(self) -> CheckResult:
        try:
            profiles = ProfileRepository(self._paths.profiles_path).load()
        except ProfileError:
            return _failed("Validade estrutural dos perfis", "O arquivo de perfis é inválido.")
        return _intact(
            "Validade estrutural dos perfis",
            len(profiles),
            f"{len(profiles)} perfil(is) validado(s).",
        )

    def _check_temporary_files(self) -> CheckResult:
        directory = self._paths.directory
        if not directory.is_dir():
            return _intact("Arquivos temporários abandonados", 0, "Nenhum arquivo temporário encontrado.")
        candidates = {
            path.resolve()
            for pattern in ("*.tmp", ".dms-backup-*", ".dms-restore-*")
            for path in directory.glob(pattern)
        }
        if candidates:
            identifiers = tuple(f"temporary#{index}" for index, _ in enumerate(sorted(candidates), 1))
            return CheckResult(
                "Arquivos temporários abandonados",
                IntegrityStatus.ATTENTION,
                len(candidates),
                len(candidates),
                "Foram encontrados artefatos temporários conhecidos.",
                identifiers,
            )
        return _intact("Arquivos temporários abandonados", 0, "Nenhum arquivo temporário encontrado.")

    @staticmethod
    def _raise_if_cancelled(should_cancel: CancellationCheck) -> None:
        if should_cancel():
            raise IntegrityCancelled("A verificação de integridade foi cancelada.")

    @staticmethod
    def _missing_database_checks() -> list[CheckResult]:
        first = CheckResult(
            "Integridade do SQLite",
            IntegrityStatus.ATTENTION,
            0,
            0,
            "O cofre ainda não existe; não há registros para verificar.",
        )
        return [first, *[_unavailable(name) for name in _DATABASE_CHECK_NAMES[1:]]]

    @staticmethod
    def _database_failure_checks() -> list[CheckResult]:
        return [
            _failed(name, "A verificação não pôde ser concluída com segurança.")
            for name in _DATABASE_CHECK_NAMES
        ]


_DATABASE_CHECK_NAMES = (
    "Integridade do SQLite",
    "Versão e estrutura do esquema",
    "Autenticação AES-GCM",
    "Compatibilidade das variações",
    "Contadores de ocorrências",
    "Referências entre tabelas",
    "Formato dos códigos",
    "Recomputação dos tokens",
)


def _valid_code(code: str, prefix: str) -> bool:
    return code.startswith(f"{prefix}-") and bool(
        _CURRENT_CODE.fullmatch(code) or _LEGACY_CODE.fullmatch(code)
    )


def _token_matches(
    generator: TokenGenerator,
    hmac_key: bytes | None,
    code: str,
    prefix: str,
    canonical: str,
) -> bool:
    current = generator.generate(prefix, canonical)
    if hmac.compare_digest(current.encode("utf-8"), code.encode("utf-8")):
        return True
    if hmac_key is None:
        return False
    message = f"{prefix}\0{canonical}".encode("utf-8")
    digest = hmac.new(hmac_key, message, hashlib.sha256).hexdigest()[:24].upper()
    legacy = f"{prefix}-{digest}"
    return hmac.compare_digest(legacy.encode("utf-8"), code.encode("utf-8"))


def _result(name: str, examined: int, failures: list[str]) -> CheckResult:
    unique = tuple(dict.fromkeys(failures))
    if unique:
        return CheckResult(
            name,
            IntegrityStatus.FAILURE,
            examined,
            len(unique),
            "Foram encontradas inconsistências.",
            unique[:10],
        )
    return _intact(name, examined, "Nenhuma inconsistência encontrada.")


def _intact(name: str, examined: int, message: str) -> CheckResult:
    return CheckResult(name, IntegrityStatus.INTACT, examined, 0, message)


def _failed(
    name: str,
    message: str,
    *,
    failures: int = 1,
) -> CheckResult:
    return CheckResult(name, IntegrityStatus.FAILURE, 0, failures, message)


def _unavailable(name: str) -> CheckResult:
    return CheckResult(
        name,
        IntegrityStatus.ATTENTION,
        0,
        0,
        "Verificação não aplicável sem um cofre local.",
    )
