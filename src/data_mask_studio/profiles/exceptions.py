from dataclasses import dataclass
from enum import StrEnum


class ProfileReason(StrEnum):
    """Optional presentation metadata, never a persistence or validation input."""

    NAME_LENGTH = "name_length"
    NAME_CONTROL_CHARACTERS = "name_control_characters"
    DUPLICATE_NAME = "duplicate_name"
    PROFILE_NOT_FOUND = "profile_not_found"
    WRITE_BUSY = "write_busy"
    SOURCE_MISMATCH = "source_mismatch"


@dataclass(frozen=True, slots=True)
class ProfileExplanation:
    reason: ProfileReason


class ProfileError(Exception):
    """Erro compreensível relacionado aos perfis de configuração."""

    def __init__(self, *args: object, explanation: ProfileExplanation | None = None) -> None:
        super().__init__(*args)
        self.explanation = explanation


class ProfileValidationError(ProfileError):
    """Um perfil ou nome não atende às regras do formato."""


class ProfileStorageError(ProfileError):
    """O arquivo de perfis não pôde ser lido ou gravado com segurança."""


class ProfileFormatError(ProfileStorageError):
    """O arquivo existe, mas não contém um documento válido e suportado."""
