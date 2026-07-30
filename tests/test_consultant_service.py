import sqlite3
from pathlib import Path

from data_mask_studio.consultant import ConsultantService, ConsultationStatus
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

KEY = b"C" * 32
EXISTING_CODE = "CPF_ID-ABCDEFGHI234"
ORIGINAL_VALUE = "sensitive-test-value"


def make_repository(tmp_path: Path) -> VaultRepository:
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(KEY))
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    EXISTING_CODE,
                    "CPF_ID",
                    ORIGINAL_VALUE,
                    "CPF/ID",
                    occurrences=3,
                )
            ]
        )
    return repository


def test_existing_code_returns_decrypted_mapping(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    service = ConsultantService(lambda: repository)

    result = service.consult(EXISTING_CODE)[0]

    assert result.status is ConsultationStatus.FOUND
    assert result.mapping is not None
    assert result.mapping.original_value == ORIGINAL_VALUE
    assert result.mapping.prefix == "CPF_ID"
    assert result.mapping.source_header == "CPF/ID"
    assert result.mapping.occurrence_count == 3


def test_missing_code_is_not_a_technical_error(tmp_path: Path) -> None:
    service = ConsultantService(lambda: make_repository(tmp_path))

    result = service.consult("CPF_ID-BCDEFGHI234A")[0]

    assert result.status is ConsultationStatus.NOT_FOUND
    assert result.message == "Código não encontrado no cofre."
    assert service.last_error is None


def test_multiple_codes_continue_after_invalid_input(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    service = ConsultantService(lambda: repository)

    results = service.consult(
        f"{EXISTING_CODE}\nINVALID-CODE\nCPF_ID-BCDEFGHI234A"
    )

    assert [result.status for result in results] == [
        ConsultationStatus.FOUND,
        ConsultationStatus.INVALID,
        ConsultationStatus.NOT_FOUND,
    ]
    assert results[1].message == "Formato de código inválido."


def test_tampered_mapping_fails_safely_without_sensitive_details(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    with sqlite3.connect(repository.database_path) as connection:
        encrypted = connection.execute(
            "SELECT encrypted_value FROM vault_variations WHERE code = ?",
            (EXISTING_CODE,),
        ).fetchone()[0]
        tampered = bytes([encrypted[0] ^ 1]) + encrypted[1:]
        connection.execute(
            "UPDATE vault_variations SET encrypted_value = ? WHERE code = ?",
            (tampered, EXISTING_CODE),
        )
    service = ConsultantService(lambda: repository)

    result = service.consult(EXISTING_CODE)[0]

    assert result.status is ConsultationStatus.RECOVERY_FAILED
    assert result.mapping is None
    assert result.message == "Não foi possível recuperar este mapeamento com segurança."
    forbidden_fragments = [
        ORIGINAL_VALUE,
        KEY.hex(),
        tampered.hex(),
        "nonce",
        "encrypted_value",
    ]
    visible_text = f"{result.message} {result!r}"
    assert all(fragment not in visible_text for fragment in forbidden_fragments)
