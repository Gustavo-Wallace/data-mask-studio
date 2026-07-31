import hmac
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from data_mask_studio.normalization import (
    NormalizationError,
    NormalizationRule,
    normalize_value,
)
from data_mask_studio.vault.database import (
    SCHEMA_VERSION,
    connect,
    connect_read_only,
    initialize_schema,
)
from data_mask_studio.vault.encryption import VaultCipher
from data_mask_studio.vault.exceptions import VaultCollisionError, VaultError
from data_mask_studio.vault.models import (
    DecryptedVariation,
    DecryptedVaultMapping,
    MappingCandidate,
    VariationCandidate,
    VaultRecord,
    VaultUpdateSummary,
    VaultVariationRecord,
)


class VaultRepository:
    """Persiste valores canônicos e suas variações originais criptografadas."""

    def __init__(
        self,
        database_path: str | Path,
        cipher: VaultCipher,
        *,
        read_only: bool = False,
    ) -> None:
        self.database_path = Path(database_path)
        self._cipher = cipher
        self._read_only = read_only
        if read_only:
            self._validate_read_only_schema()
        else:
            initialize_schema(self.database_path, cipher)

    def as_read_only(self) -> "VaultRepository":
        """Cria uma visao que o SQLite impede de alterar."""
        if self._read_only:
            return self
        return VaultRepository(self.database_path, self._cipher, read_only=True)

    def _validate_read_only_schema(self) -> None:
        connection = connect_read_only(self.database_path)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != SCHEMA_VERSION:
                raise VaultError("A versao do cofre local nao e compativel.")
        except sqlite3.Error as error:
            raise VaultError("Nao foi possivel consultar o cofre local.") from error
        finally:
            connection.close()

    def _connect_for_read(self) -> sqlite3.Connection:
        return (
            connect_read_only(self.database_path)
            if self._read_only
            else connect(self.database_path)
        )

    @contextmanager
    def transaction(self) -> Iterator["VaultTransaction"]:
        if self._read_only:
            raise VaultError("O cofre esta aberto somente para leitura.")
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
        connection = self._connect_for_read()
        try:
            row = connection.execute(
                "SELECT code, prefix, "
                "canonical_encrypted_value AS encrypted_value, "
                "canonical_nonce AS nonce, source_header, normalization_rule, "
                "first_seen, last_seen, total_occurrences AS occurrence_count "
                "FROM vault_mappings WHERE code = ?",
                (code,),
            ).fetchone()
            return _record_from_row(row) if row is not None else None
        except (sqlite3.Error, ValueError) as error:
            raise VaultError("Não foi possível consultar o cofre local.") from error
        finally:
            connection.close()

    def count(self) -> int:
        connection = self._connect_for_read()
        try:
            return int(
                connection.execute("SELECT COUNT(*) FROM vault_mappings").fetchone()[0]
            )
        except sqlite3.Error as error:
            raise VaultError("Não foi possível consultar o cofre local.") from error
        finally:
            connection.close()

    def get_decrypted_mapping(self, code: str) -> DecryptedVaultMapping | None:
        """Recupera um único código e todas as suas variações autenticadas."""
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

        variations = self._get_variations(code)
        canonical_value = self._cipher.decrypt(
            record.code,
            record.prefix,
            record.encrypted_value,
            record.nonce,
        )
        decrypted_variations = tuple(
            DecryptedVariation(
                original_value=self._cipher.decrypt(
                    record.code,
                    record.prefix,
                    variation.encrypted_value,
                    variation.nonce,
                ),
                first_seen=variation.first_seen,
                last_seen=variation.last_seen,
                occurrence_count=variation.occurrence_count,
                normalization_rule=variation.normalization_rule,
            )
            for variation in variations
        )
        if (
            not decrypted_variations
            or sum(item.occurrence_count for item in decrypted_variations)
            != record.occurrence_count
        ):
            raise VaultError("Foi encontrado um registro inconsistente no cofre local.")
        try:
            variations_match = all(
                hmac.compare_digest(
                    normalize_value(
                        item.original_value, item.normalization_rule
                    ).encode("utf-8"),
                    canonical_value.encode("utf-8"),
                )
                for item in decrypted_variations
            )
        except NormalizationError as error:
            raise VaultError(
                "Foi encontrado um registro inconsistente no cofre local."
            ) from error
        if not variations_match:
            raise VaultError("Foi encontrado um registro inconsistente no cofre local.")

        return DecryptedVaultMapping(
            code=record.code,
            prefix=record.prefix,
            source_header=record.source_header,
            original_value=decrypted_variations[0].original_value,
            first_seen=record.first_seen,
            last_seen=record.last_seen,
            occurrence_count=record.occurrence_count,
            normalization_rule=record.normalization_rule,
            variations=decrypted_variations,
            canonical_value=canonical_value,
        )

    def _get_variations(self, code: str) -> list[VaultVariationRecord]:
        connection = self._connect_for_read()
        try:
            rows = connection.execute(
                "SELECT identifier, code, encrypted_value, nonce, first_seen, "
                "last_seen, occurrence_count, normalization_rule FROM vault_variations "
                "WHERE code = ? ORDER BY identifier",
                (code,),
            ).fetchall()
            return [_variation_from_row(row) for row in rows]
        except sqlite3.Error as error:
            raise VaultError("Não foi possível consultar o cofre local.") from error
        finally:
            connection.close()


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
            "SELECT code, prefix, "
            "canonical_encrypted_value AS encrypted_value, "
            "canonical_nonce AS nonce, source_header, normalization_rule, "
            "first_seen, last_seen, total_occurrences AS occurrence_count "
            "FROM vault_mappings WHERE code = ?",
            (candidate.code,),
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat()

        if row is None:
            self._insert_mapping(candidate, now)
            self._connection.execute(
                "INSERT OR IGNORE INTO processing_changes VALUES (?, 'new')",
                (candidate.code,),
            )
            return

        record = _record_from_row(row)
        existing_canonical = self._cipher.decrypt(
            record.code,
            record.prefix,
            record.encrypted_value,
            record.nonce,
        )
        canonical_value = candidate.canonical_value or ""
        canonical_matches = hmac.compare_digest(
            existing_canonical.encode("utf-8"),
            canonical_value.encode("utf-8"),
        )
        if record.prefix != candidate.prefix or not canonical_matches:
            raise VaultCollisionError(
                "Foi detectado um conflito de código no cofre local."
            )

        self._connection.execute(
            "UPDATE vault_mappings SET last_seen = ?, "
            "total_occurrences = total_occurrences + ? WHERE code = ?",
            (now, candidate.total_occurrences, candidate.code),
        )
        self._upsert_variations(candidate, now)
        self._connection.execute(
            "INSERT OR IGNORE INTO processing_changes VALUES (?, 'updated')",
            (candidate.code,),
        )

    def _insert_mapping(self, candidate: MappingCandidate, now: str) -> None:
        canonical_value = candidate.canonical_value or ""
        encrypted = self._cipher.encrypt(
            candidate.code,
            candidate.prefix,
            canonical_value,
        )
        self._connection.execute(
            "INSERT INTO vault_mappings "
            "(code, prefix, canonical_encrypted_value, canonical_nonce, "
            "source_header, normalization_rule, first_seen, last_seen, "
            "total_occurrences) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate.code,
                candidate.prefix,
                encrypted.ciphertext,
                encrypted.nonce,
                candidate.source_header,
                candidate.normalization_rule.value,
                now,
                now,
                candidate.total_occurrences,
            ),
        )
        self._insert_variations(candidate, now)

    def _insert_variations(self, candidate: MappingCandidate, now: str) -> None:
        for original_value, variation in candidate.variations.items():
            encrypted = self._cipher.encrypt(
                candidate.code, candidate.prefix, original_value
            )
            self._connection.execute(
                "INSERT INTO vault_variations "
                "(code, encrypted_value, nonce, first_seen, last_seen, "
                "normalization_rule, occurrence_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.code,
                    encrypted.ciphertext,
                    encrypted.nonce,
                    now,
                    now,
                    variation.normalization_rule.value,
                    variation.occurrences,
                ),
            )

    def _upsert_variations(self, candidate: MappingCandidate, now: str) -> None:
        rows = self._connection.execute(
            "SELECT identifier, code, encrypted_value, nonce, first_seen, "
            "last_seen, occurrence_count, normalization_rule FROM vault_variations "
            "WHERE code = ? ORDER BY identifier",
            (candidate.code,),
        ).fetchall()
        existing = [_variation_from_row(row) for row in rows]
        decrypted = [
            (
                variation,
                self._cipher.decrypt(
                    candidate.code,
                    candidate.prefix,
                    variation.encrypted_value,
                    variation.nonce,
                ),
            )
            for variation in existing
        ]

        for original_value, candidate_variation in candidate.variations.items():
            matching = next(
                (
                    variation
                    for variation, stored_value in decrypted
                    if hmac.compare_digest(
                        stored_value.encode("utf-8"), original_value.encode("utf-8")
                    )
                ),
                None,
            )
            if matching is None:
                self._insert_single_variation(
                    candidate, original_value, candidate_variation, now
                )
            else:
                self._connection.execute(
                    "UPDATE vault_variations SET last_seen = ?, "
                    "occurrence_count = occurrence_count + ? WHERE identifier = ?",
                    (now, candidate_variation.occurrences, matching.identifier),
                )

    def _insert_single_variation(
        self,
        candidate: MappingCandidate,
        original_value: str,
        variation: VariationCandidate,
        now: str,
    ) -> None:
        encrypted = self._cipher.encrypt(
            candidate.code, candidate.prefix, original_value
        )
        self._connection.execute(
            "INSERT INTO vault_variations "
            "(code, encrypted_value, nonce, first_seen, last_seen, "
            "normalization_rule, occurrence_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                candidate.code,
                encrypted.ciphertext,
                encrypted.nonce,
                now,
                now,
                variation.normalization_rule.value,
                variation.occurrences,
            ),
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
        normalization_rule=NormalizationRule(str(row["normalization_rule"])),
    )


def _variation_from_row(row: sqlite3.Row) -> VaultVariationRecord:
    return VaultVariationRecord(
        identifier=int(row["identifier"]),
        code=str(row["code"]),
        encrypted_value=bytes(row["encrypted_value"]),
        nonce=bytes(row["nonce"]),
        first_seen=str(row["first_seen"]),
        last_seen=str(row["last_seen"]),
        occurrence_count=int(row["occurrence_count"]),
        normalization_rule=NormalizationRule(str(row["normalization_rule"])),
    )
