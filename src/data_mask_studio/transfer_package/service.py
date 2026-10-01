"""v1: magic | version (u16 BE) | salt (16) | nonce (12) | ciphertext+tag.

The complete fixed header is AAD. KDF/cipher parameters are fixed by v1,
never accepted from untrusted input. Payload plaintext exists only in memory.
"""

import os
import struct
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from data_mask_studio.publication import publish
from data_mask_studio.transfer_package.models import FORMAT_VERSION, PackageError, TransferPayload
from data_mask_studio.transfer_package.serialization import MAX_PAYLOAD_SIZE, decode_payload, encode_payload, payload_limit

MAGIC = b"DMSTRANSFER\x00"
KDF_DOMAIN = b"data-mask-studio-transfer-package-v1\x00"
HEADER = struct.Struct(f">{len(MAGIC)}sH16s12s")
MIN_PASSWORD_LENGTH = 8


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH or not password.strip():
        raise PackageError(f"A senha do pacote deve possuir pelo menos {MIN_PASSWORD_LENGTH} caracteres.")


def _key(password: str, salt: bytes) -> bytes:
    validate_password(password)
    try:
        # Same cost as backups; separate salt domain and no backup dependencies.
        return Scrypt(salt=KDF_DOMAIN + salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))
    except (ValueError, TypeError):
        raise PackageError("Não foi possível derivar a chave do pacote.") from None


def encrypt_package(payload: TransferPayload, password: str, *, max_payload_bytes: int = MAX_PAYLOAD_SIZE) -> bytes:
    plaintext = encode_payload(payload, max_payload_bytes=max_payload_bytes)
    salt, nonce = os.urandom(16), os.urandom(12)
    header = HEADER.pack(MAGIC, FORMAT_VERSION, salt, nonce)
    return header + AESGCM(_key(password, salt)).encrypt(nonce, plaintext, header)


def decrypt_package(content: bytes, password: str, *, max_payload_bytes: int = MAX_PAYLOAD_SIZE) -> TransferPayload:
    limit = payload_limit(max_payload_bytes)
    if isinstance(content, bytes) and len(content) > HEADER.size + limit + 16:
        raise PackageError("O pacote excede o limite de memória configurado.")
    try:
        if not isinstance(content, bytes) or len(content) < HEADER.size + 16:
            raise ValueError
        magic, version, salt, nonce = HEADER.unpack(content[:HEADER.size])
        if magic != MAGIC or version != FORMAT_VERSION:
            raise ValueError
        plaintext = AESGCM(_key(password, salt)).decrypt(nonce, content[HEADER.size:], content[:HEADER.size])
    except (ValueError, TypeError, InvalidTag, struct.error):
        raise PackageError("Não foi possível autenticar o pacote. Verifique a senha e a integridade do arquivo.") from None
    return decode_payload(plaintext, max_payload_bytes=limit)


def read_package(path: str | Path, password: str, *, max_payload_bytes: int = MAX_PAYLOAD_SIZE) -> TransferPayload:
    limit = payload_limit(max_payload_bytes)
    try:
        with Path(path).open("rb") as stream:
            content = stream.read(HEADER.size + limit + 16 + 1)
    except OSError:
        raise PackageError("Não foi possível ler o pacote de transferência.") from None
    return decrypt_package(content, password, max_payload_bytes=limit)


def write_package(path: str | Path, payload: TransferPayload, password: str, *, max_payload_bytes: int = MAX_PAYLOAD_SIZE) -> Path:
    """Atomic create-if-absent; deliberately no overwrite option in v1."""
    destination = Path(path).absolute()
    if destination.suffix.lower() != ".dmspackage":
        raise PackageError("Use a extensão .dmspackage para o pacote.")
    content = encrypt_package(payload, password, max_payload_bytes=max_payload_bytes)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".dms-package-", suffix=".tmp", delete=False) as stream:
            staged = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        publish(staged, destination, False)
        return destination
    except OSError:
        raise PackageError("Não foi possível publicar o pacote sem sobrescrever o destino.") from None
    finally:
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                raise PackageError("Não foi possível remover o temporário criptografado do pacote.") from None
