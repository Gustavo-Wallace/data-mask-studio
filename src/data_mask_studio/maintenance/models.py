from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from data_mask_studio.integrity import AuditReport


class MaintenanceStatus(StrEnum):
    HEALTHY = "healthy"
    ATTENTION = "attention"
    FAILURE = "failure"


STATUS_LABELS = {
    MaintenanceStatus.HEALTHY: "Saudável",
    MaintenanceStatus.ATTENTION: "Atenção",
    MaintenanceStatus.FAILURE: "Falha",
}


@dataclass(frozen=True, slots=True)
class EnvironmentStatistics:
    schema_version: int | None
    vault_size: int
    mapping_count: int
    variation_count: int
    total_occurrences: int
    profile_count: int
    prefix_count: int
    normalization_distribution: tuple[tuple[str, int], ...]
    first_entry: str | None
    last_entry: str | None
    wal_present: bool
    shm_present: bool
    journal_present: bool
    environment_size: int
    free_space: int


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    created_at: datetime
    status: MaintenanceStatus
    statistics: EnvironmentStatistics
    audit: AuditReport


@dataclass(slots=True)
class TemporaryItem:
    path: Path
    size: int
    age_seconds: float
    recent: bool
    in_use: bool
    selected: bool = False
    result: str = ""

    @property
    def removable(self) -> bool:
        return not self.recent and not self.in_use


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed: int
    preserved: int
    failed: int
    recovered_bytes: int


@dataclass(frozen=True, slots=True)
class CompactionResult:
    size_before: int
    size_after: int
    recovered_bytes: int
    audit: AuditReport
