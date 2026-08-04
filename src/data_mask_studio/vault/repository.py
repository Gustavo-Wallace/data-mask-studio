import hmac
import sqlite3
import time
from copy import deepcopy
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from data_mask_studio.normalization import (
    NormalizationError,
    NormalizationRule,
    normalize_value,
)
from data_mask_studio.performance import BALANCED_SETTINGS, RestorationMetrics
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
    def read_session(
        self, metrics: RestorationMetrics | None = None
    ) -> Iterator["VaultReadSession"]:
        connection = self._connect_for_read()
        if metrics is not None:
            metrics.connections_opened += 1
        try:
            yield VaultReadSession(connection, self._cipher, metrics)
        finally:
            connection.close()

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
        connection = self._connect_for_read()
        try:
            row = connection.execute(
                "SELECT code, prefix, canonical_encrypted_value AS encrypted_value, "
                "canonical_nonce AS nonce, source_header, normalization_rule, "
                "first_seen, last_seen, total_occurrences AS occurrence_count "
                "FROM vault_mappings WHERE code = ?",
                (code,),
            ).fetchone()
            if row is None:
                return None
            record = _record_from_row(row)
            variation_rows = connection.execute(
                "SELECT identifier, code, encrypted_value, nonce, first_seen, "
                "last_seen, occurrence_count, normalization_rule FROM vault_variations "
                "WHERE code = ? ORDER BY identifier",
                (code,),
            ).fetchall()
            variations = [_variation_from_row(item) for item in variation_rows]
        except (sqlite3.Error, ValueError) as error:
            raise VaultError("Não foi possível consultar o cofre local.") from error
        finally:
            connection.close()
        if record.code != code:
            raise VaultError("Foi encontrado um registro inconsistente no cofre local.")
        return _decrypt_mapping(record, variations, self._cipher)

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
        merged = _merge_duplicate_candidates(candidates)
        existing = self._existing_mappings(candidate.code for candidate in merged)
        for candidate in merged:
            self._upsert(candidate, existing.get(candidate.code))

    def _existing_mappings(self, codes: Iterator[str]) -> dict[str, sqlite3.Row]:
        unique = tuple(dict.fromkeys(codes))
        found: dict[str, sqlite3.Row] = {}
        for offset in range(0, len(unique), 400):
            chunk = unique[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._connection.execute(
                "SELECT code, prefix, canonical_encrypted_value AS encrypted_value, "
                "canonical_nonce AS nonce, source_header, normalization_rule, "
                "first_seen, last_seen, total_occurrences AS occurrence_count "
                f"FROM vault_mappings WHERE code IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update((str(row["code"]), row) for row in rows)
        return found

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

    def _upsert(self, candidate: MappingCandidate, row: sqlite3.Row | None) -> None:
        now = datetime.now(timezone.utc).isoformat()

        if row is None:
            self._insert_mapping(candidate, now)
            self._connection.execute(
                "INSERT OR IGNORE INTO processing_changes VALUES (?, 'new')",
                (candidate.code,),
            )
            return

        record = _record_from_row(row)
        existing_canonical = self._cipher.decrypt_mapping(
            record.code,
            record.prefix,
            record.source_header,
            record.normalization_rule,
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
        self._upsert_variations(candidate, now, record)
        self._connection.execute(
            "INSERT OR IGNORE INTO processing_changes VALUES (?, 'updated')",
            (candidate.code,),
        )

    def _insert_mapping(self, candidate: MappingCandidate, now: str) -> None:
        canonical_value = candidate.canonical_value or ""
        encrypted = self._cipher.encrypt_mapping(
            candidate.code,
            candidate.prefix,
            candidate.source_header,
            candidate.normalization_rule,
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
            cursor = self._connection.execute(
                "INSERT INTO vault_variations "
                "(code, encrypted_value, nonce, first_seen, last_seen, "
                "normalization_rule, occurrence_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.code,
                    b"",
                    b"",
                    now,
                    now,
                    variation.normalization_rule.value,
                    variation.occurrences,
                ),
            )
            identifier = int(cursor.lastrowid)
            encrypted = self._cipher.encrypt_variation(
                identifier,
                candidate.code,
                candidate.prefix,
                candidate.source_header,
                variation.normalization_rule,
                original_value,
            )
            self._connection.execute(
                "UPDATE vault_variations SET encrypted_value = ?, nonce = ? "
                "WHERE identifier = ?",
                (encrypted.ciphertext, encrypted.nonce, identifier),
            )

    def _upsert_variations(
        self,
        candidate: MappingCandidate,
        now: str,
        mapping: VaultRecord,
    ) -> None:
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
                self._cipher.decrypt_variation(
                    variation.identifier,
                    candidate.code,
                    candidate.prefix,
                    mapping.source_header,
                    variation.normalization_rule,
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
                    candidate,
                    original_value,
                    candidate_variation,
                    now,
                    mapping.source_header,
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
        source_header: str,
    ) -> None:
        cursor = self._connection.execute(
            "INSERT INTO vault_variations "
            "(code, encrypted_value, nonce, first_seen, last_seen, "
            "normalization_rule, occurrence_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                candidate.code,
                b"",
                b"",
                now,
                now,
                variation.normalization_rule.value,
                variation.occurrences,
            ),
        )
        identifier = int(cursor.lastrowid)
        encrypted = self._cipher.encrypt_variation(
            identifier,
            candidate.code,
            candidate.prefix,
            source_header,
            variation.normalization_rule,
            original_value,
        )
        self._connection.execute(
            "UPDATE vault_variations SET encrypted_value = ?, nonce = ? "
            "WHERE identifier = ?",
            (encrypted.ciphertext, encrypted.nonce, identifier),
        )


class VaultReadSession:
    def __init__(
        self,
        connection: sqlite3.Connection,
        cipher: VaultCipher,
        metrics: RestorationMetrics | None = None,
    ) -> None:
        self._connection = connection
        self._cipher = cipher
        self._metrics = metrics

    def get_many(self, codes: Sequence[str]) -> dict[str, DecryptedVaultMapping]:
        unique = tuple(dict.fromkeys(codes))
        if not unique:
            return {}
        mapping_rows: dict[str, sqlite3.Row] = {}
        variation_rows: dict[str, list[sqlite3.Row]] = {}
        started = time.perf_counter()
        for chunk in _chunks(unique, BALANCED_SETTINGS.sqlite_lookup_batch_size):
            placeholders = ",".join("?" for _ in chunk)
            rows = self._connection.execute(
                "SELECT code, prefix, canonical_encrypted_value AS encrypted_value, "
                "canonical_nonce AS nonce, source_header, normalization_rule, "
                "first_seen, last_seen, total_occurrences AS occurrence_count "
                f"FROM vault_mappings WHERE code IN ({placeholders})",
                chunk,
            ).fetchall()
            mapping_rows.update((str(row["code"]), row) for row in rows)
            self._record_query(len(rows))
        found_codes = tuple(mapping_rows)
        for chunk in _chunks(found_codes, BALANCED_SETTINGS.sqlite_lookup_batch_size):
            placeholders = ",".join("?" for _ in chunk)
            rows = self._connection.execute(
                "SELECT identifier, code, encrypted_value, nonce, first_seen, "
                "last_seen, occurrence_count, normalization_rule "
                f"FROM vault_variations WHERE code IN ({placeholders}) "
                "ORDER BY code, identifier",
                chunk,
            ).fetchall()
            for row in rows:
                variation_rows.setdefault(str(row["code"]), []).append(row)
            self._record_query(len({str(row["code"]) for row in rows}))
        if self._metrics is not None:
            self._metrics.query_seconds += time.perf_counter() - started
            self._metrics.codes_returned += len(mapping_rows)

        started = time.perf_counter()
        result: dict[str, DecryptedVaultMapping] = {}
        for code, row in mapping_rows.items():
            record = _record_from_row(row)
            variations = [
                _variation_from_row(item) for item in variation_rows.get(code, [])
            ]
            result[code] = _decrypt_mapping(record, variations, self._cipher)
            if self._metrics is not None:
                self._metrics.decryptions += 1 + len(variations)
        if self._metrics is not None:
            self._metrics.decryption_seconds += time.perf_counter() - started
        return result

    def _record_query(self, returned_codes: int) -> None:
        if self._metrics is not None:
            self._metrics.sqlite_queries += 1
            self._metrics.codes_returned_per_query.append(returned_codes)


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


def _merge_duplicate_candidates(
    candidates: Sequence[MappingCandidate],
) -> Sequence[MappingCandidate]:
    if len({candidate.code for candidate in candidates}) == len(candidates):
        return candidates
    merged: dict[str, MappingCandidate] = {}
    for candidate in candidates:
        current = merged.get(candidate.code)
        if current is None:
            merged[candidate.code] = deepcopy(candidate)
            continue
        canonical_matches = hmac.compare_digest(
            (current.canonical_value or "").encode("utf-8"),
            (candidate.canonical_value or "").encode("utf-8"),
        )
        if current.prefix != candidate.prefix or not canonical_matches:
            raise VaultCollisionError(
                "Foi detectado um conflito de código no cofre local."
            )
        for original, variation in candidate.variations.items():
            current.add_variation(
                original,
                variation.normalization_rule,
                variation.occurrences,
            )
    return tuple(merged.values())


def _chunks(values: Sequence[str], size: int) -> Iterator[tuple[str, ...]]:
    for offset in range(0, len(values), size):
        yield tuple(values[offset : offset + size])


def _decrypt_mapping(
    record: VaultRecord,
    variations: Sequence[VaultVariationRecord],
    cipher: VaultCipher,
) -> DecryptedVaultMapping:
    if (
        not record.prefix
        or not record.source_header
        or not record.first_seen
        or not record.last_seen
        or record.occurrence_count <= 0
    ):
        raise VaultError("Foi encontrado um registro inconsistente no cofre local.")
    canonical_value = cipher.decrypt_mapping(
        record.code,
        record.prefix,
        record.source_header,
        record.normalization_rule,
        record.encrypted_value,
        record.nonce,
    )
    decrypted = tuple(
        DecryptedVariation(
            original_value=cipher.decrypt_variation(
                variation.identifier,
                record.code,
                record.prefix,
                record.source_header,
                variation.normalization_rule,
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
    if not decrypted or sum(item.occurrence_count for item in decrypted) != record.occurrence_count:
        raise VaultError("Foi encontrado um registro inconsistente no cofre local.")
    try:
        valid = all(
            hmac.compare_digest(
                normalize_value(item.original_value, item.normalization_rule).encode("utf-8"),
                canonical_value.encode("utf-8"),
            )
            for item in decrypted
        )
    except NormalizationError as error:
        raise VaultError("Foi encontrado um registro inconsistente no cofre local.") from error
    if not valid:
        raise VaultError("Foi encontrado um registro inconsistente no cofre local.")
    return DecryptedVaultMapping(
        code=record.code,
        prefix=record.prefix,
        source_header=record.source_header,
        original_value=decrypted[0].original_value,
        first_seen=record.first_seen,
        last_seen=record.last_seen,
        occurrence_count=record.occurrence_count,
        normalization_rule=record.normalization_rule,
        variations=decrypted,
        canonical_value=canonical_value,
    )
