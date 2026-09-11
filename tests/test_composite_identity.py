import hashlib
import inspect
import re

import pytest

from data_mask_studio.anonymization.token_generator import generate_token
from data_mask_studio.processing.composite_identity import (
    COMPOSITE_HMAC_DOMAIN, COMPOSITE_IDENTITY_VERSION, CompositeIdentityError,
    generate_composite_token, serialize_composite_identity,
)

KEY = bytes(range(32))


@pytest.mark.parametrize("values, expected_hex, expected_token", [
    (("A", "B"), "444d5343490100000002000000000000000141000000000000000142", "CORR-B2EJHSBXNVVN"),
    (("é", "🔒"), "444d53434901000000020000000000000002c3a90000000000000004f09f9492", "CORR-FI6TNW6FK6BZ"),
])
def test_frozen_v1_vectors(values, expected_hex, expected_token):
    assert COMPOSITE_IDENTITY_VERSION == 1
    assert serialize_composite_identity(values) == bytes.fromhex(expected_hex)
    assert generate_composite_token(KEY, "CORR", values) == expected_token
    assert generate_composite_token(KEY, "CORR", list(values)) == expected_token
    assert re.fullmatch(r"CORR-[A-Z2-7]{12}", expected_token)


@pytest.mark.parametrize("left, right", [
    (("A", "B"), ("B", "A")),
    (("AB", "C"), ("A", "BC")),
    (("A;", "B"), ("A", ";B")),
    (("A|", "B"), ("A", "|B")),
    (("A\0", "B"), ("A", "\0B")),
    (("é", "X"), ("e\u0301", "X")),
    ((" A ", "B"), ("A", "B")),
    (("A  B", "C"), ("A B", "C")),
    (("A", "B"), ("a", "B")),
])
def test_boundaries_order_and_no_implicit_normalization(left, right):
    assert serialize_composite_identity(left) != serialize_composite_identity(right)
    assert generate_composite_token(KEY, "CORR", left) != generate_composite_token(KEY, "CORR", right)


def test_empty_components_and_cardinality_are_preserved():
    sequences = [("", "ABC"), ("ABC", ""), ("", "", "ABC"), ("", "ABC", ""), ("", ""), ("A", "B"), ("A", "B", ""), ("A", "", "B")]
    assert len({serialize_composite_identity(values) for values in sequences}) == len(sequences)
    assert len({generate_composite_token(KEY, "CORR", values) for values in sequences}) == len(sequences)
    assert serialize_composite_identity(("", "")) == bytes.fromhex("444d5343490100000002" + "00" * 16)


def test_key_and_namespace_participate_in_identity():
    original = generate_composite_token(KEY, "CORR", ("A", "B"))
    assert original != generate_composite_token(b"K" * 32, "CORR", ("A", "B"))
    other = generate_composite_token(KEY, "PESSOA", ("A", "B"))
    assert original.split("-")[1] != other.split("-")[1]


@pytest.mark.parametrize("values", [[], ["A"], "AB", b"AB", {"A", "B"}, ["A", 2], [None, "B"], ["A", "\ud800"]])
def test_invalid_values_have_controlled_errors(values):
    with pytest.raises(CompositeIdentityError):
        serialize_composite_identity(values)


@pytest.mark.parametrize("version", [0, 2, -1, True, "1"])
def test_unsupported_version_rejected(version):
    with pytest.raises(CompositeIdentityError, match="Versão"):
        serialize_composite_identity(("A", "B"), version=version)
    with pytest.raises(CompositeIdentityError, match="Versão"):
        generate_composite_token(KEY, "CORR", ("A", "B"), version=version)


@pytest.mark.parametrize("prefix", ["", "A", "corr", "1CORR", "CO-RR", " CORR", "A" * 25, None])
def test_prefix_is_validated_without_normalization(prefix):
    with pytest.raises(CompositeIdentityError):
        generate_composite_token(KEY, prefix, ("A", "B"))


@pytest.mark.parametrize("key", [b"", "secret", None])
def test_invalid_key(key):
    with pytest.raises(CompositeIdentityError):
        generate_composite_token(key, "CORR", ("A", "B"))


def test_scalar_and_composite_hmac_messages_are_disjoint(monkeypatch):
    import hmac

    captured = []
    real_new = hmac.new

    def capture(key, message, digestmod):
        captured.append(message)
        assert digestmod is hashlib.sha256
        return real_new(key, message, digestmod)

    monkeypatch.setattr(hmac, "new", capture)
    payload = serialize_composite_identity(("A", "B"))
    scalar = generate_token(KEY, "CORR", payload.decode("utf-8"))
    composed = generate_composite_token(KEY, "CORR", ("A", "B"))
    assert scalar != composed
    assert captured[0] == b"CORR\0" + payload
    assert captured[1] == b"\xffDMS-COMPOSITE-HMAC\0\0\0\0\x04CORR" + payload
    assert captured[1].startswith(COMPOSITE_HMAC_DOMAIN)
    with pytest.raises(UnicodeDecodeError):
        captured[1].decode("utf-8")
    # Even a textual imitation of the entire composite domain cannot encode FF.
    generate_token(KEY, "CORR", captured[1].decode("latin-1"))
    assert captured[2] != captured[1]


def test_serialization_is_independent_of_hmac(monkeypatch):
    import hmac

    def forbidden(*args, **kwargs):
        pytest.fail("Serializer must not call HMAC")

    monkeypatch.setattr(hmac, "new", forbidden)
    assert serialize_composite_identity(("A", "B"))


def test_api_has_no_metadata_and_errors_do_not_disclose_values():
    assert tuple(inspect.signature(generate_composite_token).parameters) == (
        "key", "prefix", "canonical_values", "version",
    )
    secret_value = "PRIVATE_CANONICAL_TEST_VALUE"
    with pytest.raises(CompositeIdentityError) as error:
        serialize_composite_identity((secret_value + "\ud800", "B"))
    assert secret_value not in str(error.value)
    assert error.value.__suppress_context__
