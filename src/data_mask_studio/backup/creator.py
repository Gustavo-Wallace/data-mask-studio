import hashlib
import json
import os
import secrets
import struct
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from data_mask_studio.backup.crypto import (
    NONCE_SIZE,
    SCRYPT_PARAMETERS,
    derive_key,
    validate_password,
)
from data_mask_studio.backup.exceptions import BackupError
from data_mask_studio.backup.format import FORMAT_VERSION, build_prefix
from data_mask_studio.backup.models import (
    BackupCreationResult,
    BackupHeader,
    BackupProgress,
    BackupStage,
    CancellationRequest,
    EnvironmentPaths,
)
from data_mask_studio.backup.snapshot import create_sqlite_snapshot, inspect_snapshot
from data_mask_studio.metadata import application_version
from data_mask_studio.profiles import ProfileRepository
from data_mask_studio.security import KeyProvider
from data_mask_studio.security.key_provider import KEY_SIZE
from data_mask_studio.vault.database import SCHEMA_VERSION

ProgressCallback = Callable[[BackupProgress], None]
CHUNK_SIZE = 1024 * 1024


def suggested_backup_name(now: datetime | None = None) -> str:
    moment = now or datetime.now()
    return f"data-mask-studio-backup-{moment:%Y-%m-%d-%H%M%S}.dmsbackup"


def create_backup(
    destination_path: str | Path,
    password: str,
    confirmation: str,
    *,
    paths: EnvironmentPaths,
    hmac_key_provider: KeyProvider,
    vault_key_provider: KeyProvider,
    overwrite: bool = False,
    cancellation: CancellationRequest | None = None,
    progress_callback: ProgressCallback | None = None,
    app_version: str | None = None,
) -> BackupCreationResult:
    validate_password(password, confirmation)
    destination = Path(destination_path).expanduser().absolute()
    if destination.suffix.lower() != ".dmsbackup":
        raise BackupError("Use a extensão .dmsbackup para o arquivo de backup.")
    if not destination.parent.is_dir():
        raise BackupError("A pasta de destino do backup não existe.")
    if destination.exists() and not overwrite:
        raise BackupError("O arquivo de backup já existe.")

    cancellation = cancellation or CancellationRequest()
    temporary_output: Path | None = None
    created_at = datetime.now(timezone.utc)
    hmac_key: bytes | None = None
    vault_key: bytes | None = None
    derived_key: bytes | None = None

    try:
        cancellation.raise_if_requested()
        hmac_key = hmac_key_provider.get_key()
        vault_key = vault_key_provider.get_key()
        if len(hmac_key) != KEY_SIZE or len(vault_key) != KEY_SIZE:
            raise BackupError("Uma chave local possui formato inválido.")

        with tempfile.TemporaryDirectory(
            prefix=".dms-backup-", dir=destination.parent
        ) as temporary_directory:
            work_directory = Path(temporary_directory)
            snapshot_path: Path | None = None
            vault_present = paths.vault_database_path.is_file()
            schema_version = SCHEMA_VERSION
            mapping_count = 0
            if vault_present:
                _progress(progress_callback, BackupStage.SNAPSHOT)
                snapshot_path = work_directory / "vault-snapshot.db"
                create_sqlite_snapshot(
                    paths.vault_database_path, snapshot_path, cancellation
                )
                schema_version, mapping_count = inspect_snapshot(snapshot_path)
                if schema_version != SCHEMA_VERSION:
                    raise BackupError("A versão do cofre local não é compatível.")

            profiles_data: bytes | None = None
            profile_count = 0
            if paths.profiles_path.is_file():
                profiles_data = paths.profiles_path.read_bytes()
                profile_count = len(ProfileRepository.parse_bytes(profiles_data))

            components = [
                _memory_component("hmac_key", hmac_key),
                _memory_component("vault_key", vault_key),
            ]
            if snapshot_path is not None:
                components.append(_file_component("vault_database", snapshot_path))
            if profiles_data is not None:
                components.append(_memory_component("profiles", profiles_data))

            manifest = {
                "application_version": app_version or application_version(),
                "backup_format_version": FORMAT_VERSION,
                "created_at": created_at.isoformat(),
                "vault_schema_version": schema_version,
                "vault_present": vault_present,
                "mapping_count": mapping_count,
                "profile_count": profile_count,
                "components": [item[0] for item in components],
            }
            manifest_bytes = json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            salt = secrets.token_bytes(SCRYPT_PARAMETERS.salt_size)
            nonce = secrets.token_bytes(NONCE_SIZE)
            header = BackupHeader(
                FORMAT_VERSION, SCRYPT_PARAMETERS, salt=salt, nonce=nonce
            )
            prefix = build_prefix(header)
            derived_key = derive_key(password, salt)
            cancellation.raise_if_requested()

            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary_output = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output:
                output.write(prefix)
                encryptor = Cipher(
                    algorithms.AES(derived_key), modes.GCM(nonce)
                ).encryptor()
                encryptor.authenticate_additional_data(prefix)
                _progress(progress_callback, BackupStage.ENCRYPTING)
                output.write(encryptor.update(struct.pack(">I", len(manifest_bytes))))
                output.write(encryptor.update(manifest_bytes))
                for metadata, source in components:
                    cancellation.raise_if_requested()
                    if isinstance(source, Path):
                        with source.open("rb") as component_file:
                            while chunk := component_file.read(CHUNK_SIZE):
                                cancellation.raise_if_requested()
                                output.write(encryptor.update(chunk))
                    else:
                        output.write(encryptor.update(source))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())

            cancellation.raise_if_requested()
            if destination.exists() and not overwrite:
                raise BackupError("O arquivo de backup já existe.")
            os.replace(temporary_output, destination)
            temporary_output = None
    except BackupError:
        raise
    except OSError as error:
        raise BackupError("Não foi possível criar o arquivo de backup.") from error
    except Exception as error:
        raise BackupError("Não foi possível criar o backup seguro.") from error
    finally:
        if temporary_output is not None:
            try:
                temporary_output.unlink(missing_ok=True)
            except OSError:
                pass
        hmac_key = None
        vault_key = None
        derived_key = None

    return BackupCreationResult(destination, created_at, destination.stat().st_size)


def _memory_component(name: str, data: bytes) -> tuple[dict[str, object], bytes]:
    return (
        {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        data,
    )


def _file_component(name: str, path: Path) -> tuple[dict[str, object], Path]:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            digest.update(chunk)
    return (
        {"name": name, "size": path.stat().st_size, "sha256": digest.hexdigest()},
        path,
    )


def _progress(callback: ProgressCallback | None, stage: BackupStage) -> None:
    if callback is not None:
        callback(BackupProgress(stage))
