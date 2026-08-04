import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from data_mask_studio.backup import BackupError, EnvironmentPaths, create_sqlite_snapshot
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

    def run(self, *, should_cancel=None, progress_callback=None) -> DiagnosticResult:
        try:
            audit = self._auditor.run(
                should_cancel=should_cancel, progress_callback=progress_callback
            )
        except IntegrityCancelled as error:
            raise MaintenanceCancelled("O diagnóstico foi cancelado.") from error
        statistics = self._statistics()
        status = {
            IntegrityStatus.INTACT: MaintenanceStatus.HEALTHY,
            IntegrityStatus.ATTENTION: MaintenanceStatus.ATTENTION,
            IntegrityStatus.FAILURE: MaintenanceStatus.FAILURE,
        }[audit.status]
        return DiagnosticResult(
            datetime.now(timezone.utc), status, statistics, audit
        )

    def _statistics(self) -> EnvironmentStatistics:
        path = self._paths.vault_database_path
        schema = None
        mappings = variations = occurrences = prefixes = 0
        distribution: tuple[tuple[str, int], ...] = ()
        first_entry = last_entry = None
        if path.is_file():
            with tempfile.TemporaryDirectory(prefix="dms-maintenance-diagnostic-") as root:
                snapshot = Path(root) / "vault.db"
                try:
                    create_sqlite_snapshot(path, snapshot)
                    connection = connect_read_only(snapshot)
                    try:
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
                    pass
        try:
            profiles = len(ProfileRepository(self._paths.profiles_path).load())
        except ProfileError:
            profiles = 0
        return EnvironmentStatistics(
            schema_version=schema,
            vault_size=path.stat().st_size if path.is_file() else 0,
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
            environment_size=_directory_size(self._paths.directory),
            free_space=_free_space(self._paths.directory),
        )


def _directory_size(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    total = 0
    try:
        for path in directory.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _free_space(directory: Path) -> int:
    target = directory if directory.exists() else directory.parent
    try:
        return int(shutil.disk_usage(target).free)
    except OSError:
        return 0
