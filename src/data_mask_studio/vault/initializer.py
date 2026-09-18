from pathlib import Path

from data_mask_studio.security import KeyProvider
from data_mask_studio.publication import recover_publications
from data_mask_studio.environment import guarded
from data_mask_studio.vault.database import initialize_schema
from data_mask_studio.vault.encryption import VaultCipher


@guarded(lambda database_path, *args, **kwargs: Path(database_path).parent)
def initialize_existing_vault(
    database_path: str | Path,
    key_provider: KeyProvider,
    hmac_key_provider: KeyProvider | None = None,
) -> bool:
    """Prepara um cofre existente antes de liberar operações da aplicação."""
    path = Path(database_path)
    if not path.is_file():
        return False
    hmac_key = hmac_key_provider.get_key() if hmac_key_provider is not None else None
    cipher = VaultCipher(key_provider.get_key())
    if hmac_key is not None:
        recover_publications(path, hmac_key)
    initialize_schema(path, cipher)
    return True
