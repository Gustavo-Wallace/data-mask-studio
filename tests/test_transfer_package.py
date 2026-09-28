import io
import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.transfer_package import (
    MaskedFileBinding, TransferPayload, PackageError, encrypt_package,
    decrypt_package, write_package, read_package, validate_payload,
    ScalarRestorationMapping, CompositeRestorationMapping,
)
from data_mask_studio.transfer_package import service
from data_mask_studio.transfer_package.serialization import encode_payload, decode_payload, MAX_PAYLOAD_SIZE, HARD_PAYLOAD_LIMIT
from data_mask_studio.vault.models import DecryptedVaultMapping, DecryptedVariation
from data_mask_studio.vault.composite_models import CompositeMapping, CompositeVariation

PASSWORD = "synthetic password only"


@pytest.fixture
def vault_mappings():
    scalar = DecryptedVaultMapping(
        code="NAME-ABCDEFGHIJKL", prefix="NAME", source_header="Name",
        original_value=" Synthetic Person ", canonical_value="Synthetic Person",
        normalization_rule=Rule.COLLAPSE_WHITESPACE,
        first_seen="2026-01-01", last_seen="2026-01-02", occurrence_count=3,
        variations=(
            DecryptedVariation(" Synthetic Person ", "2026-01-01", "2026-01-02", 2, Rule.COLLAPSE_WHITESPACE),
            DecryptedVariation("Synthetic Person", "2026-01-02", "2026-01-02", 1, Rule.EXACT),
        ),
    )
    code = "PAIR-ABCDEFGHIJKL"
    composite = CompositeMapping(
        code, "PAIR", 1, 1, 2, (Rule.COLLAPSE_WHITESPACE, Rule.EXACT),
        ("Synthetic Person", "Fake City"),
        (CompositeVariation("fake-id-1", code, (" Synthetic Person ", "Fake City"), "2026-01-01", "2026-01-02", 1),
         CompositeVariation("fake-id-2", code, ("Synthetic Person", "Fake City"), "2026-01-02", "2026-01-02", 2)),
        "2026-01-01", "2026-01-02", 3,
    )
    return scalar, composite


@pytest.fixture
def payload(vault_mappings):
    scalar, composite = vault_mappings
    return TransferPayload("1.2.0", MaskedFileBinding("ab" * 32, 1234),
                           (ScalarRestorationMapping.from_vault(scalar),),
                           (CompositeRestorationMapping.from_vault(composite),))


def test_round_trip_preserves_restoration_values_and_binding(payload, tmp_path):
    path = tmp_path / "synthetic.dmspackage"
    assert write_package(path, payload, PASSWORD) == path
    result = read_package(path, PASSWORD)
    assert result == payload
    assert result.scalar_mappings[0].original_value == " Synthetic Person "
    assert result.composite_mappings[0].original_values is None
    assert b"Synthetic Person" not in path.read_bytes()
    assert b"Fake City" not in path.read_bytes()
    assert "Synthetic Person" not in repr(payload)
    assert "NAME-" not in repr(payload)


@pytest.mark.parametrize("kind", ["scalar", "composite", "empty"])
def test_independent_mapping_collections(payload, kind):
    p = replace(payload, scalar_mappings=payload.scalar_mappings if kind == "scalar" else (),
                composite_mappings=payload.composite_mappings if kind == "composite" else ())
    assert decrypt_package(encrypt_package(p, PASSWORD), PASSWORD) == p


def test_randomness_and_deterministic_serialization(payload):
    assert encode_payload(payload) == encode_payload(payload)
    first, second = (encrypt_package(payload, PASSWORD) for _ in range(2))
    assert first != second
    h1, h2 = (service.HEADER.unpack(x[:service.HEADER.size]) for x in (first, second))
    assert h1[2] != h2[2] and h1[3] != h2[3]


