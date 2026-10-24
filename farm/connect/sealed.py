"""Sealed input: ECDH-ES+HKDF-SHA256+A256GCM envelope encryption for sensitive values."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ConfigDict, Field

from farm.db.pool import DbPool, system_connection
from farm.settings import data_dir

ALGORITHM = "ECDH-ES+HKDF-SHA256+A256GCM"
HKDF_INFO = b"harness-farm/connect/v1"


class SealedError(Exception):
    """Raised when opening a sealed envelope fails.

    The error message must never leak plaintext, key material, or ciphertext.
    """


class EpkJwk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kty: Literal["EC"]
    crv: Literal["P-256"]
    x: Annotated[str, Field(min_length=1, max_length=100)]
    y: Annotated[str, Field(min_length=1, max_length=100)]


class SealedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1]
    alg: Literal["ECDH-ES+HKDF-SHA256+A256GCM"]
    epk: EpkJwk
    iv: Annotated[str, Field(min_length=1, max_length=50)]
    ct: Annotated[str, Field(min_length=1, max_length=65536)]


def b64url_encode(data: bytes) -> str:
    """Encode bytes to unpadded base64url string."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(val: str) -> bytes:
    """Decode unpadded or padded base64url string to bytes."""
    padding = "=" * ((4 - len(val) % 4) % 4)
    return base64.urlsafe_b64decode((val + padding).encode("ascii"))


def public_key_to_jwk(pub: ec.EllipticCurvePublicKey) -> dict[str, str]:
    """Extract P-256 public key coordinates into a JWK dictionary."""
    numbers = pub.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_encode(numbers.x.to_bytes(32, "big")),
        "y": b64url_encode(numbers.y.to_bytes(32, "big")),
    }


def jwk_to_public_key(jwk: dict[str, Any] | EpkJwk) -> ec.EllipticCurvePublicKey:
    """Reconstruct an EC P-256 public key from a JWK."""
    if isinstance(jwk, EpkJwk):
        x_str, y_str = jwk.x, jwk.y
    else:
        x_str, y_str = jwk["x"], jwk["y"]
    x_bytes = b64url_decode(x_str)
    y_bytes = b64url_decode(y_str)
    if len(x_bytes) != 32 or len(y_bytes) != 32:
        raise ValueError("Invalid coordinate length for P-256")
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(x_bytes, "big"),
        int.from_bytes(y_bytes, "big"),
        ec.SECP256R1(),
    ).public_key()


def load_or_create_keys(
    dir: Path | None = None,
) -> tuple[ec.EllipticCurvePrivateKey, dict[str, str], bytes]:
    """Load or create the P-256 private key and 32-byte salt.

    - POSIX: mode 0600 on created key and salt files.
    - Windows: permissions rely on the user profile ACL protecting files under %LOCALAPPDATA%.
    """
    keys_dir = dir if dir is not None else (data_dir() / "keys")
    keys_dir.mkdir(parents=True, exist_ok=True)
    pem_path = keys_dir / "connect.pem"
    salt_path = keys_dir / "connect-salt"

    if pem_path.exists() and salt_path.exists():
        pem_bytes = pem_path.read_bytes()
        loaded_key = serialization.load_pem_private_key(pem_bytes, password=None)
        if not isinstance(loaded_key, ec.EllipticCurvePrivateKey):
            raise TypeError("Loaded key is not an EllipticCurvePrivateKey")
        salt = salt_path.read_bytes()
        jwk = public_key_to_jwk(loaded_key.public_key())
        return loaded_key, jwk, salt

    # Generate new key pair and salt
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    salt = os.urandom(32)

    if os.name != "nt":
        # POSIX: mode 0600
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd_pem = os.open(str(pem_path), flags, 0o600)
        with open(fd_pem, "wb") as f:
            f.write(pem_bytes)
        fd_salt = os.open(str(salt_path), flags, 0o600)
        with open(fd_salt, "wb") as f:
            f.write(salt)
    else:
        # Windows: rely on the profile ACL protecting %LOCALAPPDATA%
        pem_path.write_bytes(pem_bytes)
        salt_path.write_bytes(salt)

    jwk = public_key_to_jwk(private_key.public_key())
    return private_key, jwk, salt


def seal(plaintext: str, public_jwk: dict[str, Any], aad: str | bytes) -> dict[str, Any]:
    """Encrypt plaintext into a v1 sealed envelope dictionary."""
    recipient_pub = jwk_to_public_key(public_jwk)

    # Ephemeral key pair
    epk_priv = ec.generate_private_key(ec.SECP256R1())
    epk_jwk = public_key_to_jwk(epk_priv.public_key())

    # ECDH -> HKDF-SHA256 -> AES-256 key
    shared_secret = epk_priv.exchange(ec.ECDH(), recipient_pub)
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"",
        info=HKDF_INFO,
    )
    aes_key = hkdf.derive(shared_secret)

    # AES-256-GCM encryption with 12-byte IV
    iv = os.urandom(12)
    aesgcm = AESGCM(aes_key)
    aad_bytes = aad.encode("utf-8") if isinstance(aad, str) else aad
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), aad_bytes)

    return {
        "v": 1,
        "alg": ALGORITHM,
        "epk": epk_jwk,
        "iv": b64url_encode(iv),
        "ct": b64url_encode(ct),
    }


def open_sealed(
    envelope: SealedEnvelope | dict[str, Any],
    *,
    private_key: ec.EllipticCurvePrivateKey,
    aad: str | bytes,
) -> str:
    """Decrypt a sealed envelope using the private key and matching AAD.

    Raises SealedError on any failure.
    """
    try:
        if isinstance(envelope, dict):
            env_obj = SealedEnvelope.model_validate(envelope)
        elif isinstance(envelope, SealedEnvelope):
            env_obj = envelope
        else:
            raise TypeError("Envelope must be a dict or SealedEnvelope")

        epk_pub = jwk_to_public_key(env_obj.epk)
        shared_secret = private_key.exchange(ec.ECDH(), epk_pub)
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"",
            info=HKDF_INFO,
        )
        aes_key = hkdf.derive(shared_secret)

        iv = b64url_decode(env_obj.iv)
        if len(iv) != 12:
            raise ValueError("IV must be 12 bytes")
        ct = b64url_decode(env_obj.ct)
        aad_bytes = aad.encode("utf-8") if isinstance(aad, str) else aad

        aesgcm = AESGCM(aes_key)
        plaintext_bytes = aesgcm.decrypt(iv, ct, aad_bytes)
        return plaintext_bytes.decode("utf-8")
    except Exception:
        raise SealedError("Failed to open sealed envelope") from None


async def publish_public_key(
    pool: DbPool,
    jwk: dict[str, str] | None = None,
    keys_dir: Path | None = None,
) -> None:
    """Publish the connect public key JWK to farm_settings.connect_public_key."""
    from psycopg.types.json import Jsonb

    if jwk is None:
        _, jwk, _ = load_or_create_keys(keys_dir)

    async with system_connection(pool) as conn:
        await conn.execute(
            "update public.farm_settings set connect_public_key = %s where id = 1",
            (Jsonb(jwk),),
        )
