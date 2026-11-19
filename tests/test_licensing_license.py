"""Licence format and decision (app/licensing/license.py): only the publisher's signature opens a
licence, only on its own PC, only inside its dates, and turning the clock back buys nothing.
Keys are made inside the tests; nothing here is a real customer or a real key."""
import base64
import json
import unittest
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import signed_envelope as se
from app.licensing import fingerprint as fp
from app.licensing import keys, license as L

UTC = timezone.utc
T0 = datetime(2026, 12, 1, tzinfo=UTC)
PC_A = fp.from_raw({"board": "4c4c4544-0042-3510-8051-c7c04f4a3432", "system": "guid-a", "disk": "a4f1c2d3"})
PC_B = fp.from_raw({"board": "9f2d1a70-11c3-4e57-a2b8-0d6f5e3c7a91", "system": "guid-b", "disk": "b5e2d3c4"})


def make(kind="subscription", *, days=365, grace=14, devices=(PC_A,), starts=T0, **more) -> L.License:
    expires = None if kind == "perpetual" else starts + timedelta(days=days, seconds=-1)
    base = dict(license_id="PH-20261201-ABC123", customer="صيدلية تجربة", kind=kind, issued_at=T0, starts_at=starts,
                expires_at=expires, grace_days=0 if kind == "perpetual" else grace, updates_until=None,
                devices=tuple(devices), features=())
    base.update(more)
    return L.License(**base)


