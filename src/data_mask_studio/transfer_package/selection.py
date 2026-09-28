"""Resolve caller-supplied identities, never discover tokens or export a vault."""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from data_mask_studio.environment import EnvironmentError, guarded
from data_mask_studio.metadata import application_version
from data_mask_studio.performance import BALANCED_SETTINGS
from data_mask_studio.transfer_package.models import (
    CompositeRestorationMapping, MaskedFileBinding, PackageError,
    ScalarRestorationMapping, TransferPayload,
)
from data_mask_studio.transfer_package.serialization import (
    MAX_PAYLOAD_SIZE, _code, validate_payload,
)
from data_mask_studio.vault.composite_models import CompositeMapping
from data_mask_studio.vault.exceptions import VaultError
from data_mask_studio.vault.models import DecryptedVaultMapping
from data_mask_studio.vault.repository import VaultRepository


@dataclass(frozen=True, slots=True)
class SelectedMappings:
    scalar_mappings: tuple[ScalarRestorationMapping, ...] = field(repr=False)
    composite_mappings: tuple[CompositeRestorationMapping, ...] = field(repr=False)

    def to_payload(
        self, binding: MaskedFileBinding, *, app_version: str | None = None,
        max_payload_bytes: int = MAX_PAYLOAD_SIZE,
    ) -> TransferPayload:
        """Binding is caller supplied, not calculated or verified against a file."""
        if not self.scalar_mappings and not self.composite_mappings:
            raise PackageError("Selecione ao menos um mapeamento para transferência.")
        payload = TransferPayload(
            application_version() if app_version is None else app_version, binding,
            self.scalar_mappings, self.composite_mappings,
        )
        validate_payload(payload, max_payload_bytes=max_payload_bytes)
        return payload


def _identities(values: Iterable[str], *, composite: bool) -> set[str]:
    try:
        if isinstance(values, (str, bytes)):
            raise ValueError
        # Reuse the explicit v1 identity contract, including legacy scalars.
        return {_code(value, composite=composite) for value in values}
    except (TypeError, ValueError):
        raise PackageError("Identidade requerida inválida para transferência.") from None


def select_mappings(
    repository: VaultRepository, *, scalar_codes: Iterable[str] = (),
    composite_codes: Iterable[str] = (),
) -> SelectedMappings:
    """Strict typed lookup; stable code order, no plaintext/token diagnostics.

    All lookup batches share a read-only session and its pinned WAL snapshot.
    Mixed lookup also detects an identity present in both vault namespaces.
    """
    scalars = _identities(scalar_codes, composite=False)
    composites = _identities(composite_codes, composite=True)
    if not scalars and not composites:
        raise PackageError("Selecione ao menos um mapeamento para transferência.")
    if scalars & composites:
        raise PackageError("Identidade requerida com tipos conflitantes.")
    try:
        return _select_from_snapshot(repository, scalars, composites)
    except (VaultError, EnvironmentError, sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        raise PackageError("Não foi possível extrair todos os mapeamentos requeridos com segurança.") from None


@guarded(lambda repository, scalars, composites: repository.database_path.parent)
def _select_from_snapshot(
    repository: VaultRepository, scalars: set[str], composites: set[str],
) -> SelectedMappings:
    scalar_result: list[ScalarRestorationMapping] = []
    composite_result: list[CompositeRestorationMapping] = []
    ordered = sorted(scalars | composites)
    size = BALANCED_SETTINGS.sqlite_lookup_batch_size
    with repository.as_read_only().read_session() as session:
        for offset in range(0, len(ordered), size):
            batch = ordered[offset:offset + size]
            found = session.get_many_with_composites(batch)
            if set(found) != set(batch):
                raise PackageError("Há mapeamentos requeridos ausentes ou inconsistentes.")
            for code in batch:
                mapping = found[code]
                if mapping.code != code:
                    raise PackageError("Identidade retornada pelo cofre inconsistente.")
                if code in scalars:
                    if not isinstance(mapping, DecryptedVaultMapping):
                        raise PackageError("Tipo de mapeamento incompatível com a seleção.")
                    scalar_result.append(ScalarRestorationMapping.from_vault(mapping))
                else:
                    if not isinstance(mapping, CompositeMapping):
                        raise PackageError("Tipo de mapeamento incompatível com a seleção.")
                    composite_result.append(CompositeRestorationMapping.from_vault(mapping))
    return SelectedMappings(tuple(scalar_result), tuple(composite_result))
