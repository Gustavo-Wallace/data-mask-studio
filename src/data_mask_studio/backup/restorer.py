import hmac
import os
import sqlite3
import shutil
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
from data_mask_studio.environment import guarded, GENERATION_FILE, RESTORE_JOURNAL
from data_mask_studio.environment_restore import RestoreOperation, recover_restore, validate_paths, durable_replace
from data_mask_studio.publication import recover_publications
from data_mask_studio.backup.snapshot import create_sqlite_snapshot

AUXILIARY_DATABASE_SUFFIXES = ("-wal", "-shm", "-journal")


@guarded(lambda *args, **kwargs: kwargs["paths"].directory, exclusive=True, recovery=True)
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
        validate_paths(paths)
        recover_restore(paths.directory, protector)
        if list((paths.directory / "publication-operations").glob(".dms-operation-*.json")):
            current_key = LocalKeyProvider(paths.directory, protector).load_existing_key()
            recover_publications(paths.vault_database_path, current_key)
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

            operation = RestoreOperation(paths.directory, protector)
            try:
                staged = operation.work / "B"
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

                staged_paths = EnvironmentPaths(staged, staged_hmac, staged_vault_key,
                                                staged / "vault.db", staged / "profiles.json")
                _verify_restored_environment(staged_paths, protector, extracted.hmac_key,
                    extracted.vault_key, validation.mapping_count, validation.profile_count)
                # Collapse any migration/WAL effects into a self-contained B.
                snapshot = operation.work / "vault.snapshot"
                create_sqlite_snapshot(staged_paths.vault_database_path, snapshot, cancellation)
                durable_replace(snapshot, staged_paths.vault_database_path)
                for sidecar in _auxiliary_paths(staged_paths.vault_database_path):
                    sidecar.unlink(missing_ok=True)
                _write_bytes(staged / GENERATION_FILE, operation.identifier.encode("ascii"))
                cancellation.raise_if_requested()
                operation.prepare()
                cancellation.raise_if_requested()
                try:
                    operation.record("SWAPPING")
                    operation.install("B", _replace_file)
                    _verify_restored_environment(
                        paths,
                        protector,
                        extracted.hmac_key,
                        extracted.vault_key,
                        validation.mapping_count,
                        validation.profile_count,
                    )
                    operation.record("COMMITTED")
                except Exception as error:
                    try:
                        recover_restore(paths.directory, protector)
                    except Exception as rollback_error:
                        raise BackupError(
                            "A restauração falhou e o ambiente local exige revisão."
                        ) from rollback_error
                    if isinstance(error, BackupError):
                        raise
                    raise BackupError(
                        "A restauração falhou e o ambiente anterior foi recuperado."
                    ) from error
                operation.cleanup()
            except Exception:
                if (paths.directory / RESTORE_JOURNAL).exists() and operation.data["state"] in {"STAGED", "SWAPPING"}:
                    recover_restore(paths.directory, protector)
                raise
            finally:
                # Never discard A/B evidence while a durable decision remains.
                if not (paths.directory / RESTORE_JOURNAL).exists() and operation.work.exists():
                    shutil.rmtree(operation.work)

            return RestoreResult(
                mapping_count=validation.mapping_count,
                profile_count=validation.profile_count,
                vault_restored=validation.vault_present,
            )
    except BackupError:
        raise
    except Exception as error:
        raise BackupError("Não foi possível restaurar o backup.") from error


def _replace_file(source: Path, destination: Path) -> None:
    durable_replace(source, destination)


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
    composite = repository.composite_repository()
    connection = sqlite3.connect(paths.vault_database_path)
    try:
        for (code,) in connection.execute("SELECT code FROM composite_mappings"):
            if composite.get_composite_mapping(code) is None:
                raise BackupError("O cofre composto restaurado não pôde ser validado.")
    finally:
        connection.close()


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
