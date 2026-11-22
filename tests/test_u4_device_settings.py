"""U4-0: the device / printing / screen settings come back from an older copy of the data (synthetic data only).

The clean start renamed the data folder and every org_settings value went back to its default (the label printed
across two labels again, #100). The owner picks the old copy, sees what would change, confirms; a future clean
start carries them by itself. Never users, passwords, PINs or business data."""
import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.core import accounts, backup, device_settings as dvs
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.db import Database
from app.purchasing.service import Purchasing
from app.sales import channels
from tests.helpers import DBTestCase, OWNER_PW

ROOT = Path(__file__).resolve().parent.parent
H = {"X-Requested-With": "pharm"}
OLD_RECEIPT = {"receipt_target": "win:POS-80C", "label_lang": "driver", "label_target": "win:Xprinter XP-365B",
               "label_rows": 2, "label_size": "38x25", "drawer_timing": "before", "title": "صيدلية تجربة"}
OLD_UI = {"nav_main": ["pos", "sales", "gone-screen"], "pos_keys": {"done": "F10", "pay": "F9", "bogus": "F1"}}


def make_old(home: Path, folder: str = "data-trial-20261121-180000") -> Path:
    """An older data folder as the clean start leaves it: one pharmacy with its printing / screen settings."""
    d = home / folder
    d.mkdir(parents=True)
    db = Database(d / "pharmacy.db")
    db.migrate()
    org = accounts.create_organization(db, name="صيدلية قديمة", owner_username="oldowner",
                                       owner_full_name="Old", owner_password="Old-Pass-2026x")
    channels.ensure_defaults(db)
    oid = org["org_id"]
    with db.transaction():
        for k, v in (("receipt", json.dumps(OLD_RECEIPT, ensure_ascii=False)), ("ui", json.dumps(OLD_UI)),
                     ("receipt_logo", "iVBORw0KGgoAAAA="), ("shift_handover_pin", "secret-hash"),
                     ("manager_expense_limit", "500000")):
            db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?)", (oid, k, v))
        db.execute("UPDATE payment_channels SET name='PayMob' WHERE org_id=? AND kind='card'", (oid,))
        db.execute("INSERT INTO payment_channels (id, org_id, kind, name, sort) VALUES ('ch-old', ?, 'transfer', 'اتصالات كاش', 9)",
                   (oid,))
    db.close()
    return d / "pharmacy.db"


class DeviceSettingsCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.data = self.home / "data"
        self.data.mkdir(parents=True)
        self.bk = self.home / "backups"
        channels.ensure_defaults(self.db)

    def setting(self, key):
        r = self.db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (self.ctx.org_id, key))
        return r["value"] if r else None

    def test_lists_old_folders_and_backups_not_the_current_one(self):
        old = make_old(self.home)
        (self.data / "pharmacy.db").write_bytes(b"")              # the live one is never offered
        odb = Database(old)
        backup.create(odb, self.tmp / "up", self.bk / "fresh-start-x")
        odb.close()
        rows = dvs.sources(self.data, self.bk)
        self.assertEqual(sorted(r["kind"] for r in rows), ["backup", "folder"])
        self.assertTrue(all(Path(r["path"]).resolve() != (self.data / "pharmacy.db").resolve() for r in rows))
        self.assertEqual(len({r["id"] for r in rows}), 2)

    def test_preview_then_restore_only_device_settings(self):
        make_old(self.home)
        src = dvs.sources(self.data, self.bk)[0]
        p = dvs.preview(self.db, self.ctx, src)
        g = {x["key"]: x for x in p["groups"]}
        self.assertEqual(p["pharmacy"], "صيدلية قديمة")
        self.assertTrue(g["printing"]["default"] and g["logo"]["default"] and g["cards"]["default"] and g["screen"]["default"])
        self.assertFalse(g["cash"]["default"])                    # money settings: only when ticked
        self.assertIn("طابعة الملصقات: win:Xprinter XP-365B", g["printing"]["lines"])
        self.assertIn("عدد الملصقات في صفحة ويندوز: 2", g["printing"]["lines"])
        users_before = self.db.one("SELECT count(*) n FROM users")["n"]
        res = dvs.restore(self.db, self.ctx, src, ["printing", "logo", "cards", "screen"])
        self.assertEqual(json.loads(self.setting("receipt"))["label_rows"], 2)
        self.assertEqual(self.setting("receipt_logo"), "iVBORw0KGgoAAAA=")
        ui = json.loads(self.setting("ui"))
        self.assertEqual(ui["nav_main"], ["pos", "sales"])                 # unknown screen dropped
        self.assertEqual(ui["pos_keys"], {"done": "F10", "pay": "F9"})     # unknown action dropped
        names = {(r["name"], r["is_active"]) for r in self.db.all(
            "SELECT name, is_active FROM payment_channels WHERE org_id=?", (self.ctx.org_id,))}
        self.assertIn(("PayMob", 1), names)
        self.assertIn(("اتصالات كاش", 1), names)
        self.assertIn(("جهاز البطاقات", 0), names)        # the default the old copy renamed is stopped
        # never: users, the handover PIN, the expense limit (not ticked)
        self.assertIsNone(self.setting("shift_handover_pin"))
        self.assertIsNone(self.setting("manager_expense_limit"))
        self.assertEqual(self.db.one("SELECT count(*) n FROM users")["n"], users_before)
        a = self.db.one("SELECT * FROM audit_log WHERE action='settings.restore_devices'")
        self.assertIsNotNone(a)
        self.assertEqual(a["user_id"], self.ctx.user_id)
        self.assertIn("receipt", res["changed"])

    def test_cash_group_when_ticked_never_the_pin(self):
        make_old(self.home)
        src = dvs.sources(self.data, self.bk)[0]
        dvs.restore(self.db, self.ctx, src, ["cash"])
        self.assertEqual(self.setting("manager_expense_limit"), "500000")
        self.assertIsNone(self.setting("shift_handover_pin"))
        self.assertIsNone(self.setting("receipt"))

    def test_a_used_default_channel_is_not_stopped(self):
        make_old(self.home)
        cid = self.db.one("SELECT id FROM payment_channels WHERE org_id=? AND kind='card'", (self.ctx.org_id,))["id"]
        self.db.conn.execute("PRAGMA foreign_keys=OFF")
        self.db.conn.execute("INSERT INTO sale_payments (id, org_id, sale_id, method, channel_id, amount) VALUES"
                             " ('sp1', ?, 'nosale', 'card', ?, 100)", (self.ctx.org_id, cid))
        self.db.conn.commit()
        self.db.conn.execute("PRAGMA foreign_keys=ON")
        dvs.restore(self.db, self.ctx, dvs.sources(self.data, self.bk)[0], ["cards"])
        self.assertEqual(self.db.one("SELECT is_active FROM payment_channels WHERE id=?", (cid,))["is_active"], 1)

    def test_from_a_backup_file(self):
        old = make_old(self.home, "elsewhere")
        odb = Database(old)
        backup.create(odb, self.tmp / "up", self.bk)
        odb.close()
        shutil.rmtree(self.home / "elsewhere")
        src = dvs.sources(self.data, self.bk)
        self.assertEqual([r["kind"] for r in src], ["backup"])
        dvs.restore(self.db, self.ctx, src[0], ["printing"])
        self.assertEqual(json.loads(self.setting("receipt"))["label_target"], "win:Xprinter XP-365B")

    def test_refusals(self):
        make_old(self.home)
        src = dvs.sources(self.data, self.bk)[0]
        for bad in ([], None, ["users"], "printing"):
            with self.assertRaises(ValidationFailed):
                dvs.restore(self.db, self.ctx, src, bad)
        with self.assertRaises(NotFound):
            dvs._find("0" * 16, self.data, self.bk, ())
        accounts.add_user(self.db, self.ctx, username="cash1", full_name="C", password="Cashier-Pass-2026x",
                          role_code="assistant")
        cctx = self.login("cash1", "Cashier-Pass-2026x")
        with self.assertRaises(Forbidden):
            dvs.preview(self.db, cctx, src)
        junk = self.home / "data-junk"
        junk.mkdir()
        (junk / "pharmacy.db").write_bytes(b"not a database at all")
        js = [r for r in dvs.sources(self.data, self.bk) if r["name"] == "data-junk"][0]
        with self.assertRaises(ValidationFailed):
            dvs.preview(self.db, self.ctx, js)
        self.assertIsNone(self.setting("receipt"))

    def test_bad_values_from_an_old_copy_are_dropped_not_stored(self):
        """Review P1: the restored receipt goes through the settings screen's own checks, field by field."""
        old = make_old(self.home)
        odb = Database(old)
        oid = odb.one("SELECT id FROM organizations")["id"]
        bad = {**OLD_RECEIPT, "label_target": "C:\\Windows\\x.txt", "label_shift": 99, "copies": 7, "label_rows": 3,
               "unknown_key": 1}
        with odb.transaction():
            odb.execute("UPDATE org_settings SET value=? WHERE org_id=? AND key='receipt'", (json.dumps(bad), oid))
            odb.execute("UPDATE org_settings SET value='not a png' WHERE org_id=? AND key='receipt_logo'", (oid,))
        odb.close()
        dvs._cache.clear()
        dvs.restore(self.db, self.ctx, dvs.sources(self.data, self.bk)[0], ["printing", "logo"])
        got = json.loads(self.setting("receipt"))
        for k in ("label_target", "label_shift", "copies", "unknown_key"):
            self.assertNotIn(k, got)
        self.assertEqual(got["label_rows"], 3)
        self.assertEqual(got["receipt_target"], "win:POS-80C")
        self.assertIsNone(self.setting("receipt_logo"))

    def test_preview_sees_an_on_off_difference_in_cards(self):
        old = make_old(self.home)
        dvs.restore(self.db, self.ctx, dvs.sources(self.data, self.bk)[0], ["cards"])
        odb = Database(old)
        with odb.transaction():
            odb.execute("UPDATE payment_channels SET is_active=0 WHERE name='PayMob'")
        odb.close()
        dvs._cache.clear()
        g = {x["key"]: x for x in dvs.preview(self.db, self.ctx, dvs.sources(self.data, self.bk)[0])["groups"]}
        self.assertFalse(g["cards"]["same"])

    def test_a_copy_removed_after_the_listing_is_not_found(self):
        old = make_old(self.home)
        src = dvs.sources(self.data, self.bk)[0]
        shutil.rmtree(old.parent)
        dvs._cache.clear()
        with self.assertRaises(NotFound):
            dvs.preview(self.db, self.ctx, src)

    def test_clean_start_carry_round_trip(self):
        old = Database(make_old(self.home))
        out = self.data / dvs.CARRY_FILE
        info = dvs.carry_out(old, out)
        old.close()
        self.assertEqual(info["keys"], ["receipt", "receipt_logo", "ui"])          # no PIN, no money settings
        self.assertNotIn("secret-hash", out.read_text(encoding="utf-8"))
        r = dvs.carry_in(self.db, self.data, self.ctx.org_id, self.ctx.user_id)
        self.assertTrue(r["applied"])
        self.assertFalse(out.exists())
        self.assertTrue(out.with_suffix(".applied").exists())
        self.assertEqual(json.loads(self.setting("receipt"))["label_rows"], 2)
        self.assertIsNone(dvs.carry_in(self.db, self.data, self.ctx.org_id, self.ctx.user_id))   # once only

    def test_a_bad_carry_file_is_set_aside(self):
        f = self.data / dvs.CARRY_FILE
        f.write_text("{broken", encoding="utf-8")
        r = dvs.carry_in(self.db, self.data, self.ctx.org_id, self.ctx.user_id)
        self.assertFalse(r["applied"])
        self.assertTrue(f.with_suffix(".bad").exists())
        self.assertIsNone(self.setting("receipt"))


