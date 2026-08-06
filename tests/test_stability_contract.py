from pathlib import Path

from data_mask_studio.anonymization import generate_token
from data_mask_studio.vault.database import SCHEMA_VERSION


def test_1_0_stability_contract_preserves_known_token_and_schema() -> None:
    key = b"fixed-test-key-with-at-least-32b"

    assert generate_token(key, "CPF_ID", "123.456.789-00") == (
        "CPF_ID-IQPEWAE2ES36"
    )
    assert SCHEMA_VERSION == 3


def test_compatibility_document_records_recovery_guarantees() -> None:
    content = Path("COMPATIBILITY.md").read_text(encoding="utf-8")

    for required in (
        "HMAC-SHA256",
        "Base32",
        "AES-256-GCM",
        "Windows DPAPI",
        "schema 3",
        "fallback exato",
        "rollback integral",
        "API de backup do SQLite",
    ):
        assert required in content
