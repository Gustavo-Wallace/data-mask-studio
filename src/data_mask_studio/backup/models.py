from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import Event


class BackupCompatibility(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"


class BackupStage(StrEnum):
    SNAPSHOT = "snapshot"
    ENCRYPTING = "encrypting"
    VALIDATING = "validating"
    PREPARING_RESTORE = "preparing_restore"
    RESTORING = "restoring"
    VERIFYING = "verifying"


@dataclass(frozen=True, slots=True)
class ScryptParameters:
    n: int = 2**15
    r: int = 8
    p: int = 1
    length: int = 32
    salt_size: int = 16


@dataclass(frozen=True, slots=True)
class BackupHeader:
    format_version: int
    scrypt: ScryptParameters
    salt: bytes = field(repr=False)
    nonce: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class BackupProgress:
    stage: BackupStage
    processed_bytes: int = 0
    total_bytes: int = 0


@dataclass(frozen=True, slots=True)
class BackupValidationResult:
    created_at: datetime
    application_version: str
    format_version: int
    vault_schema_version: int
    mapping_count: int
    profile_count: int
    vault_present: bool
    compatibility: BackupCompatibility

    @property
    def is_compatible(self) -> bool:
        return self.compatibility is BackupCompatibility.COMPATIBLE


@dataclass(frozen=True, slots=True)
class BackupCreationResult:
    path: Path
    created_at: datetime
    bytes_written: int


@dataclass(frozen=True, slots=True)
class RestoreResult:
    mapping_count: int
    profile_count: int
    vault_restored: bool


@dataclass(frozen=True, slots=True)
class EnvironmentPaths:
    directory: Path
    hmac_key_path: Path
    vault_key_path: Path
    vault_database_path: Path
    profiles_path: Path


class CancellationRequest:
    def __init__(self) -> None:
        self._event = Event()

    def request(self) -> None:
        self._event.set()

    def is_requested(self) -> bool:
        return self._event.is_set()

    def raise_if_requested(self) -> None:
        from data_mask_studio.backup.exceptions import BackupCancelled

        if self.is_requested():
            raise BackupCancelled("A operação de backup foi cancelada.")
