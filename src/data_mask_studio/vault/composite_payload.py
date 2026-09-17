"""Payload canônico = Composite Identity v1; tuple original usa magic DMSOT.

Ambos: magic(5), versão u8, count u32 BE, pares len u64 BE + UTF-8.
AAD: array JSON UTF-8 compacto, sem escape ASCII, em ordem fixa:
domínio, aad_version, tipo, code, prefix, identity_version, payload_version,
count, regras ordenadas, parent_code, variation_id.
"""
import json

from data_mask_studio.vault.exceptions import VaultError

COMPOSITE_AAD_VERSION = 1
COMPOSITE_PAYLOAD_VERSION = 1


def encode_tuple(values: tuple[str, ...]) -> bytes:
    from data_mask_studio.processing.composite_identity import serialize_composite_identity
    return b"DMSOT" + serialize_composite_identity(values)[5:]


def decode_payload(payload: bytes, *, original: bool = False) -> tuple[str, ...]:
    try:
        magic = b"DMSOT" if original else b"DMSCI"
        if len(payload) < 10 or payload[:6] != magic + b"\x01":
            raise ValueError
        count = int.from_bytes(payload[6:10], "big")
        if count < 2 or count > (len(payload) - 10) // 8:
            raise ValueError
        values: list[str] = []
        offset = 10
        for _ in range(count):
            if offset + 8 > len(payload):
                raise ValueError
            length = int.from_bytes(payload[offset:offset + 8], "big")
            offset += 8
            if length > len(payload) - offset:
                raise ValueError
            values.append(payload[offset:offset + length].decode("utf-8"))
            offset += length
        if offset != len(payload):
            raise ValueError
        return tuple(values)
    except (ValueError, TypeError):
        raise VaultError("Payload composto inválido.") from None


def composite_aad(metadata, identifier: str | None = None) -> bytes:
    fields = [
        "data-mask-studio-composite-vault-aad", metadata["aad_version"],
        "composite_mapping" if identifier is None else "composite_variation",
        metadata["code"], metadata["prefix"], metadata["identity_version"],
        metadata["payload_version"], metadata["component_count"],
        json.loads(metadata["rules"]),
        None if identifier is None else metadata["code"], identifier,
    ]
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
