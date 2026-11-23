"""Cashier checkout latency and exactly-once posting at synthetic import scale."""
import asyncio
import sqlite3
import threading
import time
from contextlib import closing
from unittest.mock import patch

from app.catalog import matching
from app.dataimport import estock_archive
from app.db import new_id
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import H
from tests.test_owner_plan_priority_1_2 import PlanCase


class TestCheckoutAtScale(PlanCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(matching.forget, self.ctx.org_id)
        with self.db.transaction():
            self.db.conn.executemany(
                "INSERT INTO items (id, org_id, code, name_en, name_ar, name_norm, name_ar_norm,"
                " pack_size, public_price, status, dedupe_key) VALUES (?,?,?,?,?,?,?,1,1000,'active',?)",
                ((i, self.ctx.org_id, f"S{n}", f"Panacatalog Brand {n} 10mg tab",
                  f"صنف صناعي {n}", f"panacatalog brand {n} 10mg tab", f"صنف صناعي {n}", i)
                 for n in range(29998) for i in (new_id(),)))
        with closing(sqlite3.connect(self.tmp / estock_archive.NAME)) as archive, archive:
            archive.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            archive.execute("INSERT INTO meta VALUES ('org_id', ?)", (self.ctx.org_id,))
            archive.execute("CREATE TABLE e_sales_details (sales_id TEXT, product_id TEXT, amount TEXT)")
            archive.execute("INSERT INTO e_sales_details WITH RECURSIVE n(x) AS (VALUES(1)"
                            " UNION ALL SELECT x+1 FROM n WHERE x<500000) SELECT x, x % 30000, '1' FROM n")
            archive.execute("CREATE INDEX e_sales_details_product ON e_sales_details(product_id)")
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})

    def test_unwarmed_checkout_under_half_a_second_and_replays(self):
        sid = self.draft()
        self.line(sid, self.pan, packs=1)
        sale = self.line(sid, self.box, packs=1)
        check, complete = f"/api/sales/{sid}/check", f"/api/sales/{sid}/complete"
        payload = {"paid_cash": sale["cash_total"], "version": sale["version"], "request_id": "synthetic-checkout"}
        start = time.perf_counter()
        self.call("POST", check, json={}, headers={"Idempotency-Key": "synthetic-check"})
        checked = time.perf_counter()
        done = self.call("POST", complete, json=payload, headers={"Idempotency-Key": "synthetic-complete"})
        finish = time.perf_counter()
        self.assertLess(finish - start, 0.5, f"check {checked-start:.3f}s, complete {finish-checked:.3f}s")
        for key in ("synthetic-complete", "synthetic-retry"):
            retry = self.call("POST", complete, json=payload, headers={"Idempotency-Key": key})
            self.assertEqual(retry["sale_no"], done["sale_no"])
        self.assertEqual(self.qty(self.pan), 18)
        self.assertEqual(self.qty(self.box), 27)
        self.assertEqual(self.drawer(), 10000 + sale["cash_total"])

    def test_first_fuzzy_query_after_index_warmup(self):
        matching.prewarm(self.db)
        start = time.perf_counter()
        response = self.call("GET", "/api/items?q=Panacatalg&stock=first&meta=1")
        elapsed = time.perf_counter() - start
        self.assertEqual(response["hint"]["kind"], "fuzzy")
        self.assertTrue(response["items"])
        self.assertLess(elapsed, 1.5, f"GET items: {elapsed:.3f}s")


class TestOwnerUpdateNetwork(PlanCase):
    def test_offline_badge_never_calls_upstream(self):
        with patch.dict("os.environ", {"PHARM_TEST_OFFLINE_UPDATE": "1"}), \
                patch("app.core.updater.urllib.request.urlopen", side_effect=AssertionError("network in owner badge")) as urlopen:
            status = self.call("GET", "/api/system/update")
        urlopen.assert_not_called()
        self.assertFalse(status["available"])


