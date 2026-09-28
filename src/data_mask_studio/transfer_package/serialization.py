"""Explicit v1 schema, independent of dataclass fields and vault schema.

All keys required; unknown keys fail closed. No re-normalization.
64 MiB is a conservative in-memory default, not a persisted format ceiling.
Trusted callers can budget for larger JSON/object/AEAD allocations, up to
1 GiB (below AESGCM's signed-int input limit). Files cannot choose limits.
"""
import json
import re

from data_mask_studio.transfer_package.models import (
    FORMAT_NAME, FORMAT_VERSION, MaskedFileBinding, PackageError, TransferPayload,
    ScalarRestorationMapping, CompositeRestorationMapping,
)

MAX_PAYLOAD_SIZE = 64 * 1024 * 1024
HARD_PAYLOAD_LIMIT = 1024 * 1024 * 1024


def payload_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= HARD_PAYLOAD_LIMIT:
        raise PackageError("Limite de memória do pacote inválido.")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected.split()):
        raise ValueError


def _text(value):
    if type(value) is not str:
        raise ValueError
    value.encode("utf-8")
    return value


def _integer(value):
    if type(value) is not int:
        raise ValueError
    return value


def _list(value):
    if type(value) is not list:
        raise ValueError
    return value


def _values(value):
    return tuple(_text(v) for v in _list(value))


def _code(value, *, composite=False):
    suffix = r"[A-Z2-7]{12}" if composite else r"(?:[A-Z2-7]{12}|[0-9A-F]{24})"
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,23}-" + suffix, _text(value)):
        raise ValueError
    return value


def _decode(doc):
    _keys(doc, "format_name format_version application_version masked_file scalar_mappings composite_mappings")
    if _text(doc["format_name"]) != FORMAT_NAME or _integer(doc["format_version"]) != FORMAT_VERSION:
        raise ValueError
    version = _text(doc["application_version"])
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError
    binding = doc["masked_file"]
    _keys(binding, "sha256 size")
    if not re.fullmatch(r"[0-9a-f]{64}", _text(binding["sha256"])) or _integer(binding["size"]) < 0:
        raise ValueError
    scalars, composites = [], []
    for item in _list(doc["scalar_mappings"]):
        _keys(item, "code original_value canonical_value")
        scalars.append(ScalarRestorationMapping(
            code=_code(item["code"]), original_value=_text(item["original_value"]),
            canonical_value=_text(item["canonical_value"])))
    for item in _list(doc["composite_mappings"]):
        _keys(item, "code identity_version canonical_values original_values")
        if _integer(item["identity_version"]) != 1:
            raise ValueError
        canonical = _values(item["canonical_values"])
        original = None if item["original_values"] is None else _values(item["original_values"])
        if len(canonical) < 2:
            raise ValueError
        if original is not None and len(original) != len(canonical):
            raise ValueError
        composites.append(CompositeRestorationMapping(
            code=_code(item["code"], composite=True), identity_version=1,
            canonical_values=canonical, original_values=original))
    codes = [m.code for m in (*scalars, *composites)]
    if len(set(codes)) != len(codes):
        raise ValueError
    return TransferPayload(application_version=version,
                           masked_file=MaskedFileBinding(binding["sha256"], binding["size"]),
                           scalar_mappings=tuple(scalars), composite_mappings=tuple(composites))


def decode_payload(encoded: bytes, *, max_payload_bytes: int = MAX_PAYLOAD_SIZE) -> TransferPayload:
    limit = payload_limit(max_payload_bytes)
    if len(encoded) > limit:
        raise PackageError("O pacote excede o limite de memória configurado.")
    try:
        return _decode(json.loads(encoded.decode("utf-8"), object_pairs_hook=_object))
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise PackageError("Estrutura do pacote de transferência inválida ou não suportada.") from None


def encode_payload(payload: TransferPayload, *, max_payload_bytes: int = MAX_PAYLOAD_SIZE) -> bytes:
    limit = payload_limit(max_payload_bytes)
    try:
        # Explicit field selection, never asdict/fields/get_type_hints.
        doc = {
            "format_name": payload.format_name, "format_version": payload.format_version,
            "application_version": payload.application_version,
            "masked_file": {"sha256": payload.masked_file.sha256, "size": payload.masked_file.size},
            "scalar_mappings": [{"code": m.code, "original_value": m.original_value,
                                 "canonical_value": m.canonical_value} for m in payload.scalar_mappings],
            "composite_mappings": [{"code": m.code, "identity_version": m.identity_version,
                                    "canonical_values": list(m.canonical_values),
                                    "original_values": None if m.original_values is None else list(m.original_values)}
                                   for m in payload.composite_mappings],
        }
        _decode(doc)
        encoded = bytearray()
        for part in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(doc):
            block = part.encode("utf-8")
            if len(encoded) + len(block) > limit:
                raise PackageError("O pacote excede o limite de memória configurado.")
            encoded.extend(block)
        return bytes(encoded)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise PackageError("Estrutura do pacote de transferência inválida ou não suportada.") from None


def validate_payload(payload: TransferPayload, *, max_payload_bytes: int = MAX_PAYLOAD_SIZE) -> None:
    encode_payload(payload, max_payload_bytes=max_payload_bytes)
