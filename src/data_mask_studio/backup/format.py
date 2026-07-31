import base64
import json
import struct
from pathlib import Path
from typing import BinaryIO

from data_mask_studio.backup.crypto import NONCE_SIZE, SCRYPT_PARAMETERS
from data_mask_studio.backup.exceptions import BackupCompatibilityError, BackupValidationError
from data_mask_studio.backup.models import BackupHeader, ScryptParameters

MAGIC = b"DMSBACKUP\x00"
FORMAT_VERSION = 1
MAX_HEADER_SIZE = 4096
PREFIX_STRUCT = struct.Struct(">10sHI")


def build_prefix(header: BackupHeader) -> bytes:
    document = {
        "cipher": "AES-256-GCM",
        "kdf": {
            "name": "scrypt",
            "n": header.scrypt.n,
            "r": header.scrypt.r,
            "p": header.scrypt.p,
            "length": header.scrypt.length,
            "salt_size": header.scrypt.salt_size,
        },
        "salt": base64.b64encode(header.salt).decode("ascii"),
        "nonce": base64.b64encode(header.nonce).decode("ascii"),
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )
    if len(encoded) > MAX_HEADER_SIZE:
        raise BackupValidationError("O cabeçalho do backup é inválido.")
    return PREFIX_STRUCT.pack(MAGIC, header.format_version, len(encoded)) + encoded


def read_prefix(file: BinaryIO) -> tuple[BackupHeader, bytes]:
    fixed = file.read(PREFIX_STRUCT.size)
    if len(fixed) != PREFIX_STRUCT.size:
        raise BackupValidationError("O arquivo não é um backup válido.")
    magic, version, header_size = PREFIX_STRUCT.unpack(fixed)
    if magic != MAGIC:
        raise BackupValidationError("O arquivo não é um backup válido.")
    if version != FORMAT_VERSION:
        raise BackupCompatibilityError("A versão do backup não é suportada.")
    if not 1 <= header_size <= MAX_HEADER_SIZE:
        raise BackupValidationError("O cabeçalho do backup é inválido.")
    encoded = file.read(header_size)
    if len(encoded) != header_size:
        raise BackupValidationError("O cabeçalho do backup está incompleto.")
    try:
        document = json.loads(encoded.decode("ascii"))
        kdf = document["kdf"]
        if document["cipher"] != "AES-256-GCM" or kdf["name"] != "scrypt":
            raise ValueError
        parameters = ScryptParameters(
            n=_integer(kdf["n"]),
            r=_integer(kdf["r"]),
            p=_integer(kdf["p"]),
            length=_integer(kdf["length"]),
            salt_size=_integer(kdf["salt_size"]),
        )
        salt = base64.b64decode(document["salt"], validate=True)
        nonce = base64.b64decode(document["nonce"], validate=True)
        if len(salt) != parameters.salt_size or len(nonce) != NONCE_SIZE:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BackupValidationError("O cabeçalho do backup é inválido.") from error
    header = BackupHeader(version, parameters, salt, nonce)
    return header, fixed + encoded


def signature_matches(path: str | Path) -> bool:
    try:
        with Path(path).open("rb") as file:
            return file.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError
    return value
