from dataclasses import dataclass
from enum import StrEnum


class BackupReason(StrEnum):
    """Safe presentation metadata; never a backup or recovery decision."""

    PASSWORD_TOO_SHORT = "password_too_short"
    PASSWORD_MISMATCH = "password_mismatch"
    CRYPTO_PARAMETERS_INVALID = "crypto_parameters_invalid"
    DESTINATION_EXTENSION = "destination_extension"
    DESTINATION_FOLDER_MISSING = "destination_folder_missing"
    DESTINATION_ALIAS = "destination_alias"
    DESTINATION_EXISTS = "destination_exists"
    LOCAL_KEY_INVALID = "local_key_invalid"
    LOCAL_SCHEMA_INCOMPATIBLE = "local_schema_incompatible"
    CREATE_OUTPUT_FAILED = "create_output_failed"
    CREATE_FAILED = "create_failed"
    VAULT_MISSING = "vault_missing"
    SNAPSHOT_FAILED = "snapshot_failed"
    SNAPSHOT_CHECK_FAILED = "snapshot_check_failed"
    SNAPSHOT_COUNTERS_INCONSISTENT = "snapshot_counters_inconsistent"
    SNAPSHOT_INVALID = "snapshot_invalid"
    FORMAT_UNSUPPORTED = "format_unsupported"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"
    APPLICATION_INCOMPATIBLE = "application_incompatible"
    RESTORE_REVIEW_REQUIRED = "restore_review_required"
    RESTORE_ROLLED_BACK = "restore_rolled_back"
    RESTORE_FAILED = "restore_failed"
    RESTORED_KEYS_INVALID = "restored_keys_invalid"
    RESTORED_VAULT_INVALID = "restored_vault_invalid"
    RESTORED_PROFILES_INVALID = "restored_profiles_invalid"
    RESTORED_COMPOSITE_INVALID = "restored_composite_invalid"


@dataclass(frozen=True, slots=True)
class BackupExplanation:
    reason: BackupReason


class BackupError(RuntimeError):
    """Erro seguro e compreensível relacionado a backup ou restauração."""

    def __init__(self, *args: object, explanation: BackupExplanation | None = None) -> None:
        super().__init__(*args)
        self.explanation = explanation


class BackupValidationError(BackupError):
    """O arquivo não pôde ser autenticado e validado."""


class BackupCompatibilityError(BackupError):
    """O formato ou conteúdo é incompatível com esta aplicação."""


class BackupCancelled(BackupError):
    """A operação foi cancelada cooperativamente."""


VALIDATION_FAILURE_MESSAGE = (
    "Não foi possível validar o backup. Verifique a senha e a integridade do arquivo."
)
