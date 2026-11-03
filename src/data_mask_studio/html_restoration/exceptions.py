from dataclasses import dataclass
from enum import StrEnum


class HTMLRestorationReason(StrEnum):
    """Presentation only; never used for HTML or security decisions."""

    SOURCE_MISSING = "source_missing"
    INPUT_PERMISSION_DENIED = "input_permission_denied"
    INPUT_ACCESS_FAILED = "input_access_failed"
    INPUT_EMPTY = "input_empty"
    ENCODING_UNIDENTIFIED = "encoding_unidentified"
    INPUT_PROCESSING_FAILED = "input_processing_failed"
    OUTPUT_ENCODING_FAILED = "output_encoding_failed"
    OUTPUT_FAILED = "output_failed"
    DESTINATION_ALIAS = "destination_alias"
    DESTINATION_FOLDER_MISSING = "destination_folder_missing"
    DESTINATION_EXISTS = "destination_exists"


@dataclass(frozen=True, slots=True)
class HTMLRestorationExplanation:
    reason: HTMLRestorationReason


class HTMLRestorationError(RuntimeError):
    """Falha esperada sem exposição de conteúdo sensível."""

    def __init__(self, *args: object, explanation: HTMLRestorationExplanation | None = None) -> None:
        super().__init__(*args)
        self.explanation = explanation


class HTMLRestorationSecurityError(HTMLRestorationError):
    """Falha de integridade ou descriptografia do cofre."""


class HTMLUnsupportedContextError(HTMLRestorationError):
    """Contexto HTML não suportado; não indica corrupção do cofre."""


class HTMLRestorationCancelled(HTMLRestorationError):
    """Operação interrompida cooperativamente."""


class HTMLMissingCodeError(HTMLRestorationError):
    """Código ausente sob política de interrupção."""
