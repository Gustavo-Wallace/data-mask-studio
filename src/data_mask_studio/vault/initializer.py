from pathlib import Path

from data_mask_studio.security import KeyProvider
from data_mask_studio.vault.database import initialize_schema
from data_mask_studio.vault.encryption import VaultCipher


def initialize_existing_vault(
    database_path: str | Path,
    key_provider: KeyProvider,
) -> bool:
    """Prepara um cofre existente antes de liberar operações da aplicação."""
    path = Path(database_path)
    if not path.is_file():
        return False
    initialize_schema(path, VaultCipher(key_provider.get_key()))
    return True
