"""Suggest a section for items that have none, from words in the name and the dosage form.

Only a suggestion: the owner sees every item under each suggested section, unticks what is
wrong and applies the rest himself (the program never files items by itself). Items that
match nothing stay without a section."""
from __future__ import annotations

import re

from ..core.permissions import Context
from ..db import Database

W = lambda *ws: re.compile(r"(?<![A-Z])(?:" + "|".join(ws) + r")(?![A-Z])")  # noqa: E731

# (default section name, English-name pattern, Arabic-name pattern, dosage forms) — first match wins
RULES = [
    ("تلاجة", W("INSULIN", "LANTUS", "LEVEMIR", "TOUJEO", "MIXTARD", "INSULATARD", "ACTRAPID", "NOVORAPID", "NOVOMIX",
               "HUMALOG", "HUMULIN", "APIDRA", "TRESIBA", "RYZODEG", "OZEMPIC", "VICTOZA", "TRULICITY", "SAXENDA",
               "VACCINE", "VACC", "ERYTHROPOIETIN", "EPREX", "RECORMON", "NEUPOGEN", "CLEXANE", "INNOHEP"),
     re.compile(r"انسولين|أنسولين|فاكسين|مصل|طعم"), ()),
    ("حقن", W("AMP", "AMPS", "AMPOULE", "AMPOULES", "VIAL", "VIALS", "INJ", "INJECTION", "IV", "IM", "S\\.C", "I\\.V", "I\\.M",
             "PREFILLED", "PFS", "INFUSION"), re.compile(r"حقن|امبول|أمبول|فيال"), ("inj", "amp", "vial", "injection")),
    ("ألبان وأغذية أطفال", W("MILK", "FORMULA", "BEBELAC", "SIMILAC", "APTAMIL", "BIOMIL", "NOVALAC", "CERELAC", "NAN",
                             "PROSOBEE", "ISOMIL", "ENFAMIL", "HIPP", "GUIGOZ", "NUTRILON", "PEDIASURE", "NESTOGEN"),
     re.compile(r"لبن|ألبان|البان|حليب|سيريلاك"), ()),
    ("مستلزمات طبية", W("SYRINGE", "SYRINGES", "GAUZE", "BANDAGE", "PLASTER", "CANNULA", "CATHETER", "GLOVES", "GLOVE",
                         "MASK", "MASKS", "LANCET", "LANCETS", "THERMOMETER", "COTTON", "STRIPS", "TEST", "NEEDLE", "NEEDLES",
                         "URINE BAG", "ELASTIC", "CREPE", "SPLINT", "NEBULIZER", "GLUCOMETER"),
     re.compile(r"سرنجة|شاش|رباط|بلاستر|قسطرة|كانيولا|جوانتي|كمامة|ترمومتر|قطن|شرائط|جهاز"), ()),
    ("عناية واستعمال شخصي", W("SHAMPOO", "CONDITIONER", "SOAP", "WASH", "DEODORANT", "DEO", "TOOTHPASTE", "TOOTHBRUSH",
                               "MOUTHWASH", "DIAPER", "DIAPERS", "PADS", "WIPES", "RAZOR", "SANITARY", "FEMININE", "SPRAY DEO",
                               "BODY", "HAIR"),
     re.compile(r"شامبو|صابون|غسول|مزيل|معجون|فرشة|حفاض|بامبرز|فوط|مناديل"), ()),
    ("مستحضرات تجميل", W("SUNBLOCK", "SUNSCREEN", "SPF", "SERUM", "MAKEUP", "LIPSTICK", "MASCARA", "FOUNDATION",
                          "MOISTURIZER", "MOISTURISING", "WHITENING", "ANTI AGING", "ANTI-AGING", "NAIL"),
     re.compile(r"صن بلوك|واقي شمس|سيروم|ميكب|مكياج|روج|مرطب|تفتيح"), ()),
    ("أدوية", W("TAB", "TABS", "TABLET", "TABLETS", "CAP", "CAPS", "CAPSULE", "CAPSULES", "SYRUP", "SYR", "SUSP",
               "SUSPENSION", "DROPS", "DROP", "SUPP", "SUPPOSITORY", "SUPPOSITORIES", "SACHET", "SACHETS", "EFF",
               "EFFERVESCENT", "ORAL", "MG", "MCG", "GM", "ML"),
     re.compile(r"أقراص|اقراص|قرص|كبسول|شراب|نقط|لبوس|فوار|أكياس"), ("tab", "cap", "syrup", "susp", "drops", "supp")),
]


def suggest(db: Database, ctx: Context, show: int = 300) -> dict:
    ctx.require("items.write")
    cats = {r["name"]: dict(r) for r in db.all("SELECT id, name, no_return, mark FROM categories WHERE org_id=? AND is_active=1",
                                                (ctx.org_id,))}
    groups: dict[str, list] = {}
    left = 0
    for it in db.all("SELECT id, name_en, name_ar, dosage_form FROM items WHERE org_id=? AND status='active' AND category_id IS NULL",
                     (ctx.org_id,)):
        en, ar, form = (it["name_en"] or "").upper(), it["name_ar"] or "", (it["dosage_form"] or "").lower()
        hit = None
        for name, pe, pa, forms in RULES:
            if name not in cats:
                continue
            if pe.search(en) or (ar and pa.search(ar)) or (form and form in forms):
                hit = name
                break
        if not hit:
            left += 1
            continue
        groups.setdefault(hit, []).append({"id": it["id"], "name_en": it["name_en"], "name_ar": it["name_ar"]})
    # every id (to apply), but only the first `show` names to review on screen (light on weak computers)
    out = [{"category_id": cats[n]["id"], "category": n, "no_return": cats[n]["no_return"], "mark": cats[n]["mark"],
            "count": len(groups[n]), "ids": [x["id"] for x in groups[n]], "items": groups[n][:show]}
           for n, *_ in RULES if n in groups]
    return {"groups": out, "unmatched": left}