class DeviceSettingsAPI(DBTestCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.data = self.home / "data"
        self.data.mkdir(parents=True)
        self.s = replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None, data_dir=self.data,
                         backup_dir=self.home / "backups")

    def test_endpoints(self):
        make_old(self.home)
        c = TestClient(create_app(self.db, self.s, Purchasing(self.db, self.s)))
        self.assertEqual(c.get("/api/settings/device-restore/sources").status_code, 401)
        c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H)
        src = c.get("/api/settings/device-restore/sources").json()["sources"]
        self.assertEqual(len(src), 1)
        self.assertNotIn("path", src[0])                       # the screen never sees or sends a path
        self.assertEqual(c.post("/api/settings/device-restore/preview", json={"source": "../../etc"}, headers=H).status_code, 404)
        p = c.post("/api/settings/device-restore/preview", json={"source": src[0]["id"]}, headers=H)
        self.assertEqual(p.status_code, 200, p.text)
        r = c.post("/api/settings/device-restore/apply", json={"source": src[0]["id"], "groups": ["printing"]},
                   headers={**H, "Idempotency-Key": "k1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get("/api/settings/receipt").json()["label_rows"], 2)

    def test_setup_applies_the_carry_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="pharm-setup-"))
        try:
            data = tmp / "home" / "data"
            data.mkdir(parents=True)
            old = Database(make_old(tmp / "home"))
            dvs.carry_out(old, data / dvs.CARRY_FILE)
            old.close()
            fresh = Database(data / "pharmacy.db")
            fresh.migrate()
            s = replace(self.s, data_dir=data, db_path=data / "pharmacy.db", backup_dir=tmp / "home" / "backups")
            c = TestClient(create_app(fresh, s, Purchasing(fresh, s)))
            r = c.post("/api/setup", json={"pharmacy": "صيدلية جديدة", "username": "owner", "password": "New-Owner-2026"},
                       headers=H)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertTrue(r.json()["carried"]["applied"])
            self.assertEqual(c.get("/api/settings/receipt").json()["label_target"], "win:Xprinter XP-365B")
            self.assertEqual(fresh.one("SELECT count(*) n FROM users")["n"], 1)        # the old owner is not copied
            fresh.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Screens(DBTestCase):
    def test_ui_and_script(self):
        js = (ROOT / "app" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("رجّع إعدادات الأجهزة والطباعة من نسخة قديمة", js)
        self.assertIn('[/^#\\/setup-printers$/, viewSetupPrinters]', js)
        self.assertIn('location.hash = "#/setup-printers"', js)
        self.assertIn("اطبع ملصق تجربة", js)
        # Codex P2 on #430: the restore uses the previewed copy, and another choice clears the preview
        self.assertIn('body: { source: p.source.id, groups }', js)
        self.assertIn('$("#dvSrc").addEventListener("change", () => { $("#dvPrev").innerHTML = ""; });', js)
        bat = (ROOT / "scripts" / "windows" / "launch-fresh-start.bat").read_text(encoding="ascii")
        exp = bat.index("export-device-settings")
        self.assertLess(exp, bat.index('ren "%DATA%" "%TRIAL%"'))            # read before the rename
        self.assertGreater(bat.index('"%DATA%\\device_settings_carry.json"'), bat.index('ren "%DATA%" "%TRIAL%"'))
