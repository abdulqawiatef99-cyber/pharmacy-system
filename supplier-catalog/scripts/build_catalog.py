"""Build the unified offers table, the matched catalog, comparisons and review lists.

Inputs: originals/ (Excel files) and build/pdf_*.csv (from extract_pdfs.py).
Usage: python -I scripts/build_catalog.py <originals_dir> <build_dir>

Business rules (confirmed by the owner, 2026-10-31):
- Drug warehouses (Nahda, Ibn Khaldun, Wataniya): cost = public price * (1 - discount/100).
  All purchases are cash, so Wataniya's "كاش سياره" discount applies. Decimals kept as printed.
- Future: the "السعر" column is the net pharmacy price; no extra discount.
- Al Mostafa: no public price; cost = the lower of "سعر ج" and "نصف جمله" (both kept).
- Minimum order values exist but are not binding in practice; not included in costs.
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import openpyxl
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import DRUG_FORMS, match_key, parse_name, tidy_display  # noqa: E402

SUPPLIERS = {
    "wataniya":    {"name": "Al Wataniya", "name_ar": "الوطنية", "group": "drug_wh",
                    "file": "wataniya_مخزن.xlsx", "price_date": "2026-10-31", "date_basis": "owner"},
    "ibn_khaldun": {"name": "Ibn Khaldun", "name_ar": "ابن خلدون", "group": "drug_wh",
                    "file": "ibn_khaldun_اوردر_شركات.pdf", "price_date": "2026-10-28", "date_basis": "pdf_footer"},
    "nahda":       {"name": "Al Nahda", "name_ar": "النهضة", "group": "drug_wh",
                    "file": "nahda_اوردر.pdf", "price_date": "2026-10-31", "date_basis": "pdf_footer"},
    "future":      {"name": "Future", "name_ar": "فيوتشر", "group": "supplies_wh",
                    "file": "future_تحديث_فيوتشر_كبار_عملاء_31-10.pdf", "price_date": "2026-10-31",
                    "date_basis": "pdf_footer"},
    "mostafa":     {"name": "Al Mostafa", "name_ar": "المصطفى", "group": "supplies_wh",
                    "file": "mostafa_اوردر_اسبوعى_مستلزمات.xlsx", "price_date": "2026-10-31", "date_basis": "owner"},
}
NAME_PRIORITY = {
    "medicine": ["wataniya", "ibn_khaldun", "nahda", "future", "mostafa"],
    "accessory": ["future", "mostafa", "wataniya", "ibn_khaldun", "nahda"],
}
SUPPLIER_ORDER = ["wataniya", "ibn_khaldun", "nahda", "future", "mostafa"]
CENT = Decimal("0.01")
ACCESSORY_WORDS = set("""
شامبو بلسم صابون معطر مزيل عرق صبغه بودره فوط فوطه حفاض حفاضات بامبرز ببرونه حلمه سكينه ماكينه فرشاه
معجون غسول تفتيح واقي صن بلوك مرطب كولونيا بارفان برفان عطر بادي ماسك هير ليف مناديل قطن شاش بلاستر
سرنجه جوانتي جوانتى كمامه ترمومتر جهاز رباط ضاغط مشد حزام لاصق قسطره كانيولا كيس قربه مبوله حمام
ديودرنت رول سيروم تونر مكياج روج ماسكارا ايلاينر طلاء مانيكير كريمات حلاقه شفرات موس جل_شعر بخور
""".split())
# Words that distinguish real product variants; a fuzzy match may not differ by any of them.
VARIANT_WORDS = set("""
كبير صغير وسط وسطه متوسط ميني جامبو عائلي فراوله فانيليا شوكولاته شيكولاته ليمون نعناع تفاح برتقال مانجو
موز كراميل عسل اناناس كرز توت بطيخ جوز لافندر ورد روز ياسمين صبار خيار
اطفال طفل كبار رجالي رجال حريمي نسائي بيبي جونيور كيدز سادة سادة مخطط وردي ازرق احمر اسود ابيض بني اصفر اخضر
بنفسجي ذهبي فضي رمادي كحلي شفاف لايت دارك عادي جاف دهني مختلط حساس حساسه
كومب بلس فورت فورتي ريتارد ممتد فوار مضغ استحلاب ليلي نهاري اكسترا ماكس دبل ديو تريو
forte plus extra max mini junior kids baby sr xr mr cr er od duo combi retard light dark
""".split())


def dec(v):
    if v is None or v == "":
        return None
    if isinstance(v, float):
        # Excel stores e.g. 9.333000183105469 for 9.333; keep 4 decimals as printed precision
        return Decimal(repr(round(v, 4)))
    return Decimal(str(v))


def money(d):
    return None if d is None else d.quantize(CENT, rounding=ROUND_HALF_UP)


def load_offers(orig, build):
    offers, findings = [], []

    def add(sid, ref, name, public=None, disc=None, cost=None, code="", extra=None, raw=""):
        offers.append({"offer_id": ref, "supplier": sid, "supplier_code": code, "name_raw": name,
                       "public_price": public, "discount_pct": disc, "cost": cost,
                       "extra": extra or {}, "raw_text": raw})

    ws = openpyxl.load_workbook(orig / SUPPLIERS["wataniya"]["file"], read_only=True, data_only=True).active
    for i, r in enumerate(ws.iter_rows(values_only=True), start=1):
        if i == 1:
            if [str(x).strip() for x in r[:3]] != ["كاش سياره", "سعر ج", "الصنف"]:
                findings.append(("wataniya", "header_changed", str(r)))
            continue
        if not any(v not in (None, "") for v in r):
            continue
        disc, pub, name = dec(r[0]), dec(r[1]), str(r[2]).strip()
        add("wataniya", f"wataniya:r{i}", name, pub, disc, money(pub * (100 - disc) / 100),
            raw=f"{r[0]} | {r[1]} | {r[2]}")

    ws = openpyxl.load_workbook(orig / SUPPLIERS["mostafa"]["file"], read_only=True, data_only=True).active
    for i, r in enumerate(ws.iter_rows(values_only=True), start=1):
        if i == 1:
            if [str(x).strip() for x in r[:4]] != ["نصف جمله", "سعر ج", "الكود", "الصنف"]:
                findings.append(("mostafa", "header_changed", str(r)))
            continue
        if not any(v not in (None, "") for v in r):
            continue
        half, wh, code, name = dec(r[0]), dec(r[1]), str(r[2]), str(r[3]).strip()
        cost = min(x for x in (half, wh) if x is not None)
        add("mostafa", f"mostafa:r{i}", name, None, None, money(cost), code,
            {"mostafa_nesf_gomla": money(half), "mostafa_sear_g": money(wh),
             "cost_column": "سعر ج" if cost == wh else "نصف جمله"},
            raw=f"{r[0]} | {r[1]} | {r[2]} | {r[3]}")

    for sid in ("nahda", "ibn_khaldun", "future"):
        with open(build / f"pdf_{sid}.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if sid == "future":
                    cost, pub = dec(r["price"]), dec(r["second"])
                    add(sid, r["source_row"], r["name"], pub, None, money(cost), r["code"],
                        {"page": r["page"], "notes": r["notes"]}, r["raw_text"])
                else:
                    pub, disc = dec(r["price"]), dec(r["second"])
                    cost = money(pub * (100 - disc) / 100) if pub is not None and disc is not None else None
                    add(sid, r["source_row"], r["name"], pub, disc, cost, "",
                        {"page": r["page"], "notes": r["notes"]}, r["raw_text"])
    return offers, findings


class DSU:
    def __init__(self, offers):
        self.p = list(range(len(offers)))
        self.members = {i: {(o["supplier"], o["public_price"], o["key"])} for i, o in enumerate(offers)}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def conflict(self, a, b):
        """Two different listings of one supplier at one public price must not merge."""
        ma, mb = self.members[self.find(a)], self.members[self.find(b)]
        sa = {(s, pp): k for s, pp, k in ma}
        return any((s, pp) in sa and sa[(s, pp)] != k for s, pp, k in mb)

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        self.p[rb] = ra
        self.members[ra] |= self.members.pop(rb)


def numbers_relation(a, b):
    sa, sb = set(a["numbers"]), set(b["numbers"])
    if sa == sb:
        return "equal"
    if sa < sb or sb < sa:
        return "subset"
    return "different"


def compare(a, b):
    """Return (decision, score, reason) for two parsed offers."""
    if a["form"] and b["form"] and a["form"] != b["form"]:
        return "reject", 0, "form_differs"
    if a["strengths"] and b["strengths"] and set(a["strengths"]) != set(b["strengths"]):
        return "reject", 0, "strength_differs"
    rel = numbers_relation(a, b)
    if rel == "different":
        return "reject", 0, "numbers_differ"
    bs = fuzz.ratio(a["brand"], b["brand"])
    cs = fuzz.token_sort_ratio(a["core"], b["core"])
    score = round((bs + cs) / 2, 1)
    return rel, score, f"brand={bs:.0f} core={cs:.0f} numbers={rel}"


def main(orig, build):
    orig, build = Path(orig), Path(build)
    offers, findings = load_offers(orig, build)
    for o in offers:
        o["p"] = parse_name(o["name_raw"])
        o["key"] = match_key(o["p"])
        o["group"] = SUPPLIERS[o["supplier"]]["group"]
        o["match_method"] = "single"
    dsu = DSU(offers)
    uncertain, conflicts = [], []

    # A) exact key
    by_key = defaultdict(list)
    for i, o in enumerate(offers):
        if o["p"]["core"]:
            by_key[o["key"]].append(i)
    for idx in by_key.values():
        for j in idx[1:]:
            dsu.union(idx[0], j)
        if len({offers[j]["supplier"] for j in idx}) > 1:
            for j in idx:
                offers[j]["match_method"] = "exact_key"

    def try_link(i, j, auto, score, reason, method):
        if dsu.find(i) == dsu.find(j):
            return
        if auto and not dsu.conflict(i, j):
            dsu.union(i, j)
            for k in (i, j):
                if offers[k]["match_method"] == "single":
                    offers[k]["match_method"] = method
        else:
            uncertain.append((i, j, score, reason + (";blocked_same_supplier_conflict" if auto else "")))

    # B) fuzzy within the same public price, across suppliers
    by_price = defaultdict(list)
    for i, o in enumerate(offers):
        if o["public_price"] is not None and o["p"]["core"]:
            by_price[o["public_price"]].append(i)
    for idx in by_price.values():
        for x in range(len(idx)):
            a = offers[idx[x]]
            for y in range(x + 1, len(idx)):
                b = offers[idx[y]]
                if a["supplier"] == b["supplier"] or a["key"] == b["key"]:
                    continue
                rel, score, reason = compare(a["p"], b["p"])
                if rel == "reject":
                    continue
                bs = fuzz.ratio(a["p"]["brand"], b["p"]["brand"])
                cs = fuzz.token_sort_ratio(a["p"]["core"], b["p"]["core"])
                auto = (rel == "equal" and bs >= 80 and cs >= 75) or (rel == "subset" and bs >= 85 and cs >= 85)
                method = "fuzzy_same_price"
                if not auto and bs >= 85:
                    ta, tb = set(a["p"]["core"].split()), set(b["p"]["core"].split())
                    if fuzz.token_set_ratio(a["p"]["core"], b["p"]["core"]) >= 90 and not ((ta ^ tb) & VARIANT_WORDS):
                        auto, method = True, "fuzzy_same_price_subset_words"
                if auto or cs >= 60 or bs >= 80:
                    try_link(idx[x], idx[y], auto, score, "same_public_price;" + reason, method)

    # C) Al Mostafa (no public price) against all other suppliers, blocked by brand prefix
    by_prefix = defaultdict(list)
    for i, o in enumerate(offers):
        if o["p"]["brand"] and o["supplier"] != "mostafa":
            by_prefix[o["p"]["brand"][:2]].append(i)
    for i, o in enumerate(offers):
        if o["supplier"] != "mostafa" or not o["p"]["core"]:
            continue
        for j in by_prefix.get(o["p"]["brand"][:2], []):
            b = offers[j]
            if o["key"] == b["key"]:
                continue
            rel, score, reason = compare(o["p"], b["p"])
            if rel != "equal":
                continue
            bs = fuzz.ratio(o["p"]["brand"], b["p"]["brand"])
            cs = fuzz.token_sort_ratio(o["p"]["core"], b["p"]["core"])
            auto = bs >= 90 and cs >= 90
            if auto or cs >= 75:
                try_link(i, j, auto, score, "no_public_price;" + reason, "fuzzy_no_price")

    # Clusters -> items
    clusters = defaultdict(list)
    for i in range(len(offers)):
        clusters[dsu.find(i)].append(i)
    items = []
    for root, idx in clusters.items():
        mem = [offers[i] for i in idx]
        sups = {o["supplier"] for o in mem}
        forms = Counter(o["p"]["form"] for o in mem if o["p"]["form"])
        form = forms.most_common(1)[0][0] if forms else None
        if sups & {"future", "mostafa"}:
            cat, basis = "accessory", "sold_by_supplies_warehouse"
        elif form in DRUG_FORMS:
            cat, basis = "medicine", "dosage_form"
        elif any(set(o["p"]["core"].split()) & ACCESSORY_WORDS or o["p"]["form"] in {"shampoo", "soap"}
                 for o in mem):
            cat, basis = "accessory", "keyword_in_name"
        else:
            cat, basis = "medicine", "drug_warehouse_only_unverified"
        rep = None
        for sid in NAME_PRIORITY[cat]:
            cand = [o for o in mem if o["supplier"] == sid]
            if cand:
                rep = max(cand, key=lambda o: len(o["name_raw"]))
                break
        pubs = Counter(o["public_price"] for o in mem if o["public_price"] is not None)
        ref_pub = max(pubs.items(), key=lambda kv: (kv[1], kv[0]))[0] if pubs else None
        dup_listings = [s for s, c in Counter((o["supplier"], o["public_price"]) for o in mem).items() if c > 1]
        items.append({"root": root, "idx": idx, "category": cat, "category_basis": basis,
                      "display_name": tidy_display(rep["name_raw"]), "rep": rep, "form": form,
                      "public_price": ref_pub, "public_prices": sorted(pubs), "suppliers": sups,
                      "dup_listings": dup_listings})
    items.sort(key=lambda it: (0 if it["category"] == "medicine" else 1, it["rep"]["p"]["name_norm"],
                               it["display_name"]))
    for n, it in enumerate(items, start=1):
        it["item_id"] = f"SC{n:06d}"
        for i in it["idx"]:
            offers[i]["item_id"] = it["item_id"]

    write_outputs(build, offers, items, uncertain, findings)


def fmt(d):
    return "" if d is None else str(d)


def write_outputs(build, offers, items, uncertain, findings):
    with open(build / "offers.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["offer_id", "item_id", "supplier", "supplier_name", "supplier_code", "name_raw",
                    "public_price", "discount_pct", "net_cost", "cost_basis", "mostafa_nesf_gomla",
                    "mostafa_sear_g", "name_norm", "core", "strengths", "numbers", "pack_count", "form",
                    "price_tag_in_name", "match_key", "match_method", "price_date", "parse_notes", "raw_text"])
        for o in offers:
            s = SUPPLIERS[o["supplier"]]
            basis = {"drug_wh": "public*(1-discount/100)"}.get(s["group"], "")
            if o["supplier"] == "future":
                basis = "net price column (السعر)"
            if o["supplier"] == "mostafa":
                basis = "lower of سعر ج / نصف جمله -> " + o["extra"]["cost_column"]
            w.writerow([o["offer_id"], o["item_id"], o["supplier"], s["name"], o["supplier_code"], o["name_raw"],
                        fmt(o["public_price"]), fmt(o["discount_pct"]), fmt(o["cost"]), basis,
                        fmt(o["extra"].get("mostafa_nesf_gomla")), fmt(o["extra"].get("mostafa_sear_g")),
                        o["p"]["name_norm"], o["p"]["core"], " ".join(o["p"]["strengths"]),
                        " ".join(o["p"]["numbers"]), o["p"]["pack_count"] or "", o["p"]["form"] or "",
                        " ".join(fmt(Decimal(str(x))) for x in o["p"]["price_tags"]) or
                        ("yes" if o["p"]["has_price_tag"] else ""),
                        o["key"], o["match_method"], s["price_date"], o["extra"].get("notes", ""), o["raw_text"]])

    with open(build / "catalog_items.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "category", "category_basis", "display_name", "name_norm", "strengths", "form",
                    "pack_count", "public_price", "other_public_prices", "supplier_count", "offer_count",
                    "suppliers", "best_supplier", "best_cost", "flags"])
        for it in items:
            mem = [offers[i] for i in it["idx"]]
            best = best_offer(mem, it["public_price"])
            flags = []
            if len(it["public_prices"]) > 1:
                flags.append("multiple_public_prices")
            if it["dup_listings"]:
                flags.append("same_supplier_listed_twice")
            if any(o["match_method"].startswith("fuzzy") for o in mem):
                flags.append("contains_fuzzy_match")
            if it["category_basis"].endswith("unverified"):
                flags.append("category_unverified")
            w.writerow([it["item_id"], it["category"], it["category_basis"], it["display_name"],
                        it["rep"]["p"]["name_norm"], " ".join(it["rep"]["p"]["strengths"]), it["form"] or "",
                        it["rep"]["p"]["pack_count"] or "", fmt(it["public_price"]),
                        " ".join(fmt(x) for x in it["public_prices"] if x != it["public_price"]),
                        len(it["suppliers"]), len(mem),
                        " ".join(s for s in SUPPLIER_ORDER if s in it["suppliers"]),
                        best[0]["supplier"] if best else "", fmt(best[0]["cost"]) if best else "",
                        ";".join(flags)])

    with open(build / "comparison.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "category", "display_name", "public_price"] +
                   [f"cost_{s}" for s in SUPPLIER_ORDER] +
                   ["best_supplier", "best_cost", "second_supplier", "second_cost", "saving", "saving_pct", "note"])
        for it in items:
            if len(it["suppliers"]) < 2:
                continue
            mem = [offers[i] for i in it["idx"]]
            ref = it["public_price"]
            per = {}
            for o in mem:
                if o["cost"] is None:
                    continue
                if ref is not None and o["public_price"] is not None and o["public_price"] != ref:
                    continue  # different public price level (old/new stock): not like-for-like
                if o["supplier"] not in per or o["cost"] < per[o["supplier"]]["cost"]:
                    per[o["supplier"]] = o
            if len(per) < 2:
                continue
            ranked = sorted(per.values(), key=lambda o: o["cost"])
            saving = ranked[1]["cost"] - ranked[0]["cost"]
            pct = (saving / ranked[1]["cost"] * 100).quantize(CENT) if ranked[1]["cost"] else Decimal(0)
            note = []
            if "mostafa" in per:
                note.append("mostafa_has_no_public_price")
            if len(it["public_prices"]) > 1:
                note.append("other_price_levels_excluded")
            if any(o["match_method"].startswith("fuzzy") for o in per.values()):
                note.append("fuzzy_match_review")
            w.writerow([it["item_id"], it["category"], it["display_name"], fmt(ref)] +
                       [fmt(per[s]["cost"]) if s in per else "" for s in SUPPLIER_ORDER] +
                       [ranked[0]["supplier"], fmt(ranked[0]["cost"]), ranked[1]["supplier"],
                        fmt(ranked[1]["cost"]), fmt(saving), fmt(pct), ";".join(note)])

    seen = set()
    with open(build / "uncertain_matches.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["offer_a", "supplier_a", "name_a", "public_a", "item_a", "offer_b", "supplier_b", "name_b",
                    "public_b", "item_b", "score", "reason", "decision(fill: same/different)"])
        for i, j, score, reason in sorted(uncertain, key=lambda t: -t[2]):
            a, b = offers[i], offers[j]
            if a["item_id"] == b["item_id"]:
                continue
            k = tuple(sorted((a["item_id"], b["item_id"])))
            if k in seen:
                continue
            seen.add(k)
            w.writerow([a["offer_id"], a["supplier"], a["name_raw"], fmt(a["public_price"]), a["item_id"],
                        b["offer_id"], b["supplier"], b["name_raw"], fmt(b["public_price"]), b["item_id"],
                        score, reason, ""])

    # validation findings
    rows = list(findings)
    cnt = Counter(o["supplier"] for o in offers)
    for sid, c in cnt.items():
        rows.append((sid, "offer_count", str(c)))
    for o in offers:
        if o["cost"] is None:
            rows.append((o["supplier"], "missing_cost", o["offer_id"]))
        if o["discount_pct"] is not None and not (Decimal(0) <= o["discount_pct"] < Decimal(100)):
            rows.append((o["supplier"], "discount_out_of_range", f'{o["offer_id"]} {o["discount_pct"]}'))
        if o["discount_pct"] is not None and o["discount_pct"] >= 60:
            rows.append((o["supplier"], "very_high_discount_check", f'{o["offer_id"]} {o["discount_pct"]}% {o["name_raw"]}'))
        if o["discount_pct"] == 0:
            rows.append((o["supplier"], "zero_discount", f'{o["offer_id"]} {o["name_raw"]}'))
        if o["supplier"] == "future" and o["cost"] and o["public_price"] and o["cost"] > o["public_price"]:
            rows.append(("future", "net_above_public", o["offer_id"]))
        if o["supplier"] == "mostafa" and o["extra"]["cost_column"] != "سعر ج":
            rows.append(("mostafa", "nesf_gomla_lower_than_sear_g", f'{o["offer_id"]} {o["name_raw"]}'))
        if o["p"]["price_tags"] and o["public_price"] is not None and \
                all(Decimal(str(t)) != o["public_price"] for t in o["p"]["price_tags"]):
            rows.append((o["supplier"], "price_tag_in_name_differs_from_public",
                         f'{o["offer_id"]} tag={o["p"]["price_tags"]} public={o["public_price"]} {o["name_raw"]}'))
        if not o["p"]["core"]:
            rows.append((o["supplier"], "empty_core_name_not_matched", f'{o["offer_id"]} {o["name_raw"]}'))
    for it in items:
        if it["dup_listings"]:
            rows.append(("catalog", "same_supplier_twice_in_item", f'{it["item_id"]} {it["dup_listings"]}'))
    with open(build / "validation_findings.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["scope", "check", "detail"])
        w.writerows(rows)

    metrics = {
        "offers": len(offers), "offers_by_supplier": dict(cnt), "items": len(items),
        "items_by_category": dict(Counter(it["category"] for it in items)),
        "items_multi_supplier": sum(len(it["suppliers"]) > 1 for it in items),
        "match_methods": dict(Counter(o["match_method"] for o in offers)),
        "uncertain_pairs": len(seen),
        "findings": dict(Counter(r[1] for r in rows)),
    }
    (build / "catalog_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


def best_offer(mem, ref):
    c = [o for o in mem if o["cost"] is not None and
         (ref is None or o["public_price"] is None or o["public_price"] == ref)]
    return sorted(c, key=lambda o: o["cost"]) if c else None


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
