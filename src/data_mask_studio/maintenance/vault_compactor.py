import hashlib
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from data_mask_studio.environment import guarded

from data_mask_studio.backup import EnvironmentPaths, create_sqlite_snapshot
from data_mask_studio.integrity import IntegrityAuditor, IntegrityStatus
from data_mask_studio.maintenance.exceptions import (
    MaintenanceCancelled,
    MaintenanceError,
)
from data_mask_studio.maintenance.models import CompactionResult
from data_mask_studio.maintenance.compaction_messages import (
    CompactionExplanation, CompactionPhase, CompactionReason,
)
from data_mask_studio.security import KeyProvider
from data_mask_studio.vault.database import SCHEMA_VERSION

CancellationCheck = Callable[[], bool]
ProgressCallback = Callable[[str, bool], None]
PhaseCallback = Callable[[CompactionPhase, bool], None]


class VaultCompactor:
    def __init__(
        self,
        paths: EnvironmentPaths,
        hmac_key_provider: KeyProvider,
        vault_key_provider: KeyProvider,
    ) -> None:
        self._paths = paths
        self._auditor = IntegrityAuditor(
            paths, hmac_key_provider, vault_key_provider
        )

    @guarded(lambda self, **kwargs: self._paths.directory, exclusive=True)
    def compact(
        self,
        *,
        should_cancel: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
        phase_callback: PhaseCallback | None = None,
    ) -> CompactionResult:
        cancelled = should_cancel or (lambda: False)
        progress = progress_callback or (lambda _stage, _allowed: None)

        def report(message: str, allowed: bool, phase: CompactionPhase) -> None:
            progress(message, allowed)
            if phase_callback is not None:
                phase_callback(phase, allowed)

        database = self._paths.vault_database_path
        if not database.is_file():
            raise MaintenanceError("O cofre local não foi encontrado.",
                                   explanation=CompactionExplanation(CompactionReason.VAULT_MISSING))
        # Keep encrypted recovery evidence under the per-user DMS environment,
        # not in the general OS temp directory that may be cleaned independently.
        temporary_root = Path(tempfile.mkdtemp(
            prefix="dms-vault-compaction-", dir=self._paths.directory,
        ))
        snapshot = temporary_root / "vault-snapshot.db"
        compacted = temporary_root / "vault-compacted.db"
        replaced = False
        retain_evidence = False
        size_before = database.stat().st_size
        expected_digest: bytes | None = None
        try:
            report("Executando checkpoint do SQLite", True, CompactionPhase.CHECKPOINT)
            _raise_if_cancelled(cancelled)
            _checkpoint(database)
            size_before = database.stat().st_size

            report("Criando snapshot de segurança", True, CompactionPhase.SNAPSHOT)
            create_sqlite_snapshot(database, snapshot)
            _validate_database(snapshot)
            expected_digest = _logical_digest(snapshot)
            _raise_if_cancelled(cancelled)

            report("Validando o cofre atual", True, CompactionPhase.VALIDATING)
            _validate_database(database)
            if _logical_digest(database) != expected_digest:
                raise MaintenanceError(
                    "O snapshot de segurança não corresponde ao cofre atual.",
                    explanation=CompactionExplanation(CompactionReason.SNAPSHOT_MISMATCH),
                )

            report("Gerando banco compactado", True, CompactionPhase.COMPACTING)
            _vacuum_into(database, compacted, cancelled)
            _raise_if_cancelled(cancelled)
            _validate_database(compacted)
            if _logical_digest(compacted) != expected_digest:
                raise MaintenanceError(
                    "O banco compactado não preservou o conteúdo lógico.",
                    explanation=CompactionExplanation(CompactionReason.CONTENT_CHANGED),
                )

            report("Substituindo o cofre com segurança", False, CompactionPhase.REPLACING)
            replaced = True
            _copy_database(compacted, database, compact_destination=True)
            _optimize(database)
            _validate_database(database)
            if _logical_digest(database) != expected_digest:
                raise MaintenanceError(
                    "A validação posterior à compactação falhou.",
                    explanation=CompactionExplanation(CompactionReason.POST_VALIDATION_FAILED),
                )

            report("Executando auditoria posterior", False, CompactionPhase.AUDITING)
            audit = self._auditor.run()
            if audit.status is IntegrityStatus.FAILURE:
                raise MaintenanceError(
                    "A auditoria posterior à compactação encontrou uma falha.",
                    explanation=CompactionExplanation(CompactionReason.POST_AUDIT_FAILED),
                )
            size_after = database.stat().st_size
            return CompactionResult(
                size_before,
                size_after,
                max(0, size_before - size_after),
                audit,
            )
        except MaintenanceCancelled:
            raise
        except Exception as error:
            explanation = CompactionExplanation(CompactionReason.COMPACTION_FAILED)
            if replaced:
                try:
                    _restore_snapshot(database, snapshot)
                except Exception:
                    retain_evidence = True
                    # The explicit cause preserves the original error; Python's
                    # exception context preserves the rollback failure as well.
                    raise MaintenanceError(
                        "A compactação falhou e a restauração do original também falhou. "
                        "Não utilize o cofre até revisão manual. A pasta de recuperação "
                        "foi preservada no diretório local do aplicativo: "
                        f"{temporary_root.name} (snapshot: vault-snapshot.db).",
                        explanation=CompactionExplanation(CompactionReason.ROLLBACK_FAILED,
                                                          recovery_directory=temporary_root.name),
                    ) from error
                replaced = False
                explanation = replace(explanation, rollback_completed=True)
            if isinstance(error, MaintenanceError):
                if explanation.rollback_completed:
                    original = getattr(error, "explanation", None)
                    if original is None or isinstance(original, CompactionExplanation):
                        error.explanation = replace(original or CompactionExplanation(), rollback_completed=True)
                raise
            raise MaintenanceError(
                "Não foi possível compactar o cofre com segurança.", explanation=explanation,
            ) from error
        finally:
            if not retain_evidence:
                shutil.rmtree(temporary_root, ignore_errors=True)


