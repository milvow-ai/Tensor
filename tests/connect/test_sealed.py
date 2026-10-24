"""Tests for sealed input encryption and envelope handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from farm.connect.sealed import (
    SealedEnvelope,
    SealedError,
    b64url_decode,
    b64url_encode,
    load_or_create_keys,
    open_sealed,
    publish_public_key,
    seal,
)
from farm.db.pool import DbPool


def test_sealed_round_trip(tmp_path: Path) -> None:
    priv, pub_jwk, _ = load_or_create_keys(tmp_path)
    aad = "00000000-0000-0000-0000-000000000001|session-abc|login_input"
    secret_text = "super-sensitive-token-12345"

    envelope = seal(secret_text, pub_jwk, aad)
    assert envelope["v"] == 1
    assert envelope["alg"] == "ECDH-ES+HKDF-SHA256+A256GCM"

    # Open with dict
    recovered = open_sealed(envelope, private_key=priv, aad=aad)
    assert recovered == secret_text

    # Open with SealedEnvelope model
    env_model = SealedEnvelope.model_validate(envelope)
    assert open_sealed(env_model, private_key=priv, aad=aad) == secret_text


def test_sealed_wrong_aad_raises(tmp_path: Path) -> None:
    priv, pub_jwk, _ = load_or_create_keys(tmp_path)
    secret_text = "my-secret-value"
    aad = "tenant1|sess1|login_input"
    envelope = seal(secret_text, pub_jwk, aad)

    with pytest.raises(SealedError) as exc_info:
        open_sealed(envelope, private_key=priv, aad="tenant2|sess1|login_input")

    msg = str(exc_info.value)
    assert secret_text not in msg
    assert envelope["ct"] not in msg


def test_sealed_tampered_ct_raises(tmp_path: Path) -> None:
    priv, pub_jwk, _ = load_or_create_keys(tmp_path)
    secret_text = "another-secret"
    aad = "tenant1|sess1|login_input"
    envelope = seal(secret_text, pub_jwk, aad)

    raw_ct = bytearray(b64url_decode(envelope["ct"]))
    raw_ct[0] ^= 0xFF
    envelope["ct"] = b64url_encode(bytes(raw_ct))

    with pytest.raises(SealedError) as exc_info:
        open_sealed(envelope, private_key=priv, aad=aad)

    msg = str(exc_info.value)
    assert secret_text not in msg
    assert envelope["ct"] not in msg


def test_sealed_wrong_key_raises(tmp_path: Path) -> None:
    priv1, pub1_jwk, _ = load_or_create_keys(tmp_path / "k1")
    priv2, _, _ = load_or_create_keys(tmp_path / "k2")
    secret_text = "secret-for-priv1"
    aad = "tenant1|sess1|login_input"
    envelope = seal(secret_text, pub1_jwk, aad)

    with pytest.raises(SealedError) as exc_info:
        open_sealed(envelope, private_key=priv2, aad=aad)

    msg = str(exc_info.value)
    assert secret_text not in msg


def test_sealed_vectors_file_opens() -> None:
    vectors_path = Path(__file__).parent / "fixtures" / "sealed_vectors.json"
    assert vectors_path.exists(), f"Fixtures file not found: {vectors_path}"

    data = json.loads(vectors_path.read_text(encoding="utf-8"))
    assert "TEST-ONLY" in data["comment"]

    pem_bytes = data["private_key_pem"].encode("ascii")
    priv_key = serialization.load_pem_private_key(pem_bytes, password=None)
    assert isinstance(priv_key, ec.EllipticCurvePrivateKey)

    assert len(data["vectors"]) >= 3
    for vec in data["vectors"]:
        recovered = open_sealed(vec["envelope"], private_key=priv_key, aad=vec["aad"])
        assert recovered == vec["plaintext"]


def test_sealed_envelope_rejects_extra_fields(tmp_path: Path) -> None:
    _, pub_jwk, _ = load_or_create_keys(tmp_path)
    envelope = seal("test", pub_jwk, "t|s|p")
    envelope["extra_field"] = "malicious"

    with pytest.raises(ValidationError):
        SealedEnvelope.model_validate(envelope)


def test_load_or_create_keys_persistence(tmp_path: Path) -> None:
    priv1, jwk1, salt1 = load_or_create_keys(tmp_path)
    assert (tmp_path / "connect.pem").exists()
    assert (tmp_path / "connect-salt").exists()
    assert len(salt1) == 32

    # Second call should load identical keys and salt
    priv2, jwk2, salt2 = load_or_create_keys(tmp_path)
    assert jwk1 == jwk2
    assert salt1 == salt2
    assert (
        priv1.private_numbers().private_value
        == priv2.private_numbers().private_value
    )


async def test_publish_public_key_to_db(pool: DbPool, tmp_path: Path) -> None:
    priv, pub_jwk, salt = load_or_create_keys(tmp_path)
    await publish_public_key(pool, pub_jwk)

    async with pool.connection() as conn:
        cur = await conn.execute("select connect_public_key from public.farm_settings where id = 1")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == pub_jwk
