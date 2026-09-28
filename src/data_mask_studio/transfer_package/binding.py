"""Exact-byte binding; verification is point-in-time, not a file lock."""

import hmac
import re
from pathlib import Path

from data_mask_studio.publication import PublicationError, fingerprint
from data_mask_studio.transfer_package.models import MaskedFileBinding, PackageError


def compute_file_binding(path: str | Path) -> MaskedFileBinding:
    """Reuse the publication fingerprint's bounded, binary streaming read."""
    try:
        result = fingerprint(Path(path))
        return MaskedFileBinding(sha256=result["sha256"], size=result["size"])
    except (OSError, PublicationError):
        raise PackageError("Não foi possível ler o arquivo para verificar seu vínculo.") from None


def verify_file_binding(path: str | Path, expected: MaskedFileBinding) -> None:
    """Fail closed; does not guarantee that bytes remain unchanged afterwards."""
    if (not isinstance(expected, MaskedFileBinding)
            or type(expected.size) is not int or expected.size < 0
            or not isinstance(expected.sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected.sha256) is None):
        raise PackageError("Metadados de vínculo do arquivo inválidos.")
    actual = compute_file_binding(path)
    if actual.size != expected.size or not hmac.compare_digest(actual.sha256, expected.sha256):
        raise PackageError("O arquivo não corresponde ao vínculo esperado.")