class TestCheckoutRequests(PlanCase):
    def test_item_write_waiting_for_sqlite_does_not_block_other_requests(self):
        import httpx

        entered = threading.Event()
        original = self.db.transaction

        def observed_transaction():
            entered.set()
            return original()

        async def scenario():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://testserver", cookies=self.c.cookies,
            ) as client:
                # A slow writer (e.g. import/maintenance) holds the same lock used by idempotency.
                held, release = threading.Event(), threading.Event()

                def release_later():
                    with self.db._write_lock:
                        held.set()
                        release.wait(2)

                holder = threading.Thread(target=release_later)
                holder.start()
                try:
                    self.assertTrue(held.wait(1))
                    with patch.object(self.db, "transaction", side_effect=observed_transaction):
                        pending = asyncio.create_task(client.post(
                            "/api/items", json={"item": {"name_en": "Synthetic Lock Proof", "public_price": 200}},
                            headers={**H, "Idempotency-Key": "synthetic-lock"}))
                        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                        polls = await asyncio.gather(*(client.get(url) for url in
                            ("/api/backup/health", "/api/stock/alert", "/api/notifications")))
                        self.assertEqual([r.status_code for r in polls], [200, 200, 200])
                        self.assertFalse(pending.done(), "the event loop waited for the SQLite writer")
                finally:
                    release.set()
                    holder.join()
                    if "pending" in locals():
                        self.assertEqual((await pending).status_code, 201)

        asyncio.run(scenario())


    def test_devices_run_if_replay_record_fails_after_posting(self):
        from app.printing import service as printing
        from app.sales.cash import CashDrawer

        CashDrawer(self.db).open(self.ctx, 10000)
        sid = self.draft()
        sale = self.line(sid, self.pan, packs=1)
        path = f"/api/sales/{sid}/complete"
        data = {"paid_cash": sale["cash_total"], "request_id": "synthetic-device", "defer_devices": True}
        real_execute = self.db.execute

        def fail_response(sql, params=()):
            if sql.startswith("UPDATE idempotency_keys SET status="):
                raise OSError("synthetic replay write failure")
            return real_execute(sql, params)

        devices = {"receipt": "none", "drawer": "sent", "message": None}
        with patch.object(printing, "checkout_devices", return_value=devices) as send:
            with patch.object(self.db, "execute", side_effect=fail_response):
                self.call("POST", path, 500, json=data, headers={"Idempotency-Key": "synthetic-first"})
            replay = self.call("POST", path, json=data, headers={"Idempotency-Key": "synthetic-next"})
            self.assertTrue(replay["replayed"])
            self.assertTrue(replay["devices_pending"])
        send.assert_called_once()
        status = self.call("GET", f"/api/sales/{sid}/devices")
        self.assertEqual(status["devices"], devices)
        self.assertEqual(self.qty(self.pan), 18)
        self.assertEqual(self.drawer(), 10000 + sale["cash_total"])


