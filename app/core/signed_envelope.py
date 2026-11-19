"""Signed envelopes: the one format for everything the publisher signs (licences, release manifests).

Not used by the running program yet (docs/PROTECTION_PLAN.md): wiring is a later, installer-time change.

An envelope is a small JSON text:

    {"envelope": 1, "purpose": "license",
     "payload": "<base64 of the exact payload bytes>",
     "signatures": [{"key": "<key id>", "sig": "<base64 Ed25519 signature>"}]}

- The signature covers the exact payload bytes, so there is nothing to re-serialise and no room for two
  parsers to disagree about what was signed.
- The signed message starts with the purpose (`pharmacy-platform/<purpose>/v1`): a signature made for a
  licence can never be replayed as a release signature, even if the same key were used for both by mistake.
- There is no algorithm field: Ed25519 only, so nothing can be downgraded.
- Several signatures are allowed so a key can be replaced: sign with the old and the new key during the
  change-over. One valid signature by a trusted key is enough; a signature that names a trusted key and
  does not verify refuses the whole envelope.

Verification needs only public keys. Private keys never live in the repository or on a customer PC.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ENVELOPE_VERSION = 1
MAX_ENVELOPE_BYTES = 4 * 1024 * 1024          # a release manifest lists every file; a licence is ~1 KB
MAX_SIGNATURES = 4
_PURPOSE = re.compile(r"[a-z][a-z0-9-]{1,31}")
_KEY_ID = re.compile(r"[0-9a-f]{16}")
_HEX_KEY = re.compile(r"[0-9a-f]{64}")


class EnvelopeError(ValueError):
    """Refused. `code` is stable for logs and tests: malformed | wrong_purpose | no_trusted_key |
    untrusted_key | bad_signature."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def public_key_bytes(key) -> bytes:
    """Raw 32 bytes of a public key given as bytes, 64 hex characters, or a cryptography key object."""
    if isinstance(key, Ed25519PrivateKey):
        key = key.public_key()
    if isinstance(key, Ed25519PublicKey):
        return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if isinstance(key, str):
        if not _HEX_KEY.fullmatch(key):
            raise EnvelopeError("malformed", "a public key is 64 lowercase hex characters")
        key = bytes.fromhex(key)
    if not isinstance(key, bytes) or len(key) != 32:
        raise EnvelopeError("malformed", "a public key is 32 bytes")
    return key


def key_id(key) -> str:
    """Short name of a public key (first 16 hex characters of its SHA-256)."""
    return hashlib.sha256(public_key_bytes(key)).hexdigest()[:16]


def _message(purpose: str, payload: bytes) -> bytes:
    return b"pharmacy-platform/" + purpose.encode("ascii") + b"/v1\n" + payload


def _check_purpose(purpose: str) -> None:
    if not isinstance(purpose, str) or not _PURPOSE.fullmatch(purpose):
        raise EnvelopeError("malformed", "bad purpose")


def canonical_json(value) -> bytes:
    """Deterministic JSON for payloads the publisher signs: sorted keys, no spaces, UTF-8, no floats."""
    def no_floats(v):
        if isinstance(v, float):
            raise TypeError("floats are not allowed in a signed payload")
        if isinstance(v, dict):
            for k, x in v.items():
                if not isinstance(k, str):
                    raise TypeError("object keys must be text")
                no_floats(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                no_floats(x)
    no_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def strict_json(data: bytes):
    """Parse JSON refusing what two parsers could read differently: duplicate keys, NaN/Infinity, floats,
    invalid UTF-8."""
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError(f"duplicate key: {k}")
            out[k] = v
        return out

    def refuse(text):
        raise ValueError(f"number not allowed: {text}")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_float=refuse, parse_constant=refuse)
    except (ValueError, RecursionError) as e:      # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise EnvelopeError("malformed", str(e)) from None


