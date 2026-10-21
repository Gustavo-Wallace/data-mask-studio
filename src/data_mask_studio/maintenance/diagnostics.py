import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from data_mask_studio.environment import guarded

from data_mask_studio.backup import BackupError, EnvironmentPaths, create_sqlite_snapshot
from data_mask_studio.backup.models import CancellationRequest
from data_mask_studio.integrity import IntegrityAuditor, IntegrityCancelled, IntegrityStatus
from data_mask_studio.maintenance.exceptions import MaintenanceCancelled
from data_mask_studio.maintenance.models import (
    DiagnosticResult,
    EnvironmentStatistics,
    MaintenanceStatus,
)
from data_mask_studio.profiles import ProfileError, ProfileRepository
from data_mask_studio.security import KeyProvider
from data_mask_studio.vault.database import connect_read_only


_STATISTICS_STEPS = 4


def _raise_if_cancelled(should_cancel) -> None:
    if should_cancel():
        raise MaintenanceCancelled("O diagnóstico foi cancelado.")


class _SnapshotCancellation(CancellationRequest):
    def __init__(self, should_cancel) -> None:
        super().__init__()
        self._should_cancel = should_cancel

    def raise_if_requested(self) -> None:
        _raise_if_cancelled(self._should_cancel)


class MaintenanceDiagnostics:
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

    @guarded(lambda self, **kwargs: self._paths.directory)
    def run(self, *, should_cancel=None, progress_callback=None) -> DiagnosticResult:
        should_cancel = should_cancel or (lambda: False)
        progress = progress_callback or (lambda _done, _total: None)
        audit_steps = 0

        def audit_progress(done, total):
            nonlocal audit_steps
            audit_steps = total
            progress(done, total + _STATISTICS_STEPS)

        try:
            audit = self._auditor.run(
                should_cancel=should_cancel, progress_callback=audit_progress
            )
        except IntegrityCancelled as error:
            raise MaintenanceCancelled("O diagnóstico foi cancelado.") from error
        statistics = self._statistics(
            should_cancel=should_cancel,
            progress_callback=lambda done: progress(
                audit_steps + done, audit_steps + _STATISTICS_STEPS
            ),
        )
        _raise_if_cancelled(should_cancel)
        status = {
            IntegrityStatus.INTACT: MaintenanceStatus.HEALTHY,
            IntegrityStatus.ATTENTION: MaintenanceStatus.ATTENTION,
            IntegrityStatus.FAILURE: MaintenanceStatus.FAILURE,
        }[audit.status]
        if statistics.errors:
            status = MaintenanceStatus.FAILURE
        if status is not MaintenanceStatus.FAILURE:
            progress(audit_steps + _STATISTICS_STEPS, audit_steps + _STATISTICS_STEPS)
        _raise_if_cancelled(should_cancel)
        return DiagnosticResult(
            datetime.now(timezone.utc), status, statistics, audit
        )

    def _statistics(self, *, should_cancel, progress_callback) -> EnvironmentStatistics:
        _raise_if_cancelled(should_cancel)
        path = self._paths.vault_database_path
        errors = []
        schema = None
        mappings = variations = occurrences = prefixes = 0
        distribution: tuple[tuple[str, int], ...] = ()
        first_entry = last_entry = None
        if path.is_file():
            with tempfile.TemporaryDirectory(prefix="dms-maintenance-diagnostic-") as root:
                snapshot = Path(root) / "vault.db"
                try:
                    create_sqlite_snapshot(path, snapshot, _SnapshotCancellation(should_cancel))
                    connection = connect_read_only(snapshot)
                    try:
                        connection.set_progress_handler(lambda: int(should_cancel()), 1000)
                        schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
                        mappings = int(connection.execute("SELECT COUNT(*) FROM vault_mappings").fetchone()[0])
                        variations = int(connection.execute("SELECT COUNT(*) FROM vault_variations").fetchone()[0])
                        occurrences = int(connection.execute("SELECT COALESCE(SUM(total_occurrences), 0) FROM vault_mappings").fetchone()[0])
                        prefixes = int(connection.execute("SELECT COUNT(DISTINCT prefix) FROM vault_mappings").fetchone()[0])
                        distribution = tuple(
                            (str(row[0]), int(row[1]))
                            for row in connection.execute(
                                "SELECT normalization_rule, COUNT(*) FROM vault_mappings "
                                "GROUP BY normalization_rule ORDER BY normalization_rule"
                            )
                        )
                        dates = connection.execute(
                            "SELECT MIN(first_seen), MAX(last_seen) FROM vault_mappings"
                        ).fetchone()
                        first_entry = str(dates[0]) if dates[0] is not None else None
                        last_entry = str(dates[1]) if dates[1] is not None else None
                    finally:
                        connection.close()
                except (BackupError, sqlite3.Error, OSError, ValueError):
                    _raise_if_cancelled(should_cancel)
                    errors.append("Estatísticas do cofre indisponíveis; coleta incompleta.")
        _raise_if_cancelled(should_cancel)
        progress_callback(1)
        _raise_if_cancelled(should_cancel)
        try:
            profiles = len(ProfileRepository(self._paths.profiles_path).load())
        except ProfileError:
            profiles = 0
            errors.append("Estatísticas de perfis indisponíveis; coleta incompleta.")
        _raise_if_cancelled(should_cancel)
        progress_callback(2)
        _raise_if_cancelled(should_cancel)
        try:
            environment_size = _directory_size(self._paths.directory, should_cancel)
            vault_size = path.stat().st_size if path.is_file() else 0
            free_space = _free_space(self._paths.directory)
        except OSError:
            environment_size = vault_size = free_space = 0
            errors.append("Estatísticas de armazenamento indisponíveis; coleta incompleta.")
        _raise_if_cancelled(should_cancel)
        progress_callback(3)
        _raise_if_cancelled(should_cancel)
        return EnvironmentStatistics(
            schema_version=schema,
            vault_size=vault_size,
            mapping_count=mappings,
            variation_count=variations,
            total_occurrences=occurrences,
            profile_count=profiles,
            prefix_count=prefixes,
            normalization_distribution=distribution,
            first_entry=first_entry,
            last_entry=last_entry,
            wal_present=Path(f"{path}-wal").exists(),
            shm_present=Path(f"{path}-shm").exists(),
            journal_present=Path(f"{path}-journal").exists(),
            environment_size=environment_size,
            free_space=free_space,
            errors=tuple(errors),
        )


def _directory_size(directory: Path, should_cancel) -> int:
    if not directory.is_dir():
        return 0
    total = 0
    for path in directory.rglob("*"):
        _raise_if_cancelled(should_cancel)
        if path.is_file():
            total += path.stat().st_size
    return total


def _free_space(directory: Path) -> int:
    target = directory if directory.exists() else directory.parent
    return int(shutil.disk_usage(target).free)
