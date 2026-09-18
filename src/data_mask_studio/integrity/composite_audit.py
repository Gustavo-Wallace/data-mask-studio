"""Atestação composta read-only sobre o snapshot já criado pelo auditor."""
import hashlib
import hmac
import json
import re
from collections.abc import Callable
import sqlite3

from data_mask_studio.anonymization.prefix_rules import validate_prefix
from data_mask_studio.integrity.exceptions import IntegrityCancelled
from data_mask_studio.integrity.models import CheckResult, IntegrityStatus
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.vault import VaultCipher
from data_mask_studio.vault.composite_payload import composite_aad, decode_payload
from data_mask_studio.vault.exceptions import VaultError


class _Check:
    def __init__(self, name: str):
        self.name = name
        self.examined = self.failures = 0
        self.references: list[str] = []

    def record(self, reference: str, valid: bool) -> None:
        self.examined += 1
        if not valid:
            self.failures += 1
            if len(self.references) < 10:
                self.references.append(reference)

    def result(self) -> CheckResult:
        return CheckResult(self.name, IntegrityStatus.FAILURE if self.failures else IntegrityStatus.INTACT,
                           self.examined, self.failures,
                           "Foram encontradas inconsistências ou registros não verificáveis." if self.failures
                           else "Nenhuma inconsistência encontrada.", tuple(self.references))


def audit_composites(connection: sqlite3.Connection, hmac_key: bytes | None,
                     vault_key: bytes | None, cancelled: Callable[[], bool]) -> list[CheckResult]:
    mappings = _Check("Mappings compostos: autenticação, metadata e payload")
    variations = _Check("Variações compostas: autenticação e payload")
    tokens = _Check("Recomputação dos tokens compostos")
    counters = _Check("Contadores compostos")
    parents = _Check("Referências compostas")
    uniqueness = _Check("Unicidade de códigos e variações compostas")
    cipher = VaultCipher(vault_key) if vault_key is not None else None

    def check_cancel():
        if cancelled():
            raise IntegrityCancelled("A verificação de integridade foi cancelada.")

    # Dois cursores ordenados: nenhuma consulta por variation nem retenção global de plaintext.
    mapping_rows = connection.execute("SELECT * FROM composite_mappings ORDER BY code")
    variation_rows = iter(connection.execute("SELECT * FROM composite_variations ORDER BY code, identifier"))
    current = next(variation_rows, None)
    mapping_index = variation_index = 0

    def orphan():
        nonlocal variation_index
        variation_index += 1
        reference = f"composite-variation#{variation_index}"
        variations.record(reference, False)
        parents.record(reference, False)
        counters.record(reference, _positive(current["occurrence_count"]))

    for row in mapping_rows:
        check_cancel()
        mapping_index += 1
        reference = f"composite-mapping#{mapping_index}"
        code = row["code"]
        valid = token_valid = False
        try:
            if cipher is None:
                raise ValueError
            if (row["identity_version"], row["payload_version"], row["aad_version"]) != (1, 1, 1):
                raise ValueError
            if not isinstance(code, str) or not isinstance(row["prefix"], str) or validate_prefix(row["prefix"]):
                raise ValueError
            if not re.fullmatch(re.escape(row["prefix"]) + r"-[A-Z2-7]{12}", code):
                raise ValueError
            count = row["component_count"]
            rules = json.loads(row["rules"])
            if type(count) is not int or count < 2 or not isinstance(rules, list) or len(rules) != count:
                raise ValueError
            for rule in rules:
                NormalizationRule(rule)
            canonical = decode_payload(cipher.decrypt_payload(row["encrypted_value"], row["nonce"], composite_aad(row)))
            if len(canonical) != count:
                raise ValueError
            valid = True
            if hmac_key is not None:
                expected = generate_composite_token(hmac_key, row["prefix"], canonical, version=row["identity_version"])
                token_valid = hmac.compare_digest(expected, code)
            del canonical
        except (VaultError, ValueError, TypeError, KeyError, OverflowError):
            pass
        mappings.record(reference, valid)
        tokens.record(reference, token_valid)

        while current is not None and current["code"] < code:
            check_cancel()
            orphan()
            current = next(variation_rows, None)
        total = observed = 0
        counts_valid = True
        # Digests de payloads autenticados, não tuples em claro; descartados por mapping.
        originals: set[bytes] = set()
        while current is not None and current["code"] == code:
            check_cancel()
            variation_index += 1
            item_reference = f"composite-variation#{variation_index}"
            observed += 1
            count_valid = _positive(current["occurrence_count"])
            counts_valid &= count_valid
            if count_valid:
                total += current["occurrence_count"]
            counters.record(item_reference, count_valid)
            parents.record(item_reference, valid and token_valid)
            variation_valid = unique = False
            try:
                if not valid or cipher is None or not isinstance(current["identifier"], str) or not current["identifier"]:
                    raise ValueError
                payload = cipher.decrypt_payload(current["encrypted_value"], current["nonce"],
                                                 composite_aad(row, current["identifier"]))
                original = decode_payload(payload, original=True)
                if len(original) != row["component_count"]:
                    raise ValueError
                fingerprint = hashlib.sha256(payload).digest()
                unique = fingerprint not in originals
                originals.add(fingerprint)
                variation_valid = True
                del payload, original
            except (VaultError, ValueError, TypeError, KeyError, OverflowError):
                pass
            variations.record(item_reference, variation_valid)
            uniqueness.record(item_reference, unique)
            current = next(variation_rows, None)
        parents.record(reference, observed > 0)
        counters.record(reference, _positive(row["total_occurrences"]) and counts_valid
                        and total == row["total_occurrences"])

    while current is not None:
        check_cancel()
        orphan()
        current = next(variation_rows, None)
    check_cancel()
    if mapping_index == 0 and variation_index == 0:
        return []  # Preserva integralmente o relatório de cofres apenas escalares.
    conflicts = connection.execute(
        "SELECT COUNT(*) FROM composite_mappings c JOIN vault_mappings s ON c.code = s.code"
    ).fetchone()[0]
    uniqueness.record("scalar-composite-code-conflict", conflicts == 0)
    for table, column in (("composite_mappings", "code"), ("composite_variations", "identifier")):
        duplicates = connection.execute(
            f"SELECT COUNT(*) FROM (SELECT {column} FROM {table} GROUP BY {column} HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        uniqueness.record(f"{table}-duplicate-identity", duplicates == 0)
    return [check.result() for check in (mappings, variations, tokens, counters, parents, uniqueness)]


def _positive(value) -> bool:
    return type(value) is int and value > 0
