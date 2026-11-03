"""Shared presentation adapter for backup/recovery and backup validation only."""

from data_mask_studio.backup import (
    BackupError, BackupExplanation, BackupReason, BackupValidationError,
)


def backup_error_message_id(error: BackupError) -> str | None:
    # Authentication, tampering and malformed payloads keep one safe rejection.
    if isinstance(error, BackupValidationError):
        return "backup.error.validation"
    explanation = getattr(error, "explanation", None)
    if (isinstance(explanation, BackupExplanation)
            and isinstance(explanation.reason, BackupReason)):
        return "backup.error." + explanation.reason.value
    return None
