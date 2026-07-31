import hmac
import os
import sqlite3
import tempfile
from pathlib import Path

from data_mask_studio.backup.exceptions import (
    BackupCompatibilityError,
    BackupError,
)
from data_mask_studio.backup.models import (
    BackupCompatibility,
    CancellationRequest,
    EnvironmentPaths,
    RestoreResult,
)
from data_mask_studio.backup.validator import _validate_extracted, extracted_backup
from data_mask_studio.metadata import application_version
from data_mask_studio.profiles import ProfileRepository
from data_mask_studio.security import DataProtector, LocalKeyProvider
from data_mask_studio.vault import VaultCipher, VaultRepository

AUXILIARY_DATABASE_SUFFIXES = ("-wal", "-shm", "-journal")


def restore_backup(
    backup_path: str | Path,
    password: str,
    *,
    paths: EnvironmentPaths,
    protector: DataProtector,
    cancellation: CancellationRequest | None = None,
    current_app_version: str | None = None,
) -> RestoreResult:
    cancellation = cancellation or CancellationRequest()
    current_version = current_app_version or application_version()
    paths.directory.mkdir(parents=True, exist_ok=True)
    try:
        with extracted_backup(
            backup_path, password, cancellation=cancellation
        ) as extracted:
            validation = _validate_extracted(extracted, current_version)
            if validation.compatibility is not BackupCompatibility.COMPATIBLE:
                raise BackupCompatibilityError(
                    "O backup foi criado por uma versão incompatível."
                )
            cancellation.raise_if_requested()
            protected_hmac = protector.protect(extracted.hmac_key)
            protected_vault = protector.protect(extracted.vault_key)
            cancellation.raise_if_requested()

            with tempfile.TemporaryDirectory(
                prefix=".dms-restore-", dir=paths.directory
            ) as temporary_directory:
                work = Path(temporary_directory)
                staged = work / "staged"
                rollback = work / "rollback"
                staged.mkdir()
                rollback.mkdir()
                staged_hmac = staged / paths.hmac_key_path.name
                staged_vault_key = staged / paths.vault_key_path.name
                _write_bytes(staged_hmac, protected_hmac)
                _write_bytes(staged_vault_key, protected_vault)

                staged_database: Path | None = None
                if extracted.vault_snapshot_path is not None:
                    staged_database = staged / paths.vault_database_path.name
                    _copy_file(
                        extracted.vault_snapshot_path, staged_database, cancellation
                    )
                staged_profiles: Path | None = None
                if extracted.profiles_data is not None:
                    staged_profiles = staged / paths.profiles_path.name
                    _write_bytes(staged_profiles, extracted.profiles_data)

                targets = _all_targets(paths)
                existed = {target: target.exists() for target in targets}
                for index, target in enumerate(targets):
                    if target.exists():
                        _copy_file(target, rollback / f"{index}.bak", cancellation)
                cancellation.raise_if_requested()

                replacements = {
                    paths.hmac_key_path: staged_hmac,
                    paths.vault_key_path: staged_vault_key,
                    paths.vault_database_path: staged_database,
                    paths.profiles_path: staged_profiles,
                }
                try:
                    _replace_environment(replacements, paths)
                    _verify_restored_environment(
                        paths,
                        protector,
                        extracted.hmac_key,
                        extracted.vault_key,
                        validation.mapping_count,
                        validation.profile_count,
                    )
                except Exception as error:
                    try:
                        _rollback_environment(targets, existed, rollback)
                    except Exception as rollback_error:
                        raise BackupError(
                            "A restauração falhou e o ambiente local exige revisão."
                        ) from rollback_error
                    if isinstance(error, BackupError):
                        raise
                    raise BackupError(
                        "A restauração falhou e o ambiente anterior foi recuperado."
                    ) from error

            return RestoreResult(
                mapping_count=validation.mapping_count,
                profile_count=validation.profile_count,
                vault_restored=validation.vault_present,
            )
    except BackupError:
        raise
    except Exception as error:
        raise BackupError("Não foi possível restaurar o backup.") from error


def _replace_environment(
    replacements: dict[Path, Path | None], paths: EnvironmentPaths
) -> None:
    for auxiliary in _auxiliary_paths(paths.vault_database_path):
        auxiliary.unlink(missing_ok=True)
    for target, staged in replacements.items():
        if staged is None:
            target.unlink(missing_ok=True)
        else:
            _replace_file(staged, target)


def _replace_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _rollback_environment(
    targets: list[Path], existed: dict[Path, bool], rollback: Path
) -> None:
    failures: list[OSError] = []
    for index, target in enumerate(targets):
        try:
            if existed[target]:
                restore_staging = rollback / f"{index}.restore"
                _copy_file(rollback / f"{index}.bak", restore_staging)
                os.replace(restore_staging, target)
            else:
                target.unlink(missing_ok=True)
        except OSError as error:
            failures.append(error)
    if failures:
        raise BackupError("Não foi possível concluir o rollback local.") from failures[0]


def _verify_restored_environment(
    paths: EnvironmentPaths,
    protector: DataProtector,
    expected_hmac_key: bytes,
    expected_vault_key: bytes,
    expected_mappings: int,
    expected_profiles: int,
) -> None:
    hmac_provider = LocalKeyProvider(
        paths.directory,
        protector,
        key_file_name=paths.hmac_key_path.name,
    )
    vault_provider = LocalKeyProvider(
        paths.directory,
        protector,
        key_file_name=paths.vault_key_path.name,
    )
    restored_hmac = hmac_provider.get_key()
    restored_vault = vault_provider.get_key()
    if not hmac.compare_digest(restored_hmac, expected_hmac_key) or not hmac.compare_digest(
        restored_vault, expected_vault_key
    ):
        raise BackupError("As chaves restauradas não puderam ser validadas.")

    repository = VaultRepository(paths.vault_database_path, VaultCipher(restored_vault))
    if repository.count() != expected_mappings:
        raise BackupError("O cofre restaurado não pôde ser validado.")
    if expected_mappings:
        connection = sqlite3.connect(paths.vault_database_path)
        try:
            codes = [str(row[0]) for row in connection.execute("SELECT code FROM vault_mappings")]
        finally:
            connection.close()
        for code in codes:
            if repository.get_decrypted_mapping(code) is None:
                raise BackupError("O cofre restaurado não pôde ser validado.")

    profiles = ProfileRepository(paths.profiles_path).load()
    if len(profiles) != expected_profiles:
        raise BackupError("Os perfis restaurados não puderam ser validados.")


def _all_targets(paths: EnvironmentPaths) -> list[Path]:
    return [
        paths.hmac_key_path,
        paths.vault_key_path,
        paths.vault_database_path,
        paths.profiles_path,
        *_auxiliary_paths(paths.vault_database_path),
    ]


def _auxiliary_paths(database_path: Path) -> list[Path]:
    return [Path(f"{database_path}{suffix}") for suffix in AUXILIARY_DATABASE_SUFFIXES]


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def _copy_file(
    source: Path,
    destination: Path,
    cancellation: CancellationRequest | None = None,
) -> None:
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        while chunk := input_file.read(1024 * 1024):
            if cancellation is not None:
                cancellation.raise_if_requested()
            output_file.write(chunk)
        output_file.flush()
        os.fsync(output_file.fileno())
