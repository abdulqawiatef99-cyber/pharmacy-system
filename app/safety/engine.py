"""Medication safety engine — infrastructure only.

What it does now (no clinical knowledge needed):
  * duplicate_ingredient  same active ingredient in two different items of one
                          basket, or two strengths of it — uses ONLY verified
                          item→ingredient mappings.
  * lookalike             the chosen item has catalog neighbours with a near-
                          identical name but different strength/form/modifier.
  * insufficient_data     any item without a verified ingredient mapping. The
                          engine never reports "safe" for it.
What it can do once approved, sourced rules exist (none are shipped):
  * interaction           params {"ingredients": [id_a, id_b]}
  * max_qty               params {"item_id" | "ingredient_id", "max_base_units"}
  * split_policy          params {"item_id", "allow_partial_pack": bool,
                                  "allow_tablet_split": bool}

Governance: rules are created as drafts with a cited knowledge source, become
active only after approval (by a different authorized user when one exists),
are immutable once approved (change = new version that supersedes), and can be
disabled instantly with a reason. Ordinary users cannot disable rules.
Every finding shown and every decision is written to safety_events.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import date

from ..catalog import matching
from ..catalog.normalize import normalize_text, parse_name
from ..core import accounts, audit
from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

SEVERITY_ORDER = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "info": 0}
SEVERITY_AR = {"critical": "حرجة", "high": "مرتفعة", "moderate": "متوسطة", "low": "منخفضة",
               "info": "معلومة", "insufficient": "لا توجد بيانات كافية"}
RULE_TYPES = ("duplicate_ingredient", "interaction", "max_qty", "split_policy", "lookalike")
# Notices that state a constant fact about an item (no decision asked): written to safety_events once per
# item and month, not on every sale. Kept in memory: after a restart each is written once more at most.
ONCE_A_MONTH = ("insufficient_data",)
_logged: set[tuple] = set()
_logged_lock = threading.Lock()


def _today() -> date:
    return date.today()


def forget_logged_notices() -> None:
    with _logged_lock:
        _logged.clear()


@dataclass
class Finding:
    code: str                      # duplicate_ingredient | interaction | ... | insufficient_data
    severity: str                  # critical | high | moderate | low | info | insufficient
    message: str
    item_ids: list[str] = field(default_factory=list)
    rule_id: str | None = None
    rule_version: int | None = None
    source: dict | None = None     # {name, version, ref}
    basis: str = "data"            # data (verified mapping/rule) | heuristic (name similarity)
    requires: str | None = None    # block | override | acknowledge | none

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


class SafetyEngine:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------ knowledge
    def add_source(self, ctx: Context, *, name: str, version: str, license: str,
                   publisher: str | None = None, url: str | None = None,
                   published_at: str | None = None, notes: str | None = None) -> str:
        ctx.require("safety.manage")
        if not (name and version and license):
            raise ValidationFailed("اسم المرجع وإصداره ونوع الترخيص مطلوبين")
        sid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO knowledge_sources (id, org_id, name, version, publisher,"
                            " license, url, published_at, notes, added_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (sid, ctx.org_id, name.strip(), version.strip(), publisher, license, url,
                             published_at, notes, ctx.user_id))
            audit.record(self.db, ctx, "safety.source_add", "knowledge_source", sid,
                         after={"name": name, "version": version})
        return sid

    def _ingredient(self, ctx: Context, name_en: str, name_ar: str | None = None) -> str:
        norm = normalize_text(name_en)
        if not norm:
            raise ValidationFailed("اسم المادة الفعالة مطلوب")
        r = self.db.one("SELECT id FROM active_ingredients WHERE name_norm=? AND"
                        " (org_id IS NULL OR org_id=?) ORDER BY org_id IS NULL LIMIT 1",
                        (norm, ctx.org_id))
        if r:
            return r["id"]
        iid = new_id()
        self.db.execute("INSERT INTO active_ingredients (id, org_id, name_en, name_ar, name_norm)"
                        " VALUES (?,?,?,?,?)", (iid, ctx.org_id, name_en.strip(), name_ar, norm))
        return iid

    def set_item_ingredients(self, ctx: Context, item_id: str,
                             ingredients: list[tuple[str, str | None]],
                             source_id: str | None = None) -> None:
        """Enter the composition of an item. Stored as 'unverified' until a
        pharmacist with safety.approve verifies it."""
        ctx.require("items.write")
        if not ingredients:
            raise ValidationFailed("أدخل مادة فعالة واحدة على الأقل")
        with self.db.transaction():
            if not self.db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
                raise NotFound("الصنف غير موجود")
            if not ctx.can("safety.approve") and self.db.one(
                    "SELECT 1 FROM item_ingredients WHERE org_id=? AND item_id=? AND status='verified'",
                    (ctx.org_id, item_id)):
                # replacing a verified composition switches the item's duplicate and interaction checks off
                # until it is verified again: only who may verify may do that (#324 X8)
                raise Forbidden("تركيبة هذا الصنف موثّقة؛ تغييرها يحتاج من له صلاحية اعتماد السلامة الدوائية",
                                code="verified_composition")
            self.db.execute("DELETE FROM item_ingredients WHERE org_id=? AND item_id=?",
                            (ctx.org_id, item_id))
            for name, strength in ingredients:
                ing = self._ingredient(ctx, name)
                self.db.execute("INSERT OR REPLACE INTO item_ingredients (org_id, item_id,"
                                " ingredient_id, strength, status, source_id, entered_by)"
                                " VALUES (?,?,?,?, 'unverified', ?, ?)",
                                (ctx.org_id, item_id, ing, strength, source_id, ctx.user_id))
            audit.record(self.db, ctx, "safety.ingredients_set", "item", item_id,
                         after={"ingredients": ingredients, "source_id": source_id})

    def verify_item_ingredients(self, ctx: Context, item_id: str) -> None:
        ctx.require("safety.approve")
        with self.db.transaction():
            n = self.db.execute("UPDATE item_ingredients SET status='verified', verified_by=?,"
                                " verified_at=? WHERE org_id=? AND item_id=?",
                                (ctx.user_id, _now(), ctx.org_id, item_id)).rowcount
            if not n:
                raise NotFound("لا توجد مواد فعالة مسجلة لهذا الصنف")
            audit.record(self.db, ctx, "safety.ingredients_verify", "item", item_id)

    # ------------------------------------------------------------ rules
    def create_rule(self, ctx: Context, *, rule_type: str, severity: str, title: str,
                    explanation: str, params: dict, source_id: str | None,
                    source_ref: str | None = None, supersedes: str | None = None) -> str:
        ctx.require("safety.manage")
        if rule_type not in RULE_TYPES:
            raise ValidationFailed("نوع قاعدة غير معروف")
        if severity not in ("critical", "high", "moderate", "low"):
            raise ValidationFailed("مستوى خطورة غير صحيح")
        if rule_type in ("interaction", "max_qty", "split_policy") and not source_id:
            raise ValidationFailed("القواعد السريرية يجب أن يكون لها مرجع موثق", code="source_required")
        if source_id and not self.db.one("SELECT 1 FROM knowledge_sources WHERE id=? AND"
                                         " (org_id IS NULL OR org_id=?)", (source_id, ctx.org_id)):
            raise NotFound("المرجع غير موجود")
        self._validate_params(ctx, rule_type, params)
        version = 1
        if supersedes:
            old = self.db.one("SELECT * FROM safety_rules WHERE id=? AND org_id=?",
                              (supersedes, ctx.org_id))
            if not old:
                raise NotFound("القاعدة الأصلية غير موجودة")
            version = old["version"] + 1
        rid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO safety_rules (id, org_id, rule_type, params, severity, title,"
                            " explanation, source_id, source_ref, version, supersedes, created_by)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (rid, ctx.org_id, rule_type, json.dumps(params), severity, title,
                             explanation, source_id, source_ref, version, supersedes, ctx.user_id))
            audit.record(self.db, ctx, "safety.rule_create", "safety_rule", rid,
                         after={"type": rule_type, "severity": severity, "params": params,
                                "version": version})
        return rid

    def _validate_params(self, ctx: Context, rule_type: str, p: dict) -> None:
        def ing_ok(i):
            return self.db.one("SELECT 1 FROM active_ingredients WHERE id=? AND"
                               " (org_id IS NULL OR org_id=?)", (i, ctx.org_id))
        if rule_type == "interaction":
            ings = p.get("ingredients") or []
            if len(set(ings)) != 2 or not all(ing_ok(i) for i in ings):
                raise ValidationFailed("قاعدة التداخل تحتاج مادتين فعالتين مسجلتين")
        elif rule_type == "max_qty":
            if not (p.get("item_id") or p.get("ingredient_id")) or int(p.get("max_base_units", 0)) <= 0:
                raise ValidationFailed("حدد الصنف أو المادة والحد الأقصى")
        elif rule_type == "split_policy":
            if not (p.get("item_id") or p.get("scope") == "all_items") \
                    or not isinstance(p.get("allow_partial_pack"), bool) \
                    or not isinstance(p.get("allow_tablet_split"), bool):
                raise ValidationFailed("سياسة التقسيم تحتاج الصنف (أو كل الأصناف) وقيم صريحة (نعم/لا)")

    def approve_rule(self, ctx: Context, rule_id: str, *, self_approval_reason: str | None = None) -> None:
        ctx.require("safety.approve")
        with self.db.transaction():
            r = self.db.one("SELECT * FROM safety_rules WHERE id=? AND org_id=?", (rule_id, ctx.org_id))
            if not r:
                raise NotFound("القاعدة غير موجودة")
            if r["status"] == "approved":
                raise Conflict("القاعدة معتمدة بالفعل")
            if r["created_by"] == ctx.user_id:
                # who else may approve, by what each person can really do (role, plus rights granted or
                # denied to them): role defaults alone missed a pharmacist given safety.approve (#324 X9)
                others = [m["user_id"] for m in self.db.all(
                    "SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.org_id=?"
                    " AND m.user_id<>? AND m.is_active=1 AND u.is_active=1", (ctx.org_id, ctx.user_id))
                    if "safety.approve" in accounts.effective_access(self.db, ctx.org_id, m["user_id"])["permissions"]]
                if others:
                    raise Forbidden("يجب أن يعتمد القاعدة مستخدم آخر مخوّل (مراجعة رباعية العين)",
                                    code="four_eyes")
                if len((self_approval_reason or "").strip()) < 10:
                    raise ValidationFailed("أنت الوحيد المخوّل؛ اكتب سبب الاعتماد الذاتي ومن راجع القاعدة",
                                           code="self_approval_reason_required")
            self.db.execute("UPDATE safety_rules SET status='approved', approved_by=?, approved_at=?,"
                            " disabled_by=NULL, disabled_at=NULL, disabled_reason=NULL WHERE id=?",
                            (ctx.user_id, _now(), rule_id))
            if r["supersedes"]:
                self.db.execute("UPDATE safety_rules SET status='disabled', disabled_by=?, disabled_at=?,"
                                " disabled_reason=? WHERE id=? AND status='approved'",
                                (ctx.user_id, _now(), f"superseded by v{r['version']}", r["supersedes"]))
            audit.record(self.db, ctx, "safety.rule_approve", "safety_rule", rule_id,
                         after={"self_approval": r["created_by"] == ctx.user_id,
                                "reason": self_approval_reason})

    def disable_rule(self, ctx: Context, rule_id: str, reason: str) -> None:
        ctx.require("safety.manage")
        if len((reason or "").strip()) < 5:
            raise ValidationFailed("اكتب سبب التعطيل")
        with self.db.transaction():
            n = self.db.execute("UPDATE safety_rules SET status='disabled', disabled_by=?, disabled_at=?,"
                                " disabled_reason=? WHERE id=? AND org_id=? AND status<>'disabled'",
                                (ctx.user_id, _now(), reason, rule_id, ctx.org_id)).rowcount
            if not n:
                raise NotFound("القاعدة غير موجودة أو معطلة")
            audit.record(self.db, ctx, "safety.rule_disable", "safety_rule", rule_id,
                         after={"reason": reason})

    def edit_approved_rule(self, ctx: Context, rule_id: str, **_):
        raise Conflict("القاعدة المعتمدة لا تُعدَّل؛ أنشئ إصدارًا جديدًا يحل محلها", code="immutable_rule")

    # ------------------------------------------------------------ check
    def _rules(self, ctx: Context, rule_type: str) -> list[dict]:
        rows = self.db.all("SELECT r.*, s.name s_name, s.version s_version FROM safety_rules r"
                           " LEFT JOIN knowledge_sources s ON s.id=r.source_id"
                           " WHERE r.status='approved' AND r.rule_type=? AND"
                           " (r.org_id IS NULL OR r.org_id=?)", (rule_type, ctx.org_id))
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"])
            out.append(d)
        return out

    @staticmethod
    def _src(rule: dict) -> dict | None:
        if not rule.get("s_name"):
            return None
        return {"name": rule["s_name"], "version": rule["s_version"], "ref": rule.get("source_ref")}

    def check(self, ctx: Context, lines: list[dict], *, context_type: str = "check",
              context_id: str | None = None, log: bool = True) -> list[Finding]:
        """lines: [{"item_id", "base_qty"?, "partial_pack"?: bool, "tablet_split"?: bool}]"""
        ctx.require("safety.read")
        item_ids = []
        for l in lines:
            if not self.db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (l["item_id"], ctx.org_id)):
                raise NotFound("الصنف غير موجود")
            item_ids.append(l["item_id"])
        labels = {i: self.db.one("SELECT coalesce(name_en,name_ar) n FROM items WHERE id=?", (i,))["n"]
                  for i in item_ids}
        comp: dict[str, list[dict]] = {}
        for i in set(item_ids):
            comp[i] = [dict(r) for r in self.db.all(
                "SELECT ii.ingredient_id, ii.strength, ii.status, a.name_en FROM item_ingredients ii"
                " JOIN active_ingredients a ON a.id=ii.ingredient_id WHERE ii.org_id=? AND ii.item_id=?",
                (ctx.org_id, i))]
        findings: list[Finding] = []

        # insufficient data: never silently "safe"
        for i in dict.fromkeys(item_ids):
            if not any(c["status"] == "verified" for c in comp[i]):
                findings.append(Finding("insufficient_data", "insufficient",
                                        f"لا توجد بيانات مادة فعالة موثقة للصنف {labels[i]} — لا يمكن فحص التكرار والتداخلات",
                                        [i], requires="none"))   # shown, never hidden; not a click-gate
                                                                 # (avoids alert fatigue on every sale)

        verified = {i: [c for c in comp[i] if c["status"] == "verified"] for i in comp}
        # duplicate ingredient across different items
        dup_rule = next(iter(self._rules(ctx, "duplicate_ingredient")), None)
        by_ing: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        for i in dict.fromkeys(item_ids):
            for c in verified[i]:
                by_ing.setdefault(c["ingredient_id"], []).append(i)
                names[c["ingredient_id"]] = c["name_en"]
        for ing, items in by_ing.items():
            if len(items) > 1:
                strengths = {c["strength"] for i in items for c in verified[i]
                             if c["ingredient_id"] == ing}
                sev = dup_rule["severity"] if dup_rule else "high"
                msg = (f"نفس المادة الفعالة ({names[ing]}) موجودة في أكثر من صنف: "
                       + "، ".join(labels[i] for i in items))
                if len(strengths) > 1:
                    msg += " — وبتركيزات مختلفة"
                findings.append(Finding("duplicate_ingredient", sev, msg, items,
                                        dup_rule["id"] if dup_rule else None,
                                        dup_rule["version"] if dup_rule else None,
                                        self._src(dup_rule) if dup_rule else None,
                                        requires=self._requires(sev)))

        # interactions (only approved, sourced rules). If none are loaded, say so
        # explicitly: an empty result must never read as "no interactions".
        if not self._rules(ctx, "interaction"):
            findings.append(Finding("interaction_check_unavailable", "insufficient",
                                    "لم يتم فحص التداخلات الدوائية: لا توجد قاعدة تداخلات معتمدة مفعّلة بعد",
                                    list(dict.fromkeys(item_ids)), requires="none"))
        ing_to_items: dict[str, set[str]] = {}
        for i, cs in verified.items():
            for c in cs:
                ing_to_items.setdefault(c["ingredient_id"], set()).add(i)
        for rule in self._rules(ctx, "interaction"):
            a, b = rule["params"]["ingredients"]
            if a in ing_to_items and b in ing_to_items:
                its = sorted(ing_to_items[a] | ing_to_items[b])
                findings.append(Finding("interaction", rule["severity"],
                                        f"{rule['title']}: {rule['explanation']}", its, rule["id"],
                                        rule["version"], self._src(rule),
                                        requires=self._requires(rule["severity"])))

        # quantity limits
        for rule in self._rules(ctx, "max_qty"):
            p = rule["params"]
            total = sum(int(l.get("base_qty") or 0) for l in lines
                        if l["item_id"] == p.get("item_id") or
                        (p.get("ingredient_id") and any(c["ingredient_id"] == p["ingredient_id"]
                                                        for c in verified.get(l["item_id"], []))))
            if total > int(p["max_base_units"]):
                findings.append(Finding("max_qty", rule["severity"],
                                        f"{rule['title']}: الكمية {total} أكبر من الحد {p['max_base_units']}",
                                        [l["item_id"] for l in lines], rule["id"], rule["version"],
                                        self._src(rule), requires=self._requires(rule["severity"])))

        # partial pack / tablet split: absent policy => ask the pharmacist (never assume)
        split_rules = self._rules(ctx, "split_policy")
        policies = {r["params"]["item_id"]: r for r in split_rules if r["params"].get("item_id")}
        default_policy = next((r for r in split_rules if r["params"].get("scope") == "all_items"), None)
        for l in lines:
            for flag, key, label in (("partial_pack", "allow_partial_pack", "صرف جزء من العبوة"),
                                     ("tablet_split", "allow_tablet_split", "تقسيم القرص")):
                if not l.get(flag):
                    continue
                pol = policies.get(l["item_id"]) or default_policy   # item policy wins
                if pol is None:
                    # selling a strip from a box is routine; splitting a tablet is riskier
                    sev, req = (("moderate", "acknowledge") if flag == "partial_pack"
                                else ("high", "override"))
                    findings.append(Finding("split_unknown", sev,
                                            f"لا توجد سياسة موثقة لـ {label} للصنف {labels[l['item_id']]} — يلزم قرار الصيدلي",
                                            [l["item_id"]], requires=req))
                elif not pol["params"][key]:
                    findings.append(Finding("split_forbidden", pol["severity"],
                                            f"{label} غير مسموح للصنف {labels[l['item_id']]}: {pol['explanation']}",
                                            [l["item_id"]], pol["id"], pol["version"], self._src(pol),
                                            requires=self._requires(pol["severity"])))

        # look-alike neighbours (heuristic, informational)
        for i in dict.fromkeys(item_ids):
            for c in matching.fuzzy_candidates(self.db, ctx.org_id, labels[i], 6):
                if c.item_id != i and c.item_id not in item_ids and c.reasons and \
                        any(r.endswith("_mismatch") for r in c.reasons):
                    base_p = parse_name(labels[i])
                    if base_p.core and base_p.core == parse_name(c.label).core:
                        findings.append(Finding("lookalike", "info",
                                                f"تأكد من الصنف: يوجد صنف مشابه بالاسم ({c.label}) بتركيز أو شكل مختلف",
                                                [i, c.item_id], basis="heuristic", requires="none"))

        findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 1 if f.severity == "insufficient" else 0),
                      reverse=True)
        if log:
            month = _today().strftime("%Y-%m")
            fresh = []
            with self.db.transaction():
                for f in findings:
                    if f.severity == "info" or f.code == "interaction_check_unavailable":
                        continue
                    if f.code in ONCE_A_MONTH:
                        # the same notice for the same item on every sale: recorded the first time it is shown
                        # in a month, still shown every time (it was 95% of this table, #304 B10)
                        key = (ctx.org_id, f.code, tuple(f.item_ids), month)
                        with _logged_lock:
                            if key in _logged:
                                continue
                        fresh.append(key)
                    self._event(ctx, context_type, context_id, f, "shown")
            # remembered only once the rows are committed: a sale that fails and rolls back writes it next time
            if fresh and not self.db.conn.in_transaction:
                with _logged_lock:
                    if len(_logged) > 500_000:          # a bound, never reached by one pharmacy in a month
                        _logged.clear()
                    _logged.update(fresh)
        return findings

    @staticmethod
    def _requires(severity: str) -> str:
        return {"critical": "block", "high": "override", "moderate": "acknowledge",
                "low": "acknowledge"}.get(severity, "none")

    def _event(self, ctx: Context, context_type: str, context_id: str | None, f: Finding,
               decision: str, reason: str | None = None) -> None:
        self.db.execute("INSERT INTO safety_events (id, org_id, user_id, context_type, context_id,"
                        " rule_id, rule_version, finding_code, severity, message, decision, reason)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (new_id(), ctx.org_id, ctx.user_id, context_type, context_id, f.rule_id,
                         f.rule_version, f.code, f.severity, f.message, decision, reason))

    def resolve(self, ctx: Context, findings: list[Finding], decisions: dict[int, str] | None = None,
                *, context_type: str = "check", context_id: str | None = None) -> bool:
        """Decide whether the action may proceed. decisions: {finding_index: reason}.
        Returns True when allowed. Every decision (and every refusal) is logged."""
        decisions = decisions or {}
        allowed = True
        with self.db.transaction():
            for n, f in enumerate(findings):
                need = f.requires or "none"
                reason = (decisions.get(n) or "").strip()
                if need == "none":
                    continue
                if need == "acknowledge":
                    if n not in decisions:
                        allowed = False
                        continue
                    self._event(ctx, context_type, context_id, f, "acknowledged", reason or None)
                elif need == "override":
                    if not ctx.can("safety.override") or len(reason) < 5:
                        self._event(ctx, context_type, context_id, f, "override_denied", reason or None)
                        allowed = False
                        continue
                    self._event(ctx, context_type, context_id, f, "overridden", reason)
                elif need == "block":
                    if not ctx.can("safety.override_critical") or len(reason) < 10:
                        self._event(ctx, context_type, context_id, f, "blocked", reason or None)
                        allowed = False
                        continue
                    self._event(ctx, context_type, context_id, f, "overridden", reason)
        return allowed


# --------------------------------------------------------------------------
def evaluate(engine: SafetyEngine, ctx: Context, cases: list[dict]) -> dict:
    """Benchmark harness (spec §6): each case = {"lines": [...], "expected": {codes}}.
    Returns sensitivity (recall), false-positive count and missed cases, so rule
    sets can be measured against an independent reference before release."""
    tp = fp = fn = 0
    missed, false_pos = [], []
    for k, case in enumerate(cases):
        got = {f.code for f in engine.check(ctx, case["lines"], log=False)
               if f.code not in ("lookalike", "interaction_check_unavailable")}
        exp = set(case["expected"])
        tp += len(got & exp)
        fp += len(got - exp)
        fn += len(exp - got)
        if exp - got:
            missed.append({"case": k, "missed": sorted(exp - got)})
        if got - exp:
            false_pos.append({"case": k, "extra": sorted(got - exp)})
    return {"sensitivity": tp / (tp + fn) if tp + fn else 1.0, "false_positives": fp,
            "true_positives": tp, "missed": missed, "false_positive_cases": false_pos}
