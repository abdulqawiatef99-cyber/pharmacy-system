"""#324 audit M2, second half in my files: M2-01 a transfer marked "not received" funding a refund, M2-23 a retried
proof upload refused as a different request, M2-24 two uploads of one proof missing each other, M2-10 a manual
retry after a lost answer posting twice. Each test failed on develop first. Synthetic data only."""
import json
import shutil
import subprocess
import unittest
from dataclasses import replace
from unittest.mock import patch

from app.core import notify
from app.core.errors import Conflict
from app.sales import attachments as att
from tests.test_api import H
from tests.test_attachments import PNG
from tests.test_cashier_acceptance import Base
from tests.test_review_m2_sales_cash import JS, ApiBase


def transfer_sale(t, units=2):
    """A store sale paid by transfer (the channel set-up is not what these tests are about)."""
    sale = t.sell(units=units, cash=0, card=2000 * units)
    with t.db.transaction():
        t.db.execute("UPDATE sales SET paid_card=0, paid_transfer=? WHERE id=?", (2000 * units, sale["id"]))
    return sale


class TestM2_01TransferNotReceived(Base):
    def test_a_transfer_marked_not_received_funds_no_refund(self):
        self.cd.open(self.ctx, 10000)
        sale = transfer_sale(self)
        aid = att.attach(self.db, self.tmp, self.ctx, sale["id"], PNG)["attachments"][0]["id"]
        att.review(self.db, self.ctx, aid, False, "المبلغ لم يصل")
        line = sale["lines"][0]["id"]
        for how in ("cash", "card"):
            with self.subTest(how=how), self.assertRaises(Conflict) as e:
                self.s.return_items(self.ctx, sale["id"], {line: 1}, refund_method=how, reason="مرتجع عميل")
            self.assertEqual(e.exception.code, "cash_refund_exceeds_paid")
        self.assertEqual(self.db.one("SELECT count(*) n FROM sale_returns")["n"], 0)

    def test_only_confirmed_transfer_counts_for_an_approved_cash_exception(self):
        self.cd.open(self.ctx, 10000)
        approved = replace(self.ctx, approver_id="manager-fixture",
                           approver_permissions=frozenset({"sales.refund_other_method"}))
        waiting = transfer_sale(self)
        with self.assertRaises(Conflict) as error:
            self.s.return_items(approved, waiting["id"], {waiting["lines"][0]["id"]: 1}, refund_method="cash",
                                reason="مرتجع عميل")
        self.assertEqual(error.exception.details["max_cash"], 0)
        ok = transfer_sale(self)
        aid = att.attach(self.db, self.tmp, self.ctx, ok["id"], PNG)["attachments"][0]["id"]
        att.review(self.db, self.ctx, aid, True)
        self.s.return_items(approved, ok["id"], {ok["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع عميل")


class TestM2_23RetriedUpload(ApiBase):
    def test_the_same_file_and_fields_with_the_same_key_replay(self):
        sid = self.c.post("/api/sales", json={}, headers=H).json()["id"]
        send = lambda body, key: self.c.post(f"/api/sales/{sid}/attachments", files={"file": ("t.png", body, "image/png")},  # noqa: E731
                                             data={"note": "إنستاباي"}, headers={**H, "Idempotency-Key": key})
        first = send(PNG, "up-1")
        self.assertEqual(first.status_code, 200, first.text)
        again = send(PNG, "up-1")                                  # a new multipart boundary, same content
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(self.db.one("SELECT count(*) n FROM sale_attachments")["n"], 1)
        other = send(PNG + b"1", "up-1")                           # another file under the same key
        self.assertEqual((other.status_code, other.json()["error"]["code"]), (422, "idempotency_mismatch"))


class TestM2_24TwoUploadsOfOneProof(Base):
    def test_an_upload_between_the_check_and_the_save_is_still_caught(self):
        a, b = self.s.create(self.ctx), self.s.create(self.ctx)       # two drafts at two tills
        real, nested, started = att.new_id, [], []

        def meanwhile():
            if not started:                                           # once: the other till's whole upload
                started.append(1)
                nested.append(att.attach(self.db, self.tmp, self.ctx, b, PNG))
            return real()

        with patch.object(att, "new_id", meanwhile):
            r = att.attach(self.db, self.tmp, self.ctx, a, PNG)
        kinds = [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]]
        self.assertIn("transfer_duplicate", kinds)
        self.assertTrue(r["duplicate_of"] or nested[0]["duplicate_of"])

    def test_a_draft_without_a_number_is_still_named(self):
        a, b = self.s.create(self.ctx), self.s.create(self.ctx)
        att.attach(self.db, self.tmp, self.ctx, a, PNG)
        r = att.attach(self.db, self.tmp, self.ctx, b, PNG)
        self.assertTrue(r["duplicate_of"])                            # the screen warns, not "saved"
        self.assertEqual(r["duplicate_sale_id"], a)
        n = [x for x in notify.listing(self.db, self.ctx)["items"] if x["kind"] == "transfer_duplicate"]
        self.assertTrue(n and "None" not in n[0]["title"])


class TestM2_10RetryAfterALostAnswer(unittest.TestCase):
    def keys(self, calls):
        """Run api() for each (path, body, wait_ms, lose): lose = every attempt's answer is lost."""
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        start = JS.index("const API = { busy: 0, last: 0 };")
        end = JS.index("\n}\n", JS.index("async function api(")) + 3
        harness = """
let APPROVAL = null, ME = {}, n = 0, clock = 1000, lose = false; const location = { hash: "" }, sent = [];
Date.now = () => clock;
globalThis.setTimeout = (f) => f();
function newKey() { return "k" + (++n); }
function approvalHeaders() { return {}; }
async function askApproval() { return false; }
async function fetch(path, opts) { sent.push(opts.headers["Idempotency-Key"] ?? null);
  if (lose) throw new TypeError("network"); return { ok: true, status: 200, json: async () => ({}) }; }
"""
        code = harness + JS[start:end] + """
(async () => {
  const out = [];
  for (const [path, body, wait, l] of %s) {
    clock += wait; lose = l; const before = sent.length;
    try { await api(path, { method: "POST", body }); } catch (e) { /* the screen shows the error */ }
    out.push(sent[before]);
  }
  console.log(JSON.stringify(out));
})();
""" % json.dumps(calls)
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_same_form_sent_again_after_a_lost_answer_keeps_its_key(self):
        p, body = "/api/cash/movements", {"kind": "payout", "amount": 3000, "note": "مواصلات"}
        a, b, c = self.keys([[p, body, 0, True], [p, body, 60000, False], [p, body, 60000, False]])
        self.assertEqual(a, b)                  # a minute later, still the uncertain one: replayed or refused
        self.assertNotEqual(b, c)               # its outcome is known now: a later press is a new payout

    def test_another_amount_is_another_operation(self):
        p = "/api/cash/movements"
        a, b = self.keys([[p, {"kind": "payout", "amount": 3000, "note": "x"}, 0, True],
                          [p, {"kind": "payout", "amount": 4000, "note": "x"}, 60000, False]])
        self.assertNotEqual(a, b)
