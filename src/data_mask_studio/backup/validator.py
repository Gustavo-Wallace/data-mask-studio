import hashlib
import json
import struct
import tempfile
import string
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from data_mask_studio.backup.crypto import TAG_SIZE, derive_key, validate_scrypt_parameters
from data_mask_studio.backup.exceptions import (
    BackupCancelled,
    BackupCompatibilityError,
    BackupError,
    BackupValidationError,
    VALIDATION_FAILURE_MESSAGE,
)
from data_mask_studio.backup.format import FORMAT_VERSION, read_prefix
from data_mask_studio.backup.models import (
    BackupCompatibility,
    BackupValidationResult,
    CancellationRequest,
)
from data_mask_studio.backup.snapshot import inspect_snapshot
from data_mask_studio.metadata import application_version
from data_mask_studio.profiles import ProfileRepository
from data_mask_studio.security.key_provider import KEY_SIZE
from data_mask_studio.vault.database import RESTORABLE_SCHEMA_VERSIONS

CHUNK_SIZE = 1024 * 1024
MAX_MANIFEST_SIZE = 1024 * 1024
MAX_PROFILES_SIZE = 64 * 1024 * 1024
COMPONENT_ORDER = ("hmac_key", "vault_key", "vault_database", "profiles")


@dataclass(slots=True)
class ExtractedBackup:
    manifest: dict[str, object]
    hmac_key: bytes = field(repr=False)
    vault_key: bytes = field(repr=False)
    vault_snapshot_path: Path | None = field(default=None, repr=False)
    profiles_data: bytes | None = field(default=None, repr=False)


def validate_backup(
    backup_path: str | Path,
    password: str,
    *,
    cancellation: CancellationRequest | None = None,
    current_app_version: str | None = None,
) -> BackupValidationResult:
    try:
        with extracted_backup(
            backup_path, password, cancellation=cancellation
        ) as extracted:
            return _validate_extracted(
                extracted, current_app_version or application_version()
            )
    except (BackupCancelled, BackupCompatibilityError):
        raise
    except BackupError as error:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE) from error
    except Exception as error:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE) from error


@contextmanager
def extracted_backup(
    backup_path: str | Path,
    password: str,
    *,
    cancellation: CancellationRequest | None = None,
) -> Iterator[ExtractedBackup]:
    cancellation = cancellation or CancellationRequest()
    path = Path(backup_path).expanduser().absolute()
    try:
        with tempfile.TemporaryDirectory(prefix=".dms-validate-") as directory:
            snapshot_path = Path(directory) / "vault.db"
            with path.open("rb") as file:
                header, prefix = read_prefix(file)
                validate_scrypt_parameters(header.scrypt)
                payload_start = file.tell()
                file.seek(0, 2)
                total_size = file.tell()
                ciphertext_size = total_size - payload_start - TAG_SIZE
                if ciphertext_size <= 4:
                    raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
                file.seek(total_size - TAG_SIZE)
                tag = file.read(TAG_SIZE)
                file.seek(payload_start)
                derived_key = derive_key(password, header.salt, header.scrypt)
                decryptor = Cipher(
                    algorithms.AES(derived_key), modes.GCM(header.nonce, tag)
                ).decryptor()
                decryptor.authenticate_additional_data(prefix)
                extractor = _PayloadExtractor(snapshot_path, ciphertext_size)
                try:
                    remaining = ciphertext_size
                    while remaining:
                        cancellation.raise_if_requested()
                        encrypted = file.read(min(CHUNK_SIZE, remaining))
                        if not encrypted:
                            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
                        remaining -= len(encrypted)
                        extractor.feed(decryptor.update(encrypted))
                    extractor.feed(decryptor.finalize())
                    extracted = extractor.finish()
                finally:
                    extractor.close()
                    derived_key = b""
            try:
                yield extracted
            finally:
                extracted.hmac_key = b""
                extracted.vault_key = b""
                extracted.profiles_data = None
    except BackupCompatibilityError:
        raise
    except InvalidTag as error:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE) from error
    except BackupError:
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE) from error


