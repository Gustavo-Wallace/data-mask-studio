from dataclasses import dataclass, field
from enum import StrEnum

from data_mask_studio.vault.models import DecryptedVaultMapping


class ConsultationStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    INVALID = "invalid"
    RECOVERY_FAILED = "recovery_failed"


@dataclass(frozen=True, slots=True)
class ConsultationResult:
    code: str
    status: ConsultationStatus
    mapping: DecryptedVaultMapping | None = field(default=None, repr=False)
    message: str | None = None

