from data_mask_studio.vault.database import default_database_path, default_vault_directory
from data_mask_studio.vault.encryption import VaultCipher
from data_mask_studio.vault.key_provider import VaultKeyProvider
from data_mask_studio.vault.repository import VaultRepository
from data_mask_studio.vault.exceptions import VaultError
from data_mask_studio.security.key_provider import LocalKeyProvider
from data_mask_studio.publication import PublicationError
from data_mask_studio.environment import guarded


@guarded(lambda: default_vault_directory())
def create_default_vault_repository() -> VaultRepository:
    """Cria o repositório usando a chave exclusiva protegida pelo DPAPI."""
    LocalKeyProvider(default_vault_directory()).get_key()
    key = VaultKeyProvider(default_vault_directory()).get_key()
    # Factories run inside producer shared leases. Recovery belongs to exclusive
    # startup/restore, never a shared-to-exclusive upgrade in an active worker.
    operations = default_vault_directory() / "publication-operations"
    if operations.is_symlink() or any(operations.glob(".dms-operation-*.json")):
        raise PublicationError("Há uma publicação pendente. Reabra a aplicação para recuperação.")
    return VaultRepository(default_database_path(), VaultCipher(key))


@guarded(lambda: default_vault_directory())
def create_default_read_only_vault_repository() -> VaultRepository:
    """Abre o cofre existente com protecao de leitura do SQLite."""
    key_provider = VaultKeyProvider(default_vault_directory())
    if not key_provider.key_path.is_file():
        raise VaultError("A chave do cofre local nao foi encontrada.")
    LocalKeyProvider(default_vault_directory()).load_existing_key()
    key = key_provider.load_existing_key()
    return VaultRepository(
        default_database_path(),
        VaultCipher(key),
        read_only=True,
    )