class Base(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.trusted = [se.public_key_bytes(self.key)]

    def code(self, data, trusted=None):
        with self.assertRaises(L.LicenseError) as cm:
            L.verify(data, self.trusted if trusted is None else trusted)
        return cm.exception.code

    def forged(self, **changes) -> bytes:
        """A licence with edited terms, signed by the real key (what only the publisher could make)."""
        payload = make().payload()
        for k, v in changes.items():
            if v is ...:
                payload.pop(k)
            else:
                payload[k] = v
        return se.sign(se.canonical_json(payload), L.PURPOSE, self.key)


class TestVerify(Base):
    def test_round_trip_keeps_every_term(self):
        lic = make(features=("reports.extra",), devices=(PC_A, PC_B))
        got = L.verify(L.issue(lic, self.key), self.trusted)
        self.assertEqual(got.payload(), lic.payload())
        self.assertEqual(got.customer, "صيدلية تجربة")
        self.assertEqual(got.expires_at, datetime(2027, 11, 30, 23, 59, 59, tzinfo=UTC))

    def test_this_build_has_no_publisher_key_yet_so_it_refuses_every_licence(self):
        """Until the owner's key ceremony the list is empty; an empty list must fail closed."""
        self.assertEqual(keys.PUBLISHER_KEYS, ())
        self.assertEqual(self.code(L.issue(make(), self.key), trusted=keys.PUBLISHER_KEYS), "no_trusted_key")

    def test_built_in_keys_are_public_keys_in_the_documented_form(self):
        for k in keys.PUBLISHER_KEYS:                      # holds once keys are added, too
            self.assertRegex(k, r"^[0-9a-f]{64}$")
            se.public_key_bytes(k)
        self.assertEqual(len(set(keys.PUBLISHER_KEYS)), len(keys.PUBLISHER_KEYS))

    def test_a_licence_made_by_anyone_else_is_refused(self):
        pirate = Ed25519PrivateKey.generate()
        self.assertEqual(self.code(L.issue(make(), pirate)), "untrusted_key")

    def test_editing_any_term_breaks_the_signature(self):
        env = json.loads(L.issue(make(), self.key))
        payload = json.loads(base64.b64decode(env["payload"]))
        edits = {"expires_at": "2099-12-31T23:59:59Z", "kind": "perpetual", "grace_days": 90,
                 "customer": "صيدلية تانية", "devices": [PC_B.as_json()], "license_id": "PH-20261201-ZZZ999"}
        for field, value in edits.items():
            changed = {**payload, field: value}
            env["payload"] = base64.b64encode(se.canonical_json(changed)).decode()
            self.assertEqual(self.code(json.dumps(env).encode()), "bad_signature", field)

    def test_another_kind_of_signed_file_is_not_a_licence(self):
        release = se.sign(se.canonical_json(make().payload()), "release", self.key)
        self.assertEqual(self.code(release), "wrong_purpose")

    def test_garbage_and_oversize_are_refused(self):
        for data in (b"", b"{}", b"MZ\x90\x00", "ترخيص".encode(), b" " * (L.MAX_LICENSE_BYTES + 1)):
            self.assertEqual(self.code(data), "malformed")
        self.assertEqual(self.code(se.sign(b"[1,2]", L.PURPOSE, self.key)), "malformed")
        self.assertEqual(self.code(se.sign(b"not json", L.PURPOSE, self.key)), "malformed")


class TestStrictPayload(Base):
    def test_a_newer_format_or_another_product_is_named_as_such(self):
        self.assertEqual(self.code(self.forged(format=2)), "unsupported_format")
        self.assertEqual(self.code(self.forged(format="1")), "unsupported_format")
        self.assertEqual(self.code(self.forged(format=True)), "unsupported_format")
        self.assertEqual(self.code(self.forged(product="other-program")), "wrong_product")

    def test_missing_extra_or_wrongly_typed_fields_are_refused(self):
        bad = [dict(extra=1), dict(customer=...), dict(devices=...), dict(license_id="x"), dict(license_id=5),
               dict(customer=""), dict(customer="  "), dict(customer=" padded "), dict(customer="a\nb"),
               dict(customer="x" * 121), dict(kind="lifetime"), dict(grace_days=-1), dict(grace_days=91),
               dict(grace_days="14"), dict(grace_days=True), dict(issued_at="2026-12-01"),
               dict(starts_at="2026-13-01T00:00:00Z"), dict(expires_at="2027-02-30T00:00:00Z"),
               dict(expires_at="2027-11-30T23:59:59+02:00"), dict(expires_at=1830000000),
               dict(devices="all"), dict(devices=[PC_A.as_json()] * 11), dict(devices=[["board"]]),
               dict(devices=[{"board": "short"}]), dict(devices=[{"cpu": "0" * 32}]),
               dict(features="x"), dict(features=["a", "a"]), dict(features=["Bad Name"]), dict(features=[1])]
        for changes in bad:
            self.assertEqual(self.code(self.forged(**changes)), "malformed", changes)

    def test_terms_the_publisher_must_never_issue_are_refused_even_when_signed(self):
        t = L.format_time
        bad = [dict(kind="perpetual"),                                                # perpetual with an end date
               dict(kind="perpetual", expires_at=None),                              # ... or with a grace period
               dict(expires_at=None),                                                # subscription without an end
               dict(expires_at=t(T0)),                                               # ends when it starts
               dict(devices=[]),                                                     # subscription tied to no PC
               dict(kind="perpetual", expires_at=None, grace_days=0, devices=[]),    # perpetual tied to no PC
               dict(devices=[PC_A.as_json(), PC_A.as_json()]),                       # the same PC twice
               dict(devices=[{"system": "0" * 32}]),                                 # a PC that cannot be told apart
               dict(kind="trial"),                                                   # a year-long "trial"
               dict(updates_until=t(T0 + timedelta(days=30)))]                       # paid-updates date on a subscription
        for changes in bad:
            self.assertEqual(self.code(self.forged(**changes)), "invalid_terms", changes)

    def test_the_issuing_side_refuses_the_same_terms(self):
        for lic in (make("perpetual", expires_at=T0 + timedelta(days=1)), make(devices=()), make("trial", days=46),
                    make(expires_at=None)):
            with self.assertRaises(L.LicenseError):
                L.issue(lic, self.key)

    def test_the_allowed_shapes(self):
        ok = [make("perpetual"), make("perpetual", updates_until=T0 + timedelta(days=365)), make(grace=0),
              make("trial", days=45, grace=0, devices=()), make("trial", days=14, devices=(PC_A,)),
              make(devices=(fp.from_raw({"system": "guid-x", "disk": "0000aaaa"}),)),
              make(devices=tuple(fp.from_raw({"board": f"9f2d1a70-11c3-4e57-a2b8-0d6f5e3c7a{n:02d}"}) for n in range(10)))]
        for lic in ok:
            self.assertEqual(L.verify(L.issue(lic, self.key), self.trusted).payload(), lic.payload())

    def test_times_must_be_aware(self):
        with self.assertRaises(ValueError):
            L.format_time(datetime(2026, 12, 1))
        self.assertEqual(L.format_time(datetime(2026, 12, 1, 2, tzinfo=timezone(timedelta(hours=2)))),
                         "2026-12-01T00:00:00Z")


class TestDecision(unittest.TestCase):
    def state(self, lic, now, device=PC_A, **kw):
        return L.evaluate(lic, now=now, device=device, **kw)

    def test_subscription_through_its_life(self):
        lic = make(days=365, grace=14)                         # 1 Dec 2026 .. 30 Nov 2027 23:59:59, +14 days
        end = lic.expires_at
        s = self.state(lic, T0 + timedelta(days=100))
        self.assertEqual((s.state, s.usable, s.warn, s.days_left), ("valid", True, False, 265))
        s = self.state(lic, end - timedelta(days=10))
        self.assertEqual((s.state, s.usable, s.warn, s.days_left), ("valid", True, True, 10))
        s = self.state(lic, end)                               # the last second still counts
        self.assertEqual((s.state, s.days_left), ("valid", 0))
        s = self.state(lic, end + timedelta(seconds=1))
        self.assertEqual((s.state, s.usable, s.warn, s.grace_days_left), ("grace", True, True, 14))
        s = self.state(lic, end + timedelta(days=14))
        self.assertEqual((s.state, s.usable, s.grace_days_left), ("grace", True, 0))
        s = self.state(lic, end + timedelta(days=14, seconds=1))
        self.assertEqual((s.state, s.usable, s.warn), ("expired", False, True))
        self.assertLess(s.days_left, 0)

    def test_no_grace_means_the_end_date_is_the_end(self):
        lic = make(grace=0)
        self.assertEqual(self.state(lic, lic.expires_at + timedelta(seconds=1)).state, "expired")

    def test_perpetual_never_ends_whatever_the_clock_says(self):
        lic = make("perpetual")
        for now in (T0, T0 + timedelta(days=365 * 30), datetime(2099, 1, 1, tzinfo=UTC)):
            s = self.state(lic, now)
            self.assertEqual((s.state, s.usable, s.days_left, s.warn), ("valid", True, None, False))

    def test_another_pc_is_refused_and_a_listed_second_pc_is_accepted(self):
        self.assertEqual(self.state(make(), T0, device=PC_B).state, "wrong_device")
        self.assertFalse(self.state(make(), T0, device=PC_B).usable)
        self.assertEqual(self.state(make(devices=(PC_A, PC_B)), T0, device=PC_B).state, "valid")
        self.assertEqual(self.state(make("perpetual"), T0, device=fp.Device({})).state, "wrong_device")

    def test_a_copied_disk_on_another_board_is_another_pc(self):
        clone = fp.Device({**PC_A.parts, "board": PC_B.parts["board"]})
        self.assertEqual(self.state(make("perpetual"), T0, device=clone).state, "wrong_device")

    def test_an_untied_trial_runs_anywhere_until_its_end(self):
        lic = make("trial", days=30, grace=0, devices=())
        self.assertEqual(self.state(lic, T0 + timedelta(days=5), device=PC_B).state, "valid")
        self.assertEqual(self.state(lic, T0 + timedelta(days=31), device=PC_B).state, "expired")

    def test_revoked_wins_over_everything(self):
        s = self.state(make("perpetual"), T0, revoked={"PH-20261201-ABC123"})
        self.assertEqual((s.state, s.usable), ("revoked", False))
        self.assertEqual(self.state(make("perpetual"), T0, revoked={"PH-OTHER-000001"}).state, "valid")

    def test_not_yet_valid_with_a_day_of_tolerance(self):
        lic = make(starts=T0 + timedelta(days=10))
        self.assertEqual(self.state(lic, T0).state, "not_yet_valid")
        self.assertFalse(self.state(lic, T0).usable)
        self.assertEqual(self.state(lic, T0 + timedelta(days=9, hours=1)).state, "valid")    # time zones

    def test_turning_the_clock_back_does_not_revive_an_ended_subscription(self):
        lic = make(days=30, grace=7)
        seen = lic.expires_at + timedelta(days=20)             # the program already ran after the end
        s = self.state(lic, T0 + timedelta(days=3), high_water=seen)
        self.assertEqual((s.state, s.usable, s.clock_back), ("expired", False, True))
        s = self.state(lic, T0 + timedelta(days=3), high_water=lic.expires_at + timedelta(days=2))
        self.assertEqual((s.state, s.clock_back), ("grace", True))

    def test_a_dead_clock_battery_does_not_lock_a_paid_subscription(self):
        lic = make(days=365)
        year_2010 = datetime(2010, 1, 1, tzinfo=UTC)
        s = self.state(lic, year_2010, high_water=T0 + timedelta(days=50))
        self.assertEqual((s.state, s.usable, s.clock_back, s.warn, s.days_left), ("valid", True, True, True, 315))
        # with no memory at all the licence has simply not started: the screen says to check the date
        self.assertEqual(self.state(lic, year_2010).state, "not_yet_valid")

    def test_a_small_clock_correction_is_not_called_a_rollback(self):
        lic = make()
        now = T0 + timedelta(days=10)
        self.assertFalse(self.state(lic, now, high_water=now + timedelta(hours=25)).clock_back)
        self.assertTrue(self.state(lic, now, high_water=now + timedelta(hours=27)).clock_back)
        # but even inside the tolerance the later time is the one judged
        end = lic.expires_at
        self.assertEqual(self.state(lic, end - timedelta(hours=1), high_water=end + timedelta(hours=1)).state, "grace")

    def test_a_mark_in_the_past_changes_nothing(self):
        lic = make()
        now = T0 + timedelta(days=10)
        s = self.state(lic, now, high_water=T0)
        self.assertEqual((s.state, s.clock_back, s.days_left), ("valid", False, 355))

    def test_naive_times_are_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            self.state(make(), datetime(2026, 12, 5))
        with self.assertRaises(ValueError):
            self.state(make(), T0, high_water=datetime(2026, 12, 5))

    def test_every_state_has_an_arabic_line_without_latin_words(self):
        import re
        for state in L.STATES:
            s = L.status(state)
            self.assertEqual(s.state, state)
            self.assertTrue(s.message)
            self.assertIsNone(re.search(r"[A-Za-z]", s.message), s.message)        # OWNER_RULES §1
            self.assertEqual(s.usable, state in ("valid", "grace"))

    def test_paid_updates_window(self):
        lic = make("perpetual", updates_until=T0 + timedelta(days=365))
        self.assertTrue(lic.covers_release(T0 + timedelta(days=364)))
        self.assertFalse(lic.covers_release(T0 + timedelta(days=366)))
        self.assertTrue(make("perpetual").covers_release(datetime(2099, 1, 1, tzinfo=UTC)))


if __name__ == "__main__":
    unittest.main()
