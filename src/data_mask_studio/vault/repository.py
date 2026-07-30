import hmac
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from data_mask_studio.vault.database import connect, initialize_schema
from data_mask_studio.vault.encryption import VaultCipher
from data_mask_studio.vault.exceptions import VaultCollisionError, VaultError
from data_mask_studio.vault.models import (
    DecryptedVaultMapping,
    MappingCandidate,
    VaultRecord,
    VaultUpdateSummary,
)


class VaultRepository:
    """Persiste mapeamentos criptografados em uma transação por processamento."""

    def __init__(self, database_path: str | Path, cipher: VaultCipher) -> None:
        self.database_path = Path(database_path)
        self._cipher = cipher
        initialize_schema(self.database_path)

    @contextmanager
    def transaction(self) -> Iterator["VaultTransaction"]:
        connection = connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            transaction = VaultTransaction(connection, self._cipher)
            yield transaction
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise VaultError("Não foi possível atualizar o cofre local.") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_record(self, code: str) -> VaultRecord | None:
        connection = connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT code, prefix, encrypted_value, nonce, source_header, "
                "first_seen, last_seen, occurrence_count "
                "FROM vault_mappings WHERE code = ?",
                (code,),
            ).fetchone()
            return _record_from_row(row) if row is not None else None
        except sqlite3.Error as error:
            raise VaultError("Não foi possível consultar o cofre local.") from error
        finally:
            connection.close()

    def count(self) -> int:
        connection = connect(self.database_path)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM vault_mappings").fetchone()[0])
        except sqlite3.Error as error:
            raise VaultError("Não foi possível consultar o cofre local.") from error
        finally:
            connection.close()

    def get_decrypted_mapping(self, code: str) -> DecryptedVaultMapping | None:
        """Recupera um único código exato com autenticação AES-GCM."""
        record = self.get_record(code)
        if record is None:
            return None
        if (
            record.code != code
            or not record.prefix
            or not record.source_header
            or not record.first_seen
            or not record.last_seen
            or record.occurrence_count <= 0
        ):
            raise VaultError("Foi encontrado um registro inconsistente no cofre local.")
        original_value = self._cipher.decrypt(
            record.code,
            record.prefix,
            record.encrypted_value,
            record.nonce,
        )
        return DecryptedVaultMapping(
            code=record.code,
            prefix=record.prefix,
            source_header=record.source_header,
            original_value=original_value,
            first_seen=record.first_seen,
            last_seen=record.last_seen,
            occurrence_count=record.occurrence_count,
        )


class VaultTransaction:
    def __init__(self, connection: sqlite3.Connection, cipher: VaultCipher) -> None:
        self._connection = connection
        self._cipher = cipher
        self._connection.execute(
            "CREATE TEMP TABLE processing_changes ("
            "code TEXT PRIMARY KEY, change_type TEXT NOT NULL)"
        )

    def upsert_batch(self, candidates: Sequence[MappingCandidate]) -> None:
        for candidate in candidates:
            self._upsert(candidate)

    def summary(self) -> VaultUpdateSummary:
        counts = {
            row["change_type"]: int(row["total"])
            for row in self._connection.execute(
                "SELECT change_type, COUNT(*) AS total "
                "FROM processing_changes GROUP BY change_type"
            )
        }
        return VaultUpdateSummary(
            new_mappings=counts.get("new", 0),
            updated_mappings=counts.get("updated", 0),
        )

    def _upsert(self, candidate: MappingCandidate) -> None:
        row = self._connection.execute(
            "SELECT code, prefix, encrypted_value, nonce, source_header, "
            "first_seen, last_seen, occurrence_count "
            "FROM vault_mappings WHERE code = ?",
            (candidate.code,),
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat()

        if row is None:
            encrypted = self._cipher.encrypt(
                candidate.code,
                candidate.prefix,
                candidate.original_value,
            )
            self._connection.execute(
                "INSERT INTO vault_mappings "
                "(code, prefix, encrypted_value, nonce, source_header, first_seen, "
                "last_seen, occurrence_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.code,
                    candidate.prefix,
                    encrypted.ciphertext,
                    encrypted.nonce,
                    candidate.source_header,
                    now,
                    now,
                    candidate.occurrences,
                ),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO processing_changes VALUES (?, 'new')",
                (candidate.code,),
            )
            return

        record = _record_from_row(row)
        existing_value = self._cipher.decrypt(
            record.code,
            record.prefix,
            record.encrypted_value,
            record.nonce,
        )
        values_match = hmac.compare_digest(
            existing_value.encode("utf-8"),
            candidate.original_value.encode("utf-8"),
        )
        if record.prefix != candidate.prefix or not values_match:
            raise VaultCollisionError(
                "Foi detectado um conflito de código no cofre local."
            )

        self._connection.execute(
            "UPDATE vault_mappings SET last_seen = ?, "
            "occurrence_count = occurrence_count + ? WHERE code = ?",
            (now, candidate.occurrences, candidate.code),
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO processing_changes VALUES (?, 'updated')",
            (candidate.code,),
        )


def _record_from_row(row: sqlite3.Row) -> VaultRecord:
    return VaultRecord(
        code=str(row["code"]),
        prefix=str(row["prefix"]),
        encrypted_value=bytes(row["encrypted_value"]),
        nonce=bytes(row["nonce"]),
        source_header=str(row["source_header"]),
        first_seen=str(row["first_seen"]),
        last_seen=str(row["last_seen"]),
        occurrence_count=int(row["occurrence_count"]),
    )
