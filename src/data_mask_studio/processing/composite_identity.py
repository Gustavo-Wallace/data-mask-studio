"""Identidade composta v1, independente de planejamento e normalização.

Serialização: b'DMSCI' | versão u8 | quantidade u32 BE |
              (comprimento UTF-8 u64 BE | bytes UTF-8) por componente.
Mensagem HMAC: b'\xffDMS-COMPOSITE-HMAC\x00' | comprimento do prefixo u32 BE |
              prefixo ASCII | serialização acima.
O byte inicial FF torna o domínio disjunto das mensagens UTF-8 escalares.
Limites são os dos inteiros do formato, não limites operacionais de CSV.
"""

import base64
from collections.abc import Sequence
import hashlib
import hmac

from data_mask_studio.anonymization.prefix_rules import validate_prefix

COMPOSITE_IDENTITY_VERSION = 1
COMPOSITE_MAGIC = b"DMSCI"
COMPOSITE_HMAC_DOMAIN = b"\xffDMS-COMPOSITE-HMAC\x00"


class CompositeIdentityError(ValueError):
    """Entrada inválida; mensagens nunca incluem valores canônicos ou chaves."""


def serialize_composite_identity(
    canonical_values: Sequence[str], *, version: int = COMPOSITE_IDENTITY_VERSION,
) -> bytes:
    """Enquadra strings exatas, incluindo vazias; não executa HMAC nem I/O."""
    if type(version) is not int or version != COMPOSITE_IDENTITY_VERSION:
        raise CompositeIdentityError("Versão de identidade composta não suportada.")
    if isinstance(canonical_values, (str, bytes, bytearray)) or not isinstance(canonical_values, Sequence):
        raise CompositeIdentityError("Informe uma sequência ordenada de strings.")
    count = len(canonical_values)
    if not 2 <= count <= 0xFFFFFFFF:
        raise CompositeIdentityError("Quantidade de componentes fora do contrato composto.")
    payload = bytearray(COMPOSITE_MAGIC + bytes([version]) + count.to_bytes(4, "big"))
    for value in canonical_values:
        if not isinstance(value, str):
            raise CompositeIdentityError("Todo componente deve ser uma string.")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise CompositeIdentityError("Componente não representável em UTF-8.") from None
        if len(encoded) > 0xFFFFFFFFFFFFFFFF:
            raise CompositeIdentityError("Componente excede o comprimento do formato.")
        payload.extend(len(encoded).to_bytes(8, "big"))
        payload.extend(encoded)
    return bytes(payload)


def generate_composite_token(
    key: bytes, prefix: str, canonical_values: Sequence[str], *,
    version: int = COMPOSITE_IDENTITY_VERSION,
) -> str:
    """HMAC-SHA256 composto; não define política de saída para linhas vazias."""
    if not isinstance(key, bytes) or not key:
        raise CompositeIdentityError("Informe uma chave HMAC não vazia em bytes.")
    if not isinstance(prefix, str):
        raise CompositeIdentityError("Prefixo inválido.")
    error = validate_prefix(prefix)
    if error:
        raise CompositeIdentityError(error)
    payload = serialize_composite_identity(canonical_values, version=version)
    namespace = prefix.encode("ascii")
    message = COMPOSITE_HMAC_DOMAIN + len(namespace).to_bytes(4, "big") + namespace + payload
    digest = hmac.new(key, message, hashlib.sha256).digest()
    code = base64.b32encode(digest).decode("ascii").rstrip("=")[:12]
    return f"{prefix}-{code}"
