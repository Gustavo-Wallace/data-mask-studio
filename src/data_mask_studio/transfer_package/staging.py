"""Pre-commit candidate only. No final publication or recovery journal here."""

import os
import hashlib
import re
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from data_mask_studio.transfer_package.binding import compute_file_binding, verify_file_binding
from data_mask_studio.transfer_package.models import MaskedFileBinding, PackageError
from data_mask_studio.transfer_package.selection import select_transaction_mappings
from data_mask_studio.transfer_package.serialization import MAX_PAYLOAD_SIZE, payload_limit
from data_mask_studio.transfer_package.service import encrypt_package, read_package
from data_mask_studio.vault.repository import VaultTransaction


class PackageStagingCancelled(PackageError):
    """Cancellation before committing; no successful candidate escapes."""


@dataclass(frozen=True, slots=True)
class PackageStagingRequest:
    password: str = field(repr=False)
    max_payload_bytes: int = MAX_PAYLOAD_SIZE
    application_version: str | None = None


@dataclass(frozen=True, slots=True)
class StagedPackage:
    path: Path = field(repr=False)
    binding: MaskedFileBinding = field(repr=False)
    ciphertext: MaskedFileBinding = field(repr=False)

    def discard(self) -> None:
        """Only explicit DMS-owned candidates, not arbitrary final packages."""
        if (not re.fullmatch(r"\.dms-package-[a-z0-9_]{8}\.tmp", self.path.name)
                or self.path.is_symlink()):
            raise PackageError("Candidato de pacote inválido para limpeza.")
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise PackageError("Não foi possível remover o candidato criptografado.") from None


def stage_transfer_package(
    csv_staging: Path, transaction: VaultTransaction, request: PackageStagingRequest, *,
    scalar_codes: Iterable[str], composite_codes: Iterable[str],
    expected_binding: MaskedFileBinding | None = None,
    should_cancel: Callable[[], bool] | None = None,
    destination_directory: Path | None = None,
) -> StagedPackage:
    """Resolve own writes, encrypt/fsync, decrypt-verify and bind before commit.

    Success transfers candidate ownership to the caller. After a proven rollback
    it can be discarded; after an uncertain commit it must be retained for now.
    Cancellation is cooperative between selection, KDF/AEAD and I/O stages.
    """
    def cancelled():
        if should_cancel is not None and should_cancel():
            raise PackageStagingCancelled("A preparação do pacote foi cancelada.")

    staged = None
    try:
        limit = payload_limit(request.max_payload_bytes)
        cancelled()
        binding = expected_binding if expected_binding is not None else compute_file_binding(csv_staging)
        selected = select_transaction_mappings(transaction, scalar_codes=scalar_codes, composite_codes=composite_codes)
        cancelled()
        payload = selected.to_payload(binding, app_version=request.application_version, max_payload_bytes=limit)
        encrypted = encrypt_package(payload, request.password, max_payload_bytes=limit)
        cancelled()
        with tempfile.NamedTemporaryFile(dir=destination_directory or csv_staging.parent, prefix=".dms-package-", suffix=".tmp", delete=False) as stream:
            staged = StagedPackage(Path(stream.name), binding,
                                   MaskedFileBinding(hashlib.sha256(encrypted).hexdigest(), len(encrypted)))
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        del encrypted
        cancelled()
        # Equality verifies all mappings/identities as well as format and binding;
        # do not make another full collection of emitted codes just for checking.
        if read_package(staged.path, request.password, max_payload_bytes=limit) != payload:
            raise PackageError("O candidato do pacote não corresponde à seleção esperada.")
        verify_file_binding(csv_staging, binding)
        verify_file_binding(staged.path, staged.ciphertext)
        cancelled()
        return staged
    except BaseException as error:
        if staged is not None:
            staged.discard()
        if isinstance(error, (PackageError, KeyboardInterrupt, SystemExit)):
            raise
        raise PackageError("Não foi possível preparar o candidato criptografado do pacote.") from None
