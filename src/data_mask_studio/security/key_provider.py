import os
import secrets
import tempfile
from pathlib import Path
from typing import Protocol
from data_mask_studio.environment import guarded, GENERATION_FILE

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


class MissingKeyError(KeyProviderError):
    """Chave ausente; somente um ambiente novo pode criá-la."""


class LocalKeyProvider:
    """Mantém uma chave aleatória protegida pelo DPAPI fora do projeto."""

    def __init__(
        self,
        storage_directory: Path | None = None,
        protector: DataProtector | None = None,
        *,
        key_file_name: str = KEY_FILE_NAME,
    ) -> None:
        self._storage_directory = storage_directory or _default_storage_directory()
        self._protector = protector or WindowsDPAPIProtector()
        self._key_file_name = key_file_name

    @property
    def key_path(self) -> Path:
        return self._storage_directory / self._key_file_name

    @guarded(lambda self: self._storage_directory)
    def get_key(self) -> bytes:
        try:
            try:
                return self.load_existing_key()
            except MissingKeyError:
                self._require_new_environment()
            return self._create_key()
        except KeyProviderError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise KeyProviderError(
                "Não foi possível acessar a chave secreta local."
            ) from error

    @guarded(lambda self: self._storage_directory)
    def load_existing_key(self) -> bytes:
        """Carrega sem criar; ausência é distinta de falha de formato/DPAPI."""
        try:
            protected_key = self.key_path.read_bytes()
        except FileNotFoundError as error:
            if self.key_path.is_symlink():
                raise KeyProviderError("A chave local protegida está indisponível.") from error
            raise MissingKeyError(
                "A chave do ambiente está ausente ou indisponível. "
                "Restaure um backup válido do ambiente."
            ) from error
        except OSError as error:
            raise KeyProviderError("Não foi possível acessar a chave local protegida.") from error
        try:
            key = self._protector.unprotect(protected_key)
        except (OSError, RuntimeError, ValueError) as error:
            raise KeyProviderError(
                "Não foi possível recuperar a chave local protegida."
            ) from error
        if not isinstance(key, bytes) or len(key) != KEY_SIZE:
            raise KeyProviderError("A chave local protegida possui formato inválido.")
        return key

    def _require_new_environment(self) -> None:
        # Mesmo um banco vazio ou apenas seus sidecars é estado persistente.
        # Perfis e a outra chave não dependem da chave ausente: uma criação
        # interrompida antes do banco pode ser concluída sem substituí-los.
        for name in ("vault.db", "vault.db-wal", "vault.db-shm", "vault.db-journal", GENERATION_FILE):
            try:
                (self._storage_directory / name).lstat()
            except FileNotFoundError:
                continue
            # O vencedor pode ter publicado chave + banco desde nossa leitura.
            # Nesse caso não confundir concorrência legítima com chave perdida.
            self.load_existing_key()
            return

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
            # Outro inicializador pode ter concluído enquanto protegíamos os
            # bytes. Nunca publicar sobre uma identidade já persistida.
            try:
                return self.load_existing_key()
            except MissingKeyError:
                self._require_new_environment()
            try:
                # Hard link no mesmo volume: publicação atômica sem replace,
                # suportada pelo Windows/NTFS. Não há fallback inseguro em FS
                # sem esse recurso. O conteúdo já foi fechado e fsync'ed.
                os.link(temporary_path, self.key_path)
            except FileExistsError:
                return self.load_existing_key()
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return key


def _default_storage_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise KeyProviderError("A pasta LOCALAPPDATA do Windows não está disponível.")
    return Path(local_app_data) / "DataMaskStudio"
