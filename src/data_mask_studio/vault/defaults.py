from data_mask_studio.vault.database import default_database_path, default_vault_directory
from data_mask_studio.vault.encryption import VaultCipher
from data_mask_studio.vault.key_provider import VaultKeyProvider
from data_mask_studio.vault.repository import VaultRepository
from data_mask_studio.vault.exceptions import VaultError


def create_default_vault_repository() -> VaultRepository:
    """Cria o repositório usando a chave exclusiva protegida pelo DPAPI."""
    key = VaultKeyProvider(default_vault_directory()).get_key()
    return VaultRepository(default_database_path(), VaultCipher(key))


def create_default_read_only_vault_repository() -> VaultRepository:
    """Abre o cofre existente com protecao de leitura do SQLite."""
    key_provider = VaultKeyProvider(default_vault_directory())
    if not key_provider.key_path.is_file():
        raise VaultError("A chave do cofre local nao foi encontrada.")
    key = key_provider.get_key()
    return VaultRepository(
        default_database_path(),
        VaultCipher(key),
        read_only=True,
    )
