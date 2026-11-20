"""Ed25519 signature of a smart-update manifest (docs/SMART_UPDATE.md §2).

The trust root is the set of public keys inside the ACTIVE (already verified) release, never the keys of
the downloaded one. A new key reaches the PC inside a release signed by an old key. `cryptography` is
already part of the installed packages (requirements.txt), so nothing new ships.
"""
from __future__ import annotations

import base64
import binascii
from pathlib import Path

KEY_DIR = "release_keys"


class SignatureError(RuntimeError):
    pass


def _b64(text: str) -> bytes:
    try:
        return base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError("bad base64") from exc


def trusted_keys(root: Path) -> list[bytes]:
    """Raw 32-byte public keys from <root>/release_keys/*.pub (one base64 line each)."""
    out = []
    for p in sorted((Path(root) / KEY_DIR).glob("*.pub")):
        raw = _b64(p.read_text(encoding="ascii"))
        if len(raw) != 32:
            raise SignatureError(f"bad public key: {p.name}")
        out.append(raw)
    return out


def verify(message: bytes, signature_b64: str, keys: list[bytes]) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not keys:
        raise SignatureError("no trusted update key on this PC")
    sig = _b64(signature_b64)
    for raw in keys:
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, message)
            return
        except InvalidSignature:
            continue
    raise SignatureError("signature does not match a trusted key")


def sign(message: bytes, private_b64: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = _b64(private_b64)
    if len(raw) != 32:
        raise SignatureError("bad private key")
    return base64.b64encode(Ed25519PrivateKey.from_private_bytes(raw).sign(message)).decode("ascii")


def generate() -> tuple[str, str]:
    """(private, public) as base64 of the raw 32 bytes."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(priv).decode("ascii"), base64.b64encode(pub).decode("ascii")


def self_check(root: Path) -> None:
    """A candidate's own signature code works (part of `update --self-check`): its keys load and a fresh
    signature round-trips. Raises on any problem."""
    trusted_keys(root)
    priv, pub = generate()
    msg = b"pharmacy smart-update self-check"
    verify(msg, sign(msg, priv), [_b64(pub)])
    try:
        verify(msg + b"!", sign(msg, priv), [_b64(pub)])
    except SignatureError:
        return
    raise SignatureError("verification accepted a changed message")
