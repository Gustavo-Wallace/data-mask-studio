import json
from enum import StrEnum

from data_mask_studio.normalization import NormalizationRule

AAD_FORMAT_NAME = "data-mask-studio-vault-aad"
AAD_FORMAT_VERSION = 1


class VaultRecordType(StrEnum):
    MAPPING = "mapping"
    VARIATION = "variation"


def mapping_aad(
    code: str,
    prefix: str,
    source_header: str,
    normalization_rule: NormalizationRule | str,
) -> bytes:
    return _serialize(
        record_type=VaultRecordType.MAPPING,
        code=code,
        prefix=prefix,
        source_header=source_header,
        normalization_rule=_rule_value(normalization_rule),
        parent_code=None,
        record_identifier=None,
    )


def variation_aad(
    identifier: int,
    code: str,
    prefix: str,
    source_header: str,
    normalization_rule: NormalizationRule | str,
) -> bytes:
    return _serialize(
        record_type=VaultRecordType.VARIATION,
        code=code,
        prefix=prefix,
        source_header=source_header,
        normalization_rule=_rule_value(normalization_rule),
        parent_code=code,
        record_identifier=identifier,
    )


def legacy_aad(code: str, prefix: str) -> bytes:
    return f"{code}\0{prefix}".encode("utf-8")


def _serialize(
    *,
    record_type: VaultRecordType,
    code: str,
    prefix: str,
    source_header: str,
    normalization_rule: str,
    parent_code: str | None,
    record_identifier: int | None,
) -> bytes:
    fields = [
        AAD_FORMAT_NAME,
        AAD_FORMAT_VERSION,
        record_type.value,
        code,
        prefix,
        source_header,
        normalization_rule,
        parent_code,
        record_identifier,
    ]
    return json.dumps(
        fields,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _rule_value(rule: NormalizationRule | str) -> str:
    return rule.value if isinstance(rule, NormalizationRule) else str(rule)
