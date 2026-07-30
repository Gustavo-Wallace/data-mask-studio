from pathlib import Path

from data_mask_studio.security.key_provider import DataProtector, LocalKeyProvider

VAULT_KEY_FILE_NAME = "vault_key.dpapi"


class VaultKeyProvider(LocalKeyProvider):
    """Fornece uma chave exclusiva do cofre, protegida pelo Windows DPAPI."""

    def __init__(
        self,
        storage_directory: Path | None = None,
        protector: DataProtector | None = None,
    ) -> None:
        super().__init__(
            storage_directory,
            protector,
            key_file_name=VAULT_KEY_FILE_NAME,
        )