def sign(payload: bytes, purpose: str, *private_keys: Ed25519PrivateKey) -> bytes:
    """Publisher side: the envelope for `payload`, signed by each key given (at least one)."""
    _check_purpose(purpose)
    if not isinstance(payload, bytes) or not payload:
        raise EnvelopeError("malformed", "empty payload")
    if not private_keys or len(private_keys) > MAX_SIGNATURES:
        raise EnvelopeError("malformed", f"between 1 and {MAX_SIGNATURES} signing keys")
    message = _message(purpose, payload)
    signatures = [{"key": key_id(k), "sig": base64.b64encode(k.sign(message)).decode("ascii")} for k in private_keys]
    if len({s["key"] for s in signatures}) != len(signatures):
        raise EnvelopeError("malformed", "the same key was given twice")
    out = json.dumps({"envelope": ENVELOPE_VERSION, "purpose": purpose,
                      "payload": base64.b64encode(payload).decode("ascii"), "signatures": signatures},
                     separators=(",", ":")).encode("ascii")
    if len(out) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError("malformed", "payload too large")
    return out


def _b64(text, what: str) -> bytes:
    if not isinstance(text, str):
        raise EnvelopeError("malformed", f"{what} is not text")
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise EnvelopeError("malformed", f"{what} is not base64") from None


def open_envelope(data: bytes, purpose: str, trusted_keys) -> bytes:
    """Customer side: the payload bytes, only if a trusted key signed them for this purpose.

    `trusted_keys`: the public keys built into the program (bytes or hex). With none, everything is
    refused (`no_trusted_key`): a build without a publisher key must fail closed, never open."""
    _check_purpose(purpose)
    trusted = {key_id(k): public_key_bytes(k) for k in trusted_keys}
    if not trusted:
        raise EnvelopeError("no_trusted_key", "this build has no publisher key")
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError("malformed", "not an envelope or too large")
    env = strict_json(bytes(data))
    if not isinstance(env, dict) or set(env) != {"envelope", "purpose", "payload", "signatures"}:
        raise EnvelopeError("malformed", "unexpected fields")
    if type(env["envelope"]) is not int or env["envelope"] != ENVELOPE_VERSION:
        raise EnvelopeError("malformed", "unsupported envelope version")
    if env["purpose"] != purpose:
        raise EnvelopeError("wrong_purpose", f"this file is not a {purpose}")
    payload = _b64(env["payload"], "payload")
    if not payload:
        raise EnvelopeError("malformed", "empty payload")
    signatures = env["signatures"]
    if not isinstance(signatures, list) or not 1 <= len(signatures) <= MAX_SIGNATURES:
        raise EnvelopeError("malformed", "bad signature list")
    message, verified, seen = _message(purpose, payload), False, set()
    for s in signatures:
        if not isinstance(s, dict) or set(s) != {"key", "sig"} or not isinstance(s["key"], str) \
                or not _KEY_ID.fullmatch(s["key"]) or s["key"] in seen:
            raise EnvelopeError("malformed", "bad signature entry")
        seen.add(s["key"])
        sig = _b64(s["sig"], "signature")
        if len(sig) != 64:
            raise EnvelopeError("malformed", "bad signature length")
        if s["key"] not in trusted:
            continue                                # a newer or retired key: ignored, never counted
        try:
            Ed25519PublicKey.from_public_bytes(trusted[s["key"]]).verify(sig, message)
        except InvalidSignature:
            raise EnvelopeError("bad_signature", "the content does not match its signature") from None
        verified = True
    if not verified:
        raise EnvelopeError("untrusted_key", "not signed by the publisher of this program")
    return payload


# --- publisher key files (used by the signing tools only; a customer PC never holds a private key) ---

MIN_PASSPHRASE = 12


def new_private_key_pem(passphrase: str) -> tuple[bytes, str]:
    """A new Ed25519 private key as passphrase-protected PEM, and its public key in hex (to build in)."""
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"the passphrase needs at least {MIN_PASSPHRASE} characters")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.BestAvailableEncryption(passphrase.encode("utf-8")))
    return pem, public_key_bytes(key).hex()


def load_private_key(pem: bytes, passphrase: str) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=passphrase.encode("utf-8"))
    except (ValueError, TypeError) as e:
        raise ValueError("the key file could not be opened: wrong passphrase or not a key file") from e
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 key")
    return key
