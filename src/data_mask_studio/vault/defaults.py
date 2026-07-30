from data_mask_studio.vault.database import default_database_path, default_vault_directory
from data_mask_studio.vault.encryption import VaultCipher
from data_mask_studio.vault.key_provider import VaultKeyProvider
from data_mask_studio.vault.repository import VaultRepository


def create_default_vault_repository() -> VaultRepository:
    """Cria o repositório usando a chave exclusiva protegida pelo DPAPI."""
    key = VaultKeyProvider(default_vault_directory()).get_key()
    return VaultRepository(default_database_path(), VaultCipher(key))