@pytest.mark.parametrize("change", ["password", "ciphertext", "salt", "nonce", "version", "magic", "truncate", "append", "empty"])
def test_invalid_envelopes_fail_closed(payload, change):
    data = bytearray(encrypt_package(payload, PASSWORD))
    password = PASSWORD
    if change == "password":
        password = "wrong synthetic password"
    elif change in ("ciphertext", "salt", "nonce", "version", "magic"):
        offset = {"ciphertext": -1, "salt": len(service.MAGIC) + 2,
                  "nonce": service.HEADER.size - 1, "version": len(service.MAGIC) + 1, "magic": 0}[change]
        data[offset] ^= 1
    elif change == "truncate":
        data = data[:-9]
    elif change == "append":
        data += b"extra"
    else:
        data = b""
    with pytest.raises(PackageError) as error:
        decrypt_package(bytes(data), password)
    assert "Synthetic" not in str(error.value)
    assert password not in str(error.value)


def seal_plaintext(raw):
    salt, nonce = os.urandom(16), os.urandom(12)
    header = service.HEADER.pack(service.MAGIC, 1, salt, nonce)
    return header + AESGCM(service._key(PASSWORD, salt)).encrypt(nonce, raw, header)


@pytest.mark.parametrize("change", [
    "unknown", "missing", "version", "bool", "digest", "size", "rule", "count",
    "component_count", "component_version", "original_count", "duplicate_code",
    "tuple_type", "original_type", "surrogate", "dates", "source",
])
def test_authenticated_malformed_payload_is_rejected(payload, change):
    doc = json.loads(encode_payload(payload))
    scalar, composite = doc["scalar_mappings"][0], doc["composite_mappings"][0]
    if change == "unknown": doc["local_keys"] = "must not be accepted"
    elif change == "missing": del doc["masked_file"]
    elif change == "version": doc["format_version"] = 2
    elif change == "bool": doc["format_version"] = True
    elif change == "digest": doc["masked_file"]["sha256"] = "bad"
    elif change == "size": doc["masked_file"]["size"] = -1
    elif change == "rule": scalar["normalization_rule"] = "future-rule"
    elif change == "count": scalar["occurrence_count"] = 99
    elif change == "component_count": composite["component_count"] = 3
    elif change == "component_version": composite["identity_version"] = 2
    elif change == "original_count": composite["original_values"] = ["one"]
    elif change == "duplicate_code": doc["scalar_mappings"].append(scalar.copy())
    elif change == "tuple_type": composite["canonical_values"] = "not a tuple"
    elif change == "original_type": scalar["original_value"] = 12
    elif change == "dates": scalar["first_seen"] = "2026-01-01"
    elif change == "source": scalar["source_header"] = "Name"
    else: scalar["original_value"] = "\ud800"
    with pytest.raises(PackageError):
        decrypt_package(seal_plaintext(json.dumps(doc).encode()), PASSWORD)


@pytest.mark.parametrize("raw", [b"not JSON", b"[]", b'{"format_version":1,"format_version":1}', b'\xff'])
def test_invalid_json(raw):
    with pytest.raises(PackageError):
        decrypt_package(seal_plaintext(raw), PASSWORD)


def test_backup_domain_is_disjoint(payload):
    from data_mask_studio.backup.format import build_prefix, read_prefix
    from data_mask_studio.backup.models import BackupHeader, ScryptParameters
    from data_mask_studio.backup.crypto import derive_key
    from data_mask_studio.backup.exceptions import BackupValidationError
    salt, nonce = b"s" * 16, b"n" * 12
    assert service._key(PASSWORD, salt) != derive_key(PASSWORD, salt)
    backup_header = build_prefix(BackupHeader(1, ScryptParameters(), salt, nonce))
    backup = backup_header + AESGCM(derive_key(PASSWORD, salt)).encrypt(nonce, encode_payload(payload), backup_header)
    with pytest.raises(PackageError): decrypt_package(backup, PASSWORD)
    with pytest.raises(BackupValidationError): read_prefix(io.BytesIO(encrypt_package(payload, PASSWORD)))


