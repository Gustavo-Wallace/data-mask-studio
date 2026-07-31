from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from data_mask_studio.backup.exceptions import BackupError
from data_mask_studio.backup.models import ScryptParameters

SCRYPT_PARAMETERS = ScryptParameters()
NONCE_SIZE = 12
TAG_SIZE = 16


def validate_password(password: str, confirmation: str | None = None) -> None:
    if len(password) < 12 or not password.strip():
        raise BackupError("A senha do backup deve possuir pelo menos 12 caracteres.")
    if confirmation is not None and password != confirmation:
        raise BackupError("A senha e a confirmação não são idênticas.")


def derive_key(
    password: str,
    salt: bytes,
    parameters: ScryptParameters = SCRYPT_PARAMETERS,
) -> bytes:
    validate_scrypt_parameters(parameters)
    password_buffer = bytearray(password.encode("utf-8"))
    try:
        return Scrypt(
            salt=salt,
            length=parameters.length,
            n=parameters.n,
            r=parameters.r,
            p=parameters.p,
        ).derive(bytes(password_buffer))
    finally:
        password_buffer[:] = b"\x00" * len(password_buffer)


def validate_scrypt_parameters(parameters: ScryptParameters) -> None:
    if (
        parameters.n != SCRYPT_PARAMETERS.n
        or parameters.r != SCRYPT_PARAMETERS.r
        or parameters.p != SCRYPT_PARAMETERS.p
        or parameters.length != SCRYPT_PARAMETERS.length
        or parameters.salt_size != SCRYPT_PARAMETERS.salt_size
    ):
        raise BackupError("Os parâmetros criptográficos do backup são inválidos.")
