"""API tipada no mesmo cofre; upsert registra uma tuple observada atomicamente."""
import hmac
import json
import re
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from uuid import uuid4

from data_mask_studio.normalization import NormalizationError, NormalizationRule, normalize_value
from data_mask_studio.vault.composite_models import (
    CompositeMapping, CompositeMappingCandidate, CompositeVariation,
)
from data_mask_studio.vault.composite_payload import composite_aad, decode_payload, encode_tuple
from data_mask_studio.vault.exceptions import VaultCollisionError, VaultError
from data_mask_studio.vault.repository import VaultRepository, VaultTransaction


class CompositeVaultRepository(VaultRepository):
    """Usa inicialização, chave AES e transações do repository escalar existente."""

    def as_read_only(self) -> "CompositeVaultRepository":
        return self if self._read_only else CompositeVaultRepository(
            self.database_path, self._cipher, read_only=True,
        )

    def get_composite_mapping(self, code: str) -> CompositeMapping | None:
        connection = self._connect_for_read()
        try:
            connection.execute("BEGIN")
            return self._read_composite(connection, code)
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            raise VaultError("Registro composto inválido.") from None
        finally:
            connection.close()

    def list_composite_variations(self, code: str) -> tuple[CompositeVariation, ...]:
        mapping = self.get_composite_mapping(code)
        return mapping.variations if mapping else ()

    def upsert_composite_mapping(
        self, candidate: CompositeMappingCandidate, *,
        transaction: VaultTransaction | None = None,
    ) -> None:
        """Conta a observação no mapping e na tuple; não aceita incrementos separados."""
        try:
            if transaction is not None and (
                self._read_only or transaction._cipher is not self._cipher
                or transaction._database_path != self.database_path.resolve()
                or not transaction._connection.in_transaction
            ):
                raise VaultError("Transação composta incompatível.")
            self._upsert_composite(candidate, transaction)
        except (ValueError, TypeError, KeyError, NormalizationError):
            raise VaultError("Configuração composta inválida.") from None

    def _upsert_composite(
        self, candidate: CompositeMappingCandidate,
        transaction: VaultTransaction | None = None,
    ) -> None:
        from data_mask_studio.processing.composite_identity import serialize_composite_identity
        if (candidate.identity_version != 1 or candidate.payload_version != 1
                or type(candidate.occurrences) is not int or candidate.occurrences <= 0):
            raise VaultError("Versão ou contagem composta inválida.")
        canonical = serialize_composite_identity(candidate.canonical_values)
        original = encode_tuple(candidate.original_values)
        rules = tuple(candidate.normalization_rules)
        _check_rules(rules, len(candidate.canonical_values))
        _check_tuple(tuple(candidate.original_values), rules, tuple(candidate.canonical_values))
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,23}", candidate.prefix) or not re.fullmatch(
            re.escape(candidate.prefix) + r"-[A-Z2-7]{12}", candidate.code,
        ):
            raise VaultError("Código composto inválido.")
        metadata = dict(code=candidate.code, prefix=candidate.prefix,
                        identity_version=1, payload_version=1, aad_version=1,
                        component_count=len(rules), rules=json.dumps([r.value for r in rules]))
        now = datetime.now(timezone.utc).isoformat()
        with (nullcontext(transaction) if transaction is not None else self.transaction()) as transaction:
            connection = transaction._connection
            if connection.execute("SELECT 1 FROM vault_mappings WHERE code = ?", (candidate.code,)).fetchone():
                raise VaultCollisionError("Conflito de tipo de código no cofre local.")
            existing = self._read_composite(connection, candidate.code)
            if existing:
                if (existing.prefix != candidate.prefix or not hmac.compare_digest(
                    serialize_composite_identity(existing.canonical_values), canonical,
                ) or existing.normalization_rules != rules):
                    raise VaultCollisionError("Conflito de identidade ou regras compostas.")
                connection.execute(
                    "UPDATE composite_mappings SET total_occurrences = total_occurrences + ?, last_seen = ? WHERE code = ?",
                    (candidate.occurrences, now, candidate.code),
                )
            else:
                encrypted = self._cipher.encrypt_payload(canonical, composite_aad(metadata))
                connection.execute(
                    "INSERT INTO composite_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (candidate.code, candidate.prefix, 1, 1, 1, len(rules), metadata["rules"],
                     encrypted.ciphertext, encrypted.nonce, now, now, candidate.occurrences),
                )
            matching = next((v for v in existing.variations if hmac.compare_digest(
                encode_tuple(v.original_values), original,
            )), None) if existing else None
            if matching:
                connection.execute(
                    "UPDATE composite_variations SET occurrence_count = occurrence_count + ?, last_seen = ? WHERE identifier = ?",
                    (candidate.occurrences, now, matching.identifier),
                )
            else:
                identifier = str(uuid4())
                encrypted = self._cipher.encrypt_payload(original, composite_aad(metadata, identifier))
                connection.execute("INSERT INTO composite_variations VALUES (?, ?, ?, ?, ?, ?, ?)",
                                   (identifier, candidate.code, encrypted.ciphertext, encrypted.nonce,
                                    now, now, candidate.occurrences))

    def _read_composite(self, connection, code: str) -> CompositeMapping | None:
        row = connection.execute("SELECT * FROM composite_mappings WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        variations = connection.execute("SELECT * FROM composite_variations WHERE code = ? ORDER BY rowid", (code,))
        return decode_composite_mapping(self._cipher, row, variations)


def decode_composite_mapping(cipher, row, variation_rows, *, verify_normalization=True) -> CompositeMapping:
    """Autentica payloads e estrutura; restoration usa canonical armazenada sem renormalizar."""
    code = row["code"]
    payload = cipher.decrypt_payload(row["encrypted_value"], row["nonce"], composite_aad(row))
    if (row["identity_version"], row["payload_version"], row["aad_version"]) != (1, 1, 1):
        raise VaultError("Versão composta não suportada.")
    canonical = decode_payload(payload)
    encoded_rules = json.loads(row["rules"])
    if not isinstance(encoded_rules, list) or not all(isinstance(value, str) for value in encoded_rules):
        raise VaultError("Formato de regras compostas inválido.")
    rules = tuple(NormalizationRule(value) for value in encoded_rules)
    _check_rules(rules, row["component_count"])
    if len(canonical) != row["component_count"]:
        raise VaultError("Quantidade de componentes inconsistente.")
    variations = []
    for item in variation_rows:
        original = decode_payload(cipher.decrypt_payload(
            item["encrypted_value"], item["nonce"], composite_aad(row, item["identifier"]),
        ), original=True)
        if len(original) != len(canonical):
            raise VaultError("Quantidade de componentes inconsistente.")
        if verify_normalization:
            _check_tuple(original, rules, canonical)
        if item["occurrence_count"] <= 0:
            raise VaultError("Contagem composta inconsistente.")
        variations.append(CompositeVariation(item["identifier"], code, original,
                                             item["first_seen"], item["last_seen"], item["occurrence_count"]))
    if not variations or sum(v.occurrence_count for v in variations) != row["total_occurrences"]:
        raise VaultError("Contagens compostas inconsistentes.")
    return CompositeMapping(code, row["prefix"], 1, 1, len(canonical), rules, canonical,
                            tuple(variations), row["first_seen"], row["last_seen"], row["total_occurrences"])


def _check_rules(rules, count):
    if len(rules) != count or not all(isinstance(rule, NormalizationRule) for rule in rules):
        raise VaultError("Regras compostas inconsistentes.")


def _check_tuple(original, rules, canonical):
    try:
        if len(original) != len(canonical) or tuple(
            # Execução composta canonicaliza brancos como ausência. Mantém também
            # a leitura de tuples antigas que preservavam whitespace literalmente.
            "" if (value == "" or value.isspace()) and expected == "" else normalize_value(value, rule)
            for value, rule, expected in zip(original, rules, canonical, strict=True)
        ) != canonical:
            raise VaultError("Tuple original incompatível com a identidade composta.")
    except (ValueError, TypeError, NormalizationError):
        raise VaultError("Tuple composta inválida.") from None
