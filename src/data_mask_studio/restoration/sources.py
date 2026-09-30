"""Typed, strict restoration lookups; no source selection or fallback policy."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from collections.abc import Iterator, Sequence
from typing import Protocol
import sqlite3

from data_mask_studio.environment import EnvironmentError
from data_mask_studio.restoration.exceptions import MissingCodeError, RestorationSecurityError
from data_mask_studio.vault.models import DecryptedVaultMapping
from data_mask_studio.vault.composite_models import CompositeMapping
from data_mask_studio.vault.repository import VaultRepository, VaultReadSession
from data_mask_studio.vault.exceptions import VaultError
from data_mask_studio.performance import RestorationMetrics


@dataclass(frozen=True, slots=True)
class ScalarValue:
    code: str = field(repr=False)
    original_value: str = field(repr=False)
    canonical_value: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CompositeValue:
    code: str = field(repr=False)
    identity_version: int
    canonical_values: tuple[str, ...] = field(repr=False)
    original_values: tuple[str, ...] | None = field(repr=False)


class RestorationSource(Protocol):
    def get_scalar(self, code: str) -> ScalarValue: ...

    def get_composite(self, code: str) -> CompositeValue: ...

    def get_many_with_composites(self, codes: Sequence[str]) -> dict[str, ScalarValue | CompositeValue]: ...


class VaultRestorationSource:
    """Borrow one read session. Usable only inside open_vault_source()."""

    def __init__(self, session: VaultReadSession):
        self._session = session

    def _lookup(self, code: str, expected_type):
        if self._session is None:
            raise RestorationSecurityError("A fonte de restauração está fechada.")
        try:
            mapping = self._session.get_many_with_composites([code]).get(code)
        except (VaultError, EnvironmentError, sqlite3.Error, OSError):
            raise RestorationSecurityError("Não foi possível consultar o mapeamento com segurança.") from None
        if mapping is None:
            raise MissingCodeError("Mapeamento requerido ausente na fonte de restauração.")
        if not isinstance(mapping, expected_type) or mapping.code != code:
            raise RestorationSecurityError("Tipo de mapeamento incompatível com a consulta.")
        return mapping

    def get_scalar(self, code: str) -> ScalarValue:
        mapping = self._lookup(code, DecryptedVaultMapping)
        return ScalarValue(mapping.code, mapping.original_value, mapping.canonical_value)

    def get_composite(self, code: str) -> CompositeValue:
        mapping = self._lookup(code, CompositeMapping)
        return CompositeValue(mapping.code, mapping.identity_version, mapping.canonical_values,
                              mapping.variations[0].original_values if len(mapping.variations) == 1 else None)

    def get_many_with_composites(self, codes: Sequence[str]) -> dict[str, ScalarValue | CompositeValue]:
        if self._session is None:
            raise RestorationSecurityError("A fonte de restauração está fechada.")
        try:
            mappings = self._session.get_many_with_composites(codes)
            result = {}
            for code, mapping in mappings.items():
                if mapping.code != code:
                    raise RestorationSecurityError("Identidade de mapeamento inconsistente.")
                if isinstance(mapping, CompositeMapping):
                    result[code] = CompositeValue(code, mapping.identity_version, mapping.canonical_values,
                        mapping.variations[0].original_values if len(mapping.variations) == 1 else None)
                elif isinstance(mapping, DecryptedVaultMapping):
                    result[code] = ScalarValue(code, mapping.original_value, mapping.canonical_value)
                else:
                    raise RestorationSecurityError("Tipo de mapeamento inválido.")
            return result
        except (VaultError, EnvironmentError, sqlite3.Error, OSError):
            raise RestorationSecurityError("Não foi possível consultar os mapeamentos com segurança.") from None


@contextmanager
def open_vault_source(repository: VaultRepository, metrics: RestorationMetrics | None = None) -> Iterator[RestorationSource]:
    """Read-only connection and pinned snapshot, never migrate or write a vault."""
    with repository.as_read_only().read_session(metrics) as session:
        source = VaultRestorationSource(session)
        try:
            yield source
        finally:
            source._session = None
