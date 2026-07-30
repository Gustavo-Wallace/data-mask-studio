import pytest

from data_mask_studio.vault import VaultCipher, VaultEncryptionError

KEY = b"E" * 32
CODE = "CPF_ID-ABCDEFGHI234"
PREFIX = "CPF_ID"


def test_aes_gcm_encrypts_and_decrypts_value() -> None:
    cipher = VaultCipher(KEY)
    original_value = "sensitive-test-value"

    encrypted = cipher.encrypt(CODE, PREFIX, original_value)

    assert original_value.encode("utf-8") not in encrypted.ciphertext
    assert cipher.decrypt(
        CODE, PREFIX, encrypted.ciphertext, encrypted.nonce
    ) == original_value


def test_decryption_fails_with_wrong_key() -> None:
    encrypted = VaultCipher(KEY).encrypt(CODE, PREFIX, "sensitive-test-value")

    with pytest.raises(VaultEncryptionError):
        VaultCipher(b"W" * 32).decrypt(
            CODE, PREFIX, encrypted.ciphertext, encrypted.nonce
        )


def test_tampered_ciphertext_is_detected() -> None:
    cipher = VaultCipher(KEY)
    encrypted = cipher.encrypt(CODE, PREFIX, "sensitive-test-value")
    tampered = bytes([encrypted.ciphertext[0] ^ 1]) + encrypted.ciphertext[1:]

    with pytest.raises(VaultEncryptionError):
        cipher.decrypt(CODE, PREFIX, tampered, encrypted.nonce)


def test_associated_code_and_prefix_are_authenticated() -> None:
    cipher = VaultCipher(KEY)
    encrypted = cipher.encrypt(CODE, PREFIX, "sensitive-test-value")

    with pytest.raises(VaultEncryptionError):
        cipher.decrypt("OTHER-ABCDEFGHI234", PREFIX, encrypted.ciphertext, encrypted.nonce)