def test_no_local_key_or_environment_access(payload, tmp_path, monkeypatch):
    from data_mask_studio.security import LocalKeyProvider
    from data_mask_studio.vault import VaultKeyProvider
    from data_mask_studio.security.windows_dpapi import WindowsDPAPIProtector
    def forbidden(*args, **kwargs): raise AssertionError("Local key access")
    monkeypatch.setattr(LocalKeyProvider, "get_key", forbidden)
    monkeypatch.setattr(VaultKeyProvider, "get_key", forbidden)
    monkeypatch.setattr(WindowsDPAPIProtector, "protect", forbidden)
    monkeypatch.setattr(WindowsDPAPIProtector, "unprotect", forbidden)
    path = tmp_path / "portable.dmspackage"
    write_package(path, payload, PASSWORD)
    assert read_package(path, PASSWORD) == payload
    # Fresh interpreter, independent of importing app/pytest and existing keys.
    result = subprocess.run([sys.executable, "-c",
        "from data_mask_studio.transfer_package import read_package; import sys; "
        "assert read_package(sys.argv[1], sys.argv[2]).masked_file.size == 1234",
        str(path), PASSWORD], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failure", ["serialization", "encryption", "publication", "fsync"])
def test_failure_never_publishes_partial_file(payload, tmp_path, monkeypatch, failure):
    path = tmp_path / "result.dmspackage"
    def fail(*args, **kwargs): raise OSError("synthetic failure")
    if failure == "serialization":
        payload = replace(payload, format_version=99)
    elif failure == "encryption":
        monkeypatch.setattr(service, "_key", fail)
    elif failure == "fsync":
        monkeypatch.setattr(service.os, "fsync", fail)
    else:
        monkeypatch.setattr(service, "publish", fail)
    with pytest.raises((PackageError, OSError)):
        write_package(path, payload, PASSWORD)
    assert list(tmp_path.iterdir()) == []


def test_no_overwrite_even_with_competing_destination(payload, tmp_path, monkeypatch):
    path = tmp_path / "result.dmspackage"
    original_publish = service.publish
    def compete(staged, destination, overwrite):
        assert b"Synthetic Person" not in staged.read_bytes()
        destination.write_bytes(b"existing")
        return original_publish(staged, destination, overwrite)
    monkeypatch.setattr(service, "publish", compete)
    with pytest.raises(PackageError): write_package(path, payload, PASSWORD)
    assert path.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [path]


def test_limits_and_public_validation(payload):
    validate_payload(payload)
    with pytest.raises(PackageError): decrypt_package(b"x" * 100, PASSWORD, max_payload_bytes=10)
    with pytest.raises(PackageError): validate_payload(replace(payload, masked_file=MaskedFileBinding("bad", 0)))


def test_multiple_scalar_mappings_and_legacy_identifier(payload):
    first = payload.scalar_mappings[0]
    second = replace(first, code="LEGACY-" + "A1" * 12)
    value = replace(payload, scalar_mappings=(first, second))
    assert decrypt_package(encrypt_package(value, PASSWORD), PASSWORD) == value


@pytest.mark.parametrize("password", ["", "short", " " * 15])
def test_password_policy(payload, password):
    with pytest.raises(PackageError): encrypt_package(payload, password)


def test_invalid_payload_does_not_modify_existing_destination(payload, tmp_path):
    path = tmp_path / "existing.dmspackage"
    path.write_bytes(b"previous")
    with pytest.raises(PackageError):
        write_package(path, replace(payload, format_version=2), PASSWORD)
    assert path.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [path]


def test_explicit_v1_schema_and_minimization(payload):
    assert json.loads(encode_payload(payload)) == {
        "format_name": "Data Mask Studio Transfer Package", "format_version": 1,
        "application_version": "1.2.0", "masked_file": {"sha256": "ab" * 32, "size": 1234},
        "scalar_mappings": [{"code": "NAME-ABCDEFGHIJKL", "original_value": " Synthetic Person ",
                             "canonical_value": "Synthetic Person"}],
        "composite_mappings": [{"code": "PAIR-ABCDEFGHIJKL", "identity_version": 1,
                                "canonical_values": ["Synthetic Person", "Fake City"], "original_values": None}],
    }
    for excluded in ("first_seen", "last_seen", "occurrence_count", "identifier", "prefix",
                     "source_header", "normalization_rule", "payload_version", "component_count", "variations"):
        assert ('"' + excluded + '"').encode() not in encode_payload(payload)


def test_vault_evolution_does_not_change_wire_format(payload, vault_mappings):
    from types import SimpleNamespace
    scalar, composite = vault_mappings
    # Simulate reordered fields, new secrets, and removal of all operational fields.
    evolved_scalar = SimpleNamespace(canonical_value=scalar.canonical_value, future_secret="never export",
                                     original_value=scalar.original_value, code=scalar.code)
    evolved_composite = SimpleNamespace(variations=composite.variations, future_secret="never export",
                                        canonical_values=composite.canonical_values, code=composite.code,
                                        identity_version=composite.identity_version)
    projected = replace(payload, scalar_mappings=(ScalarRestorationMapping.from_vault(evolved_scalar),),
                        composite_mappings=(CompositeRestorationMapping.from_vault(evolved_composite),))
    assert encode_payload(projected) == encode_payload(payload)
    # Even additional fields on the transfer's in-memory model cannot leak.
    augmented = SimpleNamespace(**vars(evolved_scalar), first_seen="private timestamp")
    assert encode_payload(replace(payload, scalar_mappings=(augmented,))) == encode_payload(payload)


def test_composite_unique_original_and_ambiguous_canonical(vault_mappings, payload):
    _, composite = vault_mappings
    unique = CompositeRestorationMapping.from_vault(replace(composite, variations=composite.variations[:1]))
    assert unique.original_values == composite.variations[0].original_values
    value = replace(payload, composite_mappings=(unique,))
    assert decrypt_package(encrypt_package(value, PASSWORD), PASSWORD) == value
    assert CompositeRestorationMapping.from_vault(composite).original_values is None
    assert unique.canonical_values == composite.canonical_values


def test_exact_size_boundary_across_all_apis(payload, tmp_path):
    size = len(encode_payload(payload))
    content = encrypt_package(payload, PASSWORD, max_payload_bytes=size)
    assert decrypt_package(content, PASSWORD, max_payload_bytes=size) == payload
    assert decode_payload(encode_payload(payload), max_payload_bytes=size) == payload
    path = tmp_path / "limit.dmspackage"
    write_package(path, payload, PASSWORD, max_payload_bytes=size)
    assert read_package(path, PASSWORD, max_payload_bytes=size) == payload
    for operation in (
        lambda: encode_payload(payload, max_payload_bytes=size - 1),
        lambda: decode_payload(encode_payload(payload), max_payload_bytes=size - 1),
        lambda: encrypt_package(payload, PASSWORD, max_payload_bytes=size - 1),
        lambda: decrypt_package(content, PASSWORD, max_payload_bytes=size - 1),
        lambda: read_package(path, PASSWORD, max_payload_bytes=size - 1),
        lambda: write_package(tmp_path / "absent.dmspackage", payload, PASSWORD, max_payload_bytes=size - 1),
    ):
        with pytest.raises(PackageError, match="limite de memória"):
            operation()
    assert not (tmp_path / "absent.dmspackage").exists()
    assert decrypt_package(content, PASSWORD, max_payload_bytes=MAX_PAYLOAD_SIZE + 1) == payload


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, HARD_PAYLOAD_LIMIT + 1])
def test_untrusted_or_unbounded_limits_rejected(payload, limit):
    with pytest.raises(PackageError): encrypt_package(payload, PASSWORD, max_payload_bytes=limit)


def test_size_rejection_precedes_key_derivation(payload, monkeypatch):
    content = encrypt_package(payload, PASSWORD)
    def forbidden(*args): raise AssertionError("KDF must not run")
    monkeypatch.setattr(service, "_key", forbidden)
    with pytest.raises(PackageError, match="limite de memória"):
        decrypt_package(content, PASSWORD, max_payload_bytes=1)
