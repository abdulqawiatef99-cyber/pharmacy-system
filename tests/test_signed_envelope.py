"""Signed envelopes (app/core/signed_envelope.py): what the publisher signs must open only when a
trusted key signed exactly these bytes for exactly this purpose. Keys are made inside the tests;
no key of any kind is stored in the repository."""
import base64
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import signed_envelope as se


def _edit(data: bytes, **changes) -> bytes:
    env = json.loads(data)
    env.update(changes)
    return json.dumps(env).encode()


class TestSignAndOpen(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.pub = se.public_key_bytes(self.key)
        self.payload = se.canonical_json({"customer": "صيدلية تجربة", "n": 1})
        self.env = se.sign(self.payload, "license", self.key)

    def code(self, data, purpose="license", keys=None):
        with self.assertRaises(se.EnvelopeError) as cm:
            se.open_envelope(data, purpose, [self.pub] if keys is None else keys)
        return cm.exception.code

    def test_round_trip_returns_the_exact_bytes(self):
        self.assertEqual(se.open_envelope(self.env, "license", [self.pub]), self.payload)
        self.assertEqual(se.open_envelope(self.env, "license", [self.pub.hex()]), self.payload)   # hex form too

    def test_the_envelope_is_plain_ascii_json_with_the_documented_fields(self):
        env = json.loads(self.env.decode("ascii"))
        self.assertEqual(set(env), {"envelope", "purpose", "payload", "signatures"})
        self.assertEqual(env["signatures"][0]["key"], se.key_id(self.pub))
        self.assertEqual(base64.b64decode(env["payload"]), self.payload)

    def test_a_build_without_a_publisher_key_refuses_everything(self):
        self.assertEqual(self.code(self.env, keys=[]), "no_trusted_key")

    def test_another_publisher_is_not_trusted(self):
        other = Ed25519PrivateKey.generate()
        self.assertEqual(self.code(self.env, keys=[se.public_key_bytes(other)]), "untrusted_key")

    def test_changed_payload_is_refused(self):
        forged = se.canonical_json({"customer": "صيدلية تجربة", "n": 2})
        self.assertEqual(self.code(_edit(self.env, payload=base64.b64encode(forged).decode())), "bad_signature")

    def test_one_flipped_bit_anywhere_in_the_payload_is_refused(self):
        raw = bytearray(self.payload)
        for i in range(len(raw)):
            raw[i] ^= 1
            data = _edit(self.env, payload=base64.b64encode(bytes(raw)).decode())
            self.assertEqual(self.code(data), "bad_signature", i)
            raw[i] ^= 1

    def test_changed_signature_is_refused(self):
        env = json.loads(self.env)
        sig = bytearray(base64.b64decode(env["signatures"][0]["sig"]))
        sig[0] ^= 1
        env["signatures"][0]["sig"] = base64.b64encode(bytes(sig)).decode()
        self.assertEqual(self.code(json.dumps(env).encode()), "bad_signature")

    def test_a_signature_for_one_purpose_never_opens_as_another(self):
        self.assertEqual(self.code(self.env, purpose="release"), "wrong_purpose")
        # even when the label is edited to match, the signature was made for "license"
        self.assertEqual(self.code(_edit(self.env, purpose="release"), purpose="release"), "bad_signature")

    def test_a_signature_naming_a_trusted_key_with_another_keys_signature_is_refused(self):
        other = Ed25519PrivateKey.generate()
        env = json.loads(se.sign(self.payload, "license", other))
        env["signatures"][0]["key"] = se.key_id(self.pub)
        self.assertEqual(self.code(json.dumps(env).encode()), "bad_signature")


class TestKeyChange(unittest.TestCase):
    def setUp(self):
        self.old, self.new = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        self.payload = b'{"a":1}'

    def test_signed_by_old_and_new_opens_for_a_program_that_knows_either(self):
        env = se.sign(self.payload, "release", self.old, self.new)
        for trusted in ([self.old], [self.new], [self.old, self.new]):
            self.assertEqual(se.open_envelope(env, "release", [se.public_key_bytes(k) for k in trusted]), self.payload)

    def test_an_unknown_extra_signature_is_ignored_but_never_enough(self):
        stranger = Ed25519PrivateKey.generate()
        env = se.sign(self.payload, "release", stranger, self.new)
        self.assertEqual(se.open_envelope(env, "release", [se.public_key_bytes(self.new)]), self.payload)
        with self.assertRaises(se.EnvelopeError) as cm:
            se.open_envelope(env, "release", [se.public_key_bytes(self.old)])
        self.assertEqual(cm.exception.code, "untrusted_key")

    def test_one_bad_trusted_signature_refuses_even_next_to_a_good_one(self):
        env = json.loads(se.sign(self.payload, "release", self.old, self.new))
        env["signatures"][0]["sig"] = env["signatures"][1]["sig"]
        with self.assertRaises(se.EnvelopeError) as cm:
            se.open_envelope(json.dumps(env).encode(), "release",
                             [se.public_key_bytes(self.old), se.public_key_bytes(self.new)])
        self.assertEqual(cm.exception.code, "bad_signature")

    def test_the_same_key_twice_is_refused_when_signing(self):
        with self.assertRaises(se.EnvelopeError):
            se.sign(self.payload, "release", self.old, self.old)


class TestMalformed(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.pub = se.public_key_bytes(self.key)
        self.env = se.sign(b'{"a":1}', "license", self.key)

    def assertMalformed(self, data):
        with self.assertRaises(se.EnvelopeError) as cm:
            se.open_envelope(data, "license", [self.pub])
        self.assertEqual(cm.exception.code, "malformed", data[:80])

    def test_garbage_is_refused_without_a_crash(self):
        env = json.loads(self.env)
        sig = env["signatures"][0]
        cases = [b"", b"not json", b"[]", b"null", b'"text"', b"\xff\xfe\x00", b"{}",
                 _edit(self.env, extra=1),
                 _edit(self.env, envelope=2), _edit(self.env, envelope="1"), _edit(self.env, envelope=True),
                 _edit(self.env, payload=""), _edit(self.env, payload="@@@"), _edit(self.env, payload=5),
                 _edit(self.env, payload=env["payload"] + "\n"),
                 _edit(self.env, signatures=[]), _edit(self.env, signatures="x"), _edit(self.env, signatures=[sig] * 5),
                 _edit(self.env, signatures=[sig, sig]),                                  # the same key twice
                 _edit(self.env, signatures=[{**sig, "alg": "none"}]),
                 _edit(self.env, signatures=[{"key": sig["key"]}]),
                 _edit(self.env, signatures=[{"key": "XYZ", "sig": sig["sig"]}]),
                 _edit(self.env, signatures=[{"key": sig["key"], "sig": "AAAA"}]),
                 _edit(self.env, signatures=[{"key": sig["key"], "sig": 7}]),
                 _edit(self.env, signatures=[7]),
                 b"[" * 100000]
        for data in cases:
            self.assertMalformed(data)

    def test_duplicate_keys_are_refused(self):
        text = self.env.decode()
        self.assertMalformed(('{"purpose":"license",' + text[1:]).encode())

    def test_too_large_is_refused_before_parsing(self):
        self.assertMalformed(b" " * (se.MAX_ENVELOPE_BYTES + 1))

    def test_bad_purpose_names(self):
        for purpose in ("", "License", "a", "x" * 40, "a/b", None):
            with self.assertRaises(se.EnvelopeError):
                se.sign(b"x", purpose, self.key)
            with self.assertRaises(se.EnvelopeError):
                se.open_envelope(self.env, purpose, [self.pub])

    def test_bad_trusted_key_material_is_an_error_not_a_silent_skip(self):
        for bad in ("abc", b"short", "G" * 64, 5):
            with self.assertRaises(se.EnvelopeError):
                se.open_envelope(self.env, "license", [bad])


class TestJson(unittest.TestCase):
    def test_canonical_json_is_stable_and_keeps_arabic(self):
        a = se.canonical_json({"b": 1, "a": "صيدلية", "c": [1, {"z": None, "y": True}]})
        b = se.canonical_json({"c": [1, {"y": True, "z": None}], "a": "صيدلية", "b": 1})
        self.assertEqual(a, b)
        self.assertEqual(a, '{"a":"صيدلية","b":1,"c":[1,{"y":true,"z":null}]}'.encode("utf-8"))

    def test_floats_and_non_text_keys_are_refused(self):
        for bad in ({"a": 1.5}, {"a": [1, {"b": 2.0}]}, {1: "a"}):
            with self.assertRaises(TypeError):
                se.canonical_json(bad)

    def test_strict_json_refuses_ambiguous_input(self):
        for bad in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1.0}', b'{"a":1e3}', b"\xff"):
            with self.assertRaises(se.EnvelopeError):
                se.strict_json(bad)
        self.assertEqual(se.strict_json(b'{"a":[1,"x",null,true]}'), {"a": [1, "x", None, True]})


class TestKeyFiles(unittest.TestCase):
    def test_key_file_round_trip_and_wrong_passphrase(self):
        phrase = "correct horse battery"
        pem, public_hex = se.new_private_key_pem(phrase)
        self.assertIn(b"ENCRYPTED PRIVATE KEY", pem)
        key = se.load_private_key(pem, phrase)
        self.assertEqual(se.public_key_bytes(key).hex(), public_hex)
        with self.assertRaises(ValueError):
            se.load_private_key(pem, "wrong horse battery")
        with self.assertRaises(ValueError):
            se.load_private_key(b"not a key", phrase)

    def test_short_passphrase_is_refused(self):
        with self.assertRaises(ValueError):
            se.new_private_key_pem("short")

    def test_only_ed25519_keys_are_accepted(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        phrase = "correct horse battery"
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(phrase.encode()))
        with self.assertRaises(ValueError):
            se.load_private_key(pem, phrase)

    def test_key_id_is_short_stable_and_differs_per_key(self):
        a, b = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        self.assertRegex(se.key_id(a), r"^[0-9a-f]{16}$")
        self.assertEqual(se.key_id(a), se.key_id(se.public_key_bytes(a).hex()))
        self.assertNotEqual(se.key_id(a), se.key_id(b))


if __name__ == "__main__":
    unittest.main()
