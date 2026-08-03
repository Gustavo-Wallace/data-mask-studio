import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.vault.aad import legacy_aad, mapping_aad, variation_aad
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
        """Criptografa no formato legado, usado somente por migrações e testes."""
        return self._encrypt(original_value, legacy_aad(code, prefix))

    def encrypt_mapping(
        self,
        code: str,
        prefix: str,
        source_header: str,
        normalization_rule: NormalizationRule | str,
        original_value: str,
    ) -> EncryptedValue:
        return self._encrypt(
            original_value,
            mapping_aad(code, prefix, source_header, normalization_rule),
        )

    def encrypt_variation(
        self,
        identifier: int,
        code: str,
        prefix: str,
        source_header: str,
        normalization_rule: NormalizationRule | str,
        original_value: str,
    ) -> EncryptedValue:
        return self._encrypt(
            original_value,
            variation_aad(
                identifier, code, prefix, source_header, normalization_rule
            ),
        )

    def _encrypt(self, original_value: str, associated_data: bytes) -> EncryptedValue:
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._cipher.encrypt(
            nonce,
            original_value.encode("utf-8"),
            associated_data,
        )
        return EncryptedValue(ciphertext=ciphertext, nonce=nonce)

    def decrypt(
        self,
        code: str,
        prefix: str,
        encrypted_value: bytes,
        nonce: bytes,
    ) -> str:
        """Descriptografa o formato legado durante migrações."""
        return self._decrypt(
            encrypted_value,
            nonce,
            legacy_aad(code, prefix),
        )

    def decrypt_mapping(
        self,
        code: str,
        prefix: str,
        source_header: str,
        normalization_rule: NormalizationRule | str,
        encrypted_value: bytes,
        nonce: bytes,
    ) -> str:
        return self._decrypt(
            encrypted_value,
            nonce,
            mapping_aad(code, prefix, source_header, normalization_rule),
        )

    def decrypt_variation(
        self,
        identifier: int,
        code: str,
        prefix: str,
        source_header: str,
        normalization_rule: NormalizationRule | str,
        encrypted_value: bytes,
        nonce: bytes,
    ) -> str:
        return self._decrypt(
            encrypted_value,
            nonce,
            variation_aad(
                identifier, code, prefix, source_header, normalization_rule
            ),
        )

    def _decrypt(
        self,
        encrypted_value: bytes,
        nonce: bytes,
        associated_data: bytes,
    ) -> str:
        try:
            plaintext = self._cipher.decrypt(
                nonce,
                encrypted_value,
                associated_data,
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError) as error:
            raise VaultEncryptionError(
                "Não foi possível autenticar um registro do cofre local."
            ) from error