def _checkpoint(database: Path) -> None:
    try:
        connection = sqlite3.connect(database)
        try:
            result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result is not None and int(result[0]) != 0:
                raise MaintenanceError("O cofre está ocupado por outra operação.",
                                       explanation=CompactionExplanation(CompactionReason.VAULT_BUSY))
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise MaintenanceError("Não foi possível concluir o checkpoint do cofre.",
                               explanation=CompactionExplanation(CompactionReason.CHECKPOINT_FAILED)) from error


def _vacuum_into(
    database: Path, destination: Path, should_cancel: CancellationCheck
) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database)
        connection.set_progress_handler(lambda: 1 if should_cancel() else 0, 1000)
        quoted = str(destination.resolve()).replace("'", "''")
        connection.execute(f"VACUUM INTO '{quoted}'")
    except sqlite3.OperationalError as error:
        if should_cancel() or "interrupted" in str(error).casefold():
            raise MaintenanceCancelled(
                "A compactação foi cancelada antes da substituição do cofre.",
                explanation=CompactionExplanation(CompactionReason.CANCELLED),
            ) from error
        raise MaintenanceError("Não foi possível gerar o banco compactado.",
                               explanation=CompactionExplanation(CompactionReason.VACUUM_FAILED)) from error
    except sqlite3.Error as error:
        raise MaintenanceError("Não foi possível gerar o banco compactado.",
                               explanation=CompactionExplanation(CompactionReason.VACUUM_FAILED)) from error
    finally:
        if connection is not None:
            connection.set_progress_handler(None, 0)
            connection.close()


def _validate_database(database: Path) -> None:
    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro&immutable=1", uri=True
        )
        try:
            integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if integrity != ["ok"] or version != SCHEMA_VERSION:
                raise MaintenanceError("O banco não passou na validação estrutural.",
                                       explanation=CompactionExplanation(CompactionReason.STRUCTURE_INVALID))
            connection.execute("SELECT COUNT(*) FROM vault_mappings").fetchone()
            connection.execute("SELECT COUNT(*) FROM vault_variations").fetchone()
        finally:
            connection.close()
    except MaintenanceError:
        raise
    except sqlite3.Error as error:
        raise MaintenanceError("O banco não pôde ser validado.",
                               explanation=CompactionExplanation(CompactionReason.DATABASE_INVALID)) from error


def _logical_digest(database: Path) -> bytes:
    digest = hashlib.sha256()
    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro&immutable=1", uri=True
        )
        try:
            for query in (
                "SELECT code, prefix, canonical_encrypted_value, canonical_nonce, "
                "source_header, normalization_rule, first_seen, last_seen, "
                "total_occurrences FROM vault_mappings ORDER BY code",
                "SELECT identifier, code, encrypted_value, nonce, normalization_rule, "
                "first_seen, last_seen, occurrence_count FROM vault_variations "
                "ORDER BY identifier",
            ):
                for row in connection.execute(query):
                    for value in row:
                        encoded = (
                            bytes(value)
                            if isinstance(value, bytes)
                            else str(value).encode("utf-8")
                        )
                        digest.update(len(encoded).to_bytes(8, "big"))
                        digest.update(encoded)
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise MaintenanceError("Não foi possível comparar o conteúdo do cofre.",
                               explanation=CompactionExplanation(CompactionReason.COMPARISON_FAILED)) from error
    return digest.digest()


def _optimize(database: Path) -> None:
    try:
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA optimize")
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise MaintenanceError("Não foi possível otimizar o banco compactado.",
                               explanation=CompactionExplanation(CompactionReason.OPTIMIZE_FAILED)) from error


def _restore_snapshot(database: Path, snapshot: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        auxiliary = Path(f"{database}{suffix}")
        if auxiliary.exists():
            try:
                auxiliary.unlink()
            except OSError:
                pass
    if snapshot.exists():
        _copy_database(snapshot, database)


def _copy_database(
    source: Path, destination: Path, *, compact_destination: bool = False
) -> None:
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"{source.resolve().as_uri()}?mode=ro&immutable=1", uri=True
        )
        destination_connection = sqlite3.connect(destination)
        source_connection.backup(destination_connection, pages=128)
        destination_connection.commit()
        if compact_destination:
            destination_connection.execute("VACUUM")
    except sqlite3.Error as error:
        raise MaintenanceError(
            "Não foi possível substituir o cofre de forma transacional.",
            explanation=CompactionExplanation(CompactionReason.REPLACEMENT_FAILED),
        ) from error
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
    if compact_destination:
        _checkpoint(destination)


def _raise_if_cancelled(should_cancel: CancellationCheck) -> None:
    if should_cancel():
        raise MaintenanceCancelled(
            "A compactação foi cancelada antes da substituição do cofre.",
            explanation=CompactionExplanation(CompactionReason.CANCELLED),
        )
