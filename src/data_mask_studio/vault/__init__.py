"""Cofre local criptografado de mapeamentos anonimizados."""

from data_mask_studio.vault.defaults import (
    create_default_read_only_vault_repository,
    create_default_vault_repository,
)
from data_mask_studio.vault.encryption import VaultCipher
from data_mask_studio.vault.exceptions import (
    VaultCollisionError,
    VaultEncryptionError,
    VaultError,
)
from data_mask_studio.vault.key_provider import VaultKeyProvider
from data_mask_studio.vault.models import MappingCandidate, VaultUpdateSummary
from data_mask_studio.vault.repository import VaultRepository

__all__ = [
    "MappingCandidate",
    "VaultCipher",
    "VaultCollisionError",
    "VaultEncryptionError",
    "VaultError",
    "VaultKeyProvider",
    "VaultRepository",
    "VaultUpdateSummary",
    "create_default_vault_repository",
    "create_default_read_only_vault_repository",
]
