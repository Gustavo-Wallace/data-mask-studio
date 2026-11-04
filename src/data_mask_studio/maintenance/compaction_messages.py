"""Optional presentation metadata, never compaction or recovery decisions."""

from dataclasses import dataclass
from enum import StrEnum


class CompactionPhase(StrEnum):
    CHECKPOINT = "checkpoint"
    SNAPSHOT = "snapshot"
    VALIDATING = "validating"
    COMPACTING = "compacting"
    REPLACING = "replacing"
    AUDITING = "auditing"


class CompactionReason(StrEnum):
    VAULT_MISSING = "vault_missing"
    SNAPSHOT_MISMATCH = "snapshot_mismatch"
    CONTENT_CHANGED = "content_changed"
    POST_VALIDATION_FAILED = "post_validation_failed"
    POST_AUDIT_FAILED = "post_audit_failed"
    ROLLBACK_FAILED = "rollback_failed"
    COMPACTION_FAILED = "compaction_failed"
    VAULT_BUSY = "vault_busy"
    CHECKPOINT_FAILED = "checkpoint_failed"
    VACUUM_FAILED = "vacuum_failed"
    STRUCTURE_INVALID = "structure_invalid"
    DATABASE_INVALID = "database_invalid"
    COMPARISON_FAILED = "comparison_failed"
    OPTIMIZE_FAILED = "optimize_failed"
    REPLACEMENT_FAILED = "replacement_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CompactionExplanation:
    reason: CompactionReason | None = None
    rollback_completed: bool = False
    recovery_directory: str | None = None
