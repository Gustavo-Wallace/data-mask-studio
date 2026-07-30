import os
import secrets
import tempfile
from pathlib import Path
from typing import Protocol

from data_mask_studio.security.windows_dpapi import WindowsDPAPIProtector

KEY_SIZE = 32
KEY_FILE_NAME = "secret.key"


class DataProtector(Protocol):
    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, data: bytes) -> bytes: ...


class KeyProvider(Protocol):
    def get_key(self) -> bytes: ...


class KeyProviderError(RuntimeError):
    """Falha ao criar ou recuperar a chave local protegida."""


class LocalKeyProvider:
    """Mantém uma chave aleatória protegida pelo DPAPI fora do projeto."""

    def __init__(
        self,
        storage_directory: Path | None = None,
        protector: DataProtector | None = None,
    ) -> None:
        self._storage_directory = storage_directory or _default_storage_directory()
        self._protector = protector or WindowsDPAPIProtector()

    @property
    def key_path(self) -> Path:
        return self._storage_directory / KEY_FILE_NAME

    def get_key(self) -> bytes:
        try:
            if self.key_path.exists():
                protected_key = self.key_path.read_bytes()
                key = self._protector.unprotect(protected_key)
                if len(key) != KEY_SIZE:
                    raise KeyProviderError("A chave local protegida possui formato inválido.")
                return key
            return self._create_key()
        except KeyProviderError:
            raise
        except (OSError, RuntimeError) as error:
            raise KeyProviderError(
                "Não foi possível acessar a chave secreta local."
            ) from error

    def _create_key(self) -> bytes:
        self._storage_directory.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(KEY_SIZE)
        protected_key = self._protector.protect(key)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".secret-",
                suffix=".tmp",
                dir=self._storage_directory,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(protected_key)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.key_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return key


def _default_storage_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise KeyProviderError("A pasta LOCALAPPDATA do Windows não está disponível.")
    return Path(local_app_data) / "DataMaskStudio"

