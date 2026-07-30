import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from data_mask_studio.vault.exceptions import VaultEncryptionError
from data_mask_studio.vault.models import EncryptedValue

VAULT_KEY_SIZE = 32
NONCE_SIZE = 12


class VaultCipher:
    """Criptografa valores do cofre com AES-256-GCM."""

    def __init__(self, key: bytes) -> None:
        if len(key) != VAULT_KEY_SIZE:
            raise VaultEncryptionError("A chave criptográfica do cofre é inválida.")
        self._cipher = AESGCM(key)

    def encrypt(self, code: str, prefix: str, original_value: str) -> EncryptedValue:
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._cipher.encrypt(
            nonce,
            original_value.encode("utf-8"),
            _associated_data(code, prefix),
        )
        return EncryptedValue(ciphertext=ciphertext, nonce=nonce)

    def decrypt(
        self,
        code: str,
        prefix: str,
        encrypted_value: bytes,
        nonce: bytes,
    ) -> str:
        try:
            plaintext = self._cipher.decrypt(
                nonce,
                encrypted_value,
                _associated_data(code, prefix),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError) as error:
            raise VaultEncryptionError(
                "Não foi possível autenticar um registro do cofre local."
            ) from error


def _associated_data(code: str, prefix: str) -> bytes:
    return f"{code}\0{prefix}".encode("utf-8")