class _PayloadExtractor:
    def __init__(self, snapshot_path: Path, ciphertext_size: int) -> None:
        self.snapshot_path = snapshot_path
        self.ciphertext_size = ciphertext_size
        self.buffer = bytearray()
        self.manifest: dict[str, object] | None = None
        self.components: list[dict[str, object]] = []
        self.component_index = 0
        self.component_remaining = 0
        self.component_hash = hashlib.sha256()
        self.component_memory = bytearray()
        self.snapshot_file: BinaryIO | None = None
        self.hmac_key = b""
        self.vault_key = b""
        self.profiles_data: bytes | None = None

    def feed(self, data: bytes) -> None:
        self.buffer.extend(data)
        if self.manifest is None:
            if len(self.buffer) < 4:
                return
            manifest_size = struct.unpack(">I", self.buffer[:4])[0]
            if not 1 <= manifest_size <= MAX_MANIFEST_SIZE:
                raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
            if len(self.buffer) < 4 + manifest_size:
                return
            encoded = bytes(self.buffer[4 : 4 + manifest_size])
            del self.buffer[: 4 + manifest_size]
            self.manifest = json.loads(encoded.decode("utf-8"))
            self.components = _validate_manifest(self.manifest)
            expected_size = 4 + manifest_size + sum(
                int(item["size"]) for item in self.components
            )
            if expected_size != self.ciphertext_size:
                raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
            self._start_component()
        self._consume_components()

    def _consume_components(self) -> None:
        while self.buffer and self.component_index < len(self.components):
            amount = min(len(self.buffer), self.component_remaining)
            chunk = bytes(self.buffer[:amount])
            del self.buffer[:amount]
            self.component_hash.update(chunk)
            name = str(self.components[self.component_index]["name"])
            if name == "vault_database":
                if self.snapshot_file is None:
                    self.snapshot_file = self.snapshot_path.open("wb")
                self.snapshot_file.write(chunk)
            else:
                self.component_memory.extend(chunk)
            self.component_remaining -= amount
            if self.component_remaining == 0:
                self._finish_component(name)
                self.component_index += 1
                self._start_component()

    def _start_component(self) -> None:
        if self.component_index >= len(self.components):
            return
        self.component_remaining = int(self.components[self.component_index]["size"])
        self.component_hash = hashlib.sha256()
        self.component_memory = bytearray()
        if self.component_remaining == 0:
            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)

    def _finish_component(self, name: str) -> None:
        expected_hash = str(self.components[self.component_index]["sha256"])
        if self.component_hash.hexdigest() != expected_hash:
            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
        if name == "hmac_key":
            self.hmac_key = bytes(self.component_memory)
        elif name == "vault_key":
            self.vault_key = bytes(self.component_memory)
        elif name == "profiles":
            if len(self.component_memory) > MAX_PROFILES_SIZE:
                raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
            self.profiles_data = bytes(self.component_memory)
        elif name == "vault_database" and self.snapshot_file is not None:
            self.snapshot_file.flush()
            self.snapshot_file.close()
            self.snapshot_file = None

    def finish(self) -> ExtractedBackup:
        if self.snapshot_file is not None:
            self.snapshot_file.close()
            self.snapshot_file = None
        if (
            self.manifest is None
            or self.buffer
            or self.component_index != len(self.components)
            or len(self.hmac_key) != KEY_SIZE
            or len(self.vault_key) != KEY_SIZE
        ):
            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
        vault_path = (
            self.snapshot_path
            if any(item["name"] == "vault_database" for item in self.components)
            else None
        )
        return ExtractedBackup(
            manifest=self.manifest,
            hmac_key=self.hmac_key,
            vault_key=self.vault_key,
            vault_snapshot_path=vault_path,
            profiles_data=self.profiles_data,
        )

    def close(self) -> None:
        if self.snapshot_file is not None:
            self.snapshot_file.close()
            self.snapshot_file = None


def _validate_manifest(manifest: object) -> list[dict[str, object]]:
    if not isinstance(manifest, dict):
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
    required = {
        "application_version",
        "backup_format_version",
        "created_at",
        "vault_schema_version",
        "vault_present",
        "mapping_count",
        "profile_count",
        "components",
    }
    if not required.issubset(manifest):
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
    components = manifest["components"]
    if not isinstance(components, list):
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
    validated: list[dict[str, object]] = []
    names: list[str] = []
    for item in components:
        if not isinstance(item, dict):
            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
        name, size, digest = item.get("name"), item.get("size"), item.get("sha256")
        if (
            not isinstance(name, str)
            or name not in COMPONENT_ORDER
            or name in names
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in string.hexdigits for character in digest)
        ):
            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
        if name in {"hmac_key", "vault_key"} and size != KEY_SIZE:
            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
        if name == "profiles" and size > MAX_PROFILES_SIZE:
            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
        names.append(name)
        validated.append(item)
    if names[:2] != ["hmac_key", "vault_key"] or names != sorted(
        names, key=COMPONENT_ORDER.index
    ):
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
    return validated


def _validate_extracted(
    extracted: ExtractedBackup, current_version: str
) -> BackupValidationResult:
    manifest = extracted.manifest
    try:
        created_at = datetime.fromisoformat(str(manifest["created_at"]))
        backup_app_version = str(manifest["application_version"])
        format_version = int(manifest["backup_format_version"])
        schema_version = int(manifest["vault_schema_version"])
        declared_mappings = int(manifest["mapping_count"])
        declared_profiles = int(manifest["profile_count"])
        vault_present = manifest["vault_present"]
        if not isinstance(vault_present, bool) or format_version != FORMAT_VERSION:
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE) from error

    if schema_version not in RESTORABLE_SCHEMA_VERSIONS:
        raise BackupCompatibilityError("O esquema do cofre não é compatível.")

    mapping_count = 0
    if extracted.vault_snapshot_path is not None:
        if not vault_present:
            raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
        actual_schema, mapping_count = inspect_snapshot(extracted.vault_snapshot_path)
        if (
            actual_schema != schema_version
            or schema_version not in RESTORABLE_SCHEMA_VERSIONS
        ):
            raise BackupCompatibilityError("O esquema do cofre não é compatível.")
    elif vault_present:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
    if mapping_count != declared_mappings:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)

    profiles = (
        ProfileRepository.parse_bytes(extracted.profiles_data)
        if extracted.profiles_data is not None
        else []
    )
    if len(profiles) != declared_profiles:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE)
    compatible = _version_tuple(backup_app_version) <= _version_tuple(current_version)
    return BackupValidationResult(
        created_at=created_at,
        application_version=backup_app_version,
        format_version=format_version,
        vault_schema_version=schema_version,
        mapping_count=mapping_count,
        profile_count=len(profiles),
        vault_present=vault_present,
        compatibility=(
            BackupCompatibility.COMPATIBLE
            if compatible
            else BackupCompatibility.INCOMPATIBLE
        ),
    )


def _version_tuple(value: str) -> tuple[int, int, int]:
    try:
        parts = value.split(".")
        return tuple(int(part) for part in parts[:3]) + (0,) * (3 - len(parts[:3]))
    except ValueError as error:
        raise BackupValidationError(VALIDATION_FAILURE_MESSAGE) from error