@needs_browser
class TestCashierCheckout(PlanCase):
    def test_one_f9_waits_for_scan_and_frees_till_before_receipt(self):
        from app.printing import escpos, settings
        from app.sales.cash import CashDrawer

        CashDrawer(self.db).open(self.ctx, 10000)
        settings.save(self.db, self.ctx, {"receipt_target": "tcp:127.0.0.1:9100", "auto_receipt": True})
        kicked, print_started, finish_print = threading.Event(), threading.Event(), threading.Event()
        sends = []

        def send(_target, data):
            sends.append(data)
            if data == escpos.INIT + escpos.DRAWER_KICK:
                kicked.set()
            else:
                print_started.set()
                finish_print.wait(12)

        flag = self.tmp / "cashier-free"
        def release_receipt():
            if print_started.wait(12):
                for _ in range(120):
                    if flag.exists():
                        break
                    time.sleep(0.05)
            finish_print.set()

        script = r"""
const { chromium } = require('playwright');
const { writeFileSync } = require('fs');
(async () => {
  const [base, user, pw, flag] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  const checks = [], completes = [];
  p.on('request', r => {
    if (r.url().endsWith('/check')) checks.push(r.url());
    if (r.url().endsWith('/complete')) completes.push(r.url());
  });
  let release;
  const scan = new Promise(resolve => { release = resolve; });
  await p.route('**/api/items?**', async r => { await scan; await r.continue(); });
  const waiting = p.waitForRequest('**/api/items?**');
  await p.fill('#posQ', '6221025012345');
  await p.press('#posQ', 'Enter');
  await waiting;
  await p.keyboard.press('F9');
  const frozen = await p.locator('#posQ').evaluate(el => !!el.closest('[inert]'));
  await p.keyboard.down('F9'); await p.keyboard.down('F9'); await p.keyboard.up('F9');
  release();
  await p.waitForSelector('#posLast', { timeout: 3000 });
  writeFileSync(flag, 'ready');
  await p.waitForFunction(() => document.body.textContent.includes('أُرسل الإيصال للطابعة'));
  console.log(JSON.stringify({checks: checks.length, completes: completes.length, frozen}));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""
        with patch.object(escpos, "send", side_effect=send):
            out = run_node(self, script, "owner", OWNER_PW, str(flag), behind=release_receipt)
        self.assertTrue(kicked.is_set())
        self.assertEqual(len(sends), 2)
        self.assertNotIn(escpos.DRAWER_KICK, sends[1])
        self.assertEqual(out, {"checks": 1, "completes": 1, "frozen": True})
        self.assertEqual(self.qty(self.pan), 18)
        self.assertEqual(self.drawer(), 14000)

    def test_failed_pending_scan_keeps_the_draft_open(self):
        from app.sales.cash import CashDrawer

        CashDrawer(self.db).open(self.ctx, 10000)
        sid = self.draft()
        self.line(sid, self.box, packs=1)
        script = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posDone:enabled');
  let release, posted = 0;
  const scan = new Promise(resolve => { release = resolve; });
  await p.route('**/api/items?**', async r => { await scan; await r.continue(); });
  await p.route('**/api/sales/*/lines', r => r.fulfill({
    status: 409, json: { error: { code: 'insufficient_stock', message: 'Synthetic stock changed' } }
  }));
  p.on('request', r => { if (r.url().endsWith('/complete')) posted++; });
  const lookup = p.waitForRequest('**/api/items?**');
  await p.fill('#posQ', '6221025012345');
  await p.press('#posQ', 'Enter');
  await lookup;
  await p.keyboard.press('F9');
  release();
  await p.waitForFunction(() => document.querySelector('#toast')?.textContent.includes('Synthetic stock changed')
    && !document.querySelector('#posDone').closest('[inert]'));
  console.log(JSON.stringify({posted}));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""
        self.assertEqual(run_node(self, script, "owner", OWNER_PW), {"posted": 0})
        self.assertEqual(self.call("GET", f"/api/sales/{sid}")["status"], "draft")

    def test_checkout_waits_only_for_scans_on_its_draft(self):
        from app.sales.cash import CashDrawer

        CashDrawer(self.db).open(self.ctx, 10000)
        other, current = self.draft(), self.draft()
        self.line(other, self.box, packs=1)
        self.line(current, self.box, packs=1)
        script = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, other, current] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector(`[data-tab="${current}"].on`);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await p.route('**/api/items?**', async r => { await held; await r.abort(); });
  const lookup = p.waitForRequest('**/api/items?**');
  await p.fill('#posQ', '6221025012345');
  await p.press('#posQ', 'Enter');
  await lookup;
  await p.click(`[data-tab="${other}"]`);
  await p.waitForSelector(`[data-tab="${other}"].on`);
  const finished = p.waitForResponse(`**/api/sales/${other}/complete`, { timeout: 3000 });
  await p.keyboard.press('F9');
  const response = await finished;
  release();
  console.log(JSON.stringify({status: response.status()}));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""
        out = run_node(self, script, "owner", OWNER_PW, other, current)
        self.assertEqual(out, {"status": 200})
        self.assertEqual(self.call("GET", f"/api/sales/{current}")["status"], "draft")
