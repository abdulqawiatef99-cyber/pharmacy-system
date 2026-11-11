#!/usr/bin/env python3
"""Quality check for the static site. Standard library only.

  python3 tools/check_site.py

Fails (exit 1) when:
  - data/site.json is missing a key that index.html or assets/app.js uses, or the
    Arabic and English string sets differ;
  - a link or value in data/site.json is malformed (WhatsApp, maps, phone, URLs, colours, hours);
  - any HTML file does not parse cleanly (html.parser + tag balance, duplicate ids, img alt);
  - a page loads anything from another host (no CDNs, fonts or trackers allowed);
  - the generated SEO / JSON-LD / no-JS blocks in index.html are out of sync (run tools/sync_head.py);
  - data/catalog.schema.json allows more than name, category and available per item;
  - the site is larger than the size budget.
Prints the TODO_OWNER values that are still open (these do not fail the check).
"""
import json
import pathlib
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sitegen import INDEX, ROOT, is_todo, json_ld, load, render_index  # noqa: E402

SIZE_BUDGET = 60 * 1024  # bytes for everything a visitor downloads
SITE_FILES = ["index.html", "assets/app.js", "assets/style.css", "assets/icon.svg", "data/site.json"]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "path"}
errors = []


def err(msg):
    errors.append(msg)


class Checker(HTMLParser):
    def __init__(self, name):
        super().__init__(convert_charrefs=True)
        self.name, self.stack, self.ids = name, [], set()
        self.attrs_seen = []  # (tag, attrs)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.attrs_seen.append((tag, a))
        if "id" in a:
            if a["id"] in self.ids:
                err(f"{self.name}: duplicate id {a['id']!r}")
            self.ids.add(a["id"])
        if tag == "img" and "alt" not in a:
            err(f"{self.name}: <img> without alt")
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_startendtag(self, tag, attrs):
        self.attrs_seen.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            where = self.stack[-1] if self.stack else ("nothing", self.getpos())
            err(f"{self.name}:{self.getpos()[0]}: </{tag}> closes <{where[0]}> opened at line {where[1][0]}")
            for k in range(len(self.stack) - 1, -1, -1):
                if self.stack[k][0] == tag:
                    del self.stack[k:]
                    break
            return
        self.stack.pop()

    def close(self):
        super().close()
        for tag, pos in self.stack:
            err(f"{self.name}:{pos[0]}: <{tag}> never closed")


def lookup(data, path):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def https_url(v, hosts=None):
    u = urlparse(v)
    ok = u.scheme == "https" and bool(u.netloc) and " " not in v
    return ok and (hosts is None or u.netloc in hosts)


def check_data(data):
    for k in ("todo_marker", "pharmacy", "hours", "services", "delivery", "returns", "strings"):
        if k not in data:
            err(f"site.json: missing top-level key {k!r}")
    ar, en = data["strings"]["ar"], data["strings"]["en"]
    for k in sorted(set(ar) ^ set(en)):
        err(f"site.json: string {k!r} exists in only one language")
    ph = data["pharmacy"]
    m = re.fullmatch(r"https://wa\.me/(20\d{10})", ph["whatsapp_link"])
    if not m:
        err("site.json: pharmacy.whatsapp_link must look like https://wa.me/20XXXXXXXXXX")
    elif "+" + m.group(1) != ph["mobile_e164"]:
        err("site.json: whatsapp_link and mobile_e164 are different numbers")
    if not re.fullmatch(r"01\d{9}", ph["whatsapp_display"]) or "+20" + ph["whatsapp_display"][1:] != ph["mobile_e164"]:
        err("site.json: whatsapp_display does not match mobile_e164")
    if not https_url(ph["maps_link"], {"maps.app.goo.gl", "www.google.com", "maps.google.com", "goo.gl"}):
        err("site.json: pharmacy.maps_link is not a Google Maps https link")
    if not is_todo(data, ph["landline_e164"]) and not re.fullmatch(r"\+20\d{8,10}", ph["landline_e164"]):
        err("site.json: landline_e164 must look like +2048XXXXXXX")
    if is_todo(data, ph["landline_e164"]) != is_todo(data, ph["landline_display"]):
        err("site.json: fill landline_display and landline_e164 together")
    for key in ("site_url",):
        if not is_todo(data, ph[key]) and not https_url(ph[key]):
            err(f"site.json: pharmacy.{key} must be an https:// address")
    for k, v in ph["social"].items():
        if not is_todo(data, v) and not https_url(v):
            err(f"site.json: pharmacy.social.{k} must be an https:// address")
    if not is_todo(data, ph["logo"]):
        if urlparse(ph["logo"]).scheme or not (ROOT / ph["logo"]).is_file():
            err("site.json: pharmacy.logo must be a file inside the site, e.g. assets/logo.png")
    for k, v in ph["colors"].items():
        if not is_todo(data, v) and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            err(f"site.json: pharmacy.colors.{k} must be like #1a7f45")
    if not is_todo(data, data["hours"]):
        if not isinstance(data["hours"], list):
            err("site.json: hours must be TODO_OWNER or a list")
        else:
            for i, h in enumerate(data["hours"]):
                if not h.get("days") or any(f"day.{d}" not in ar for d in h["days"]):
                    err(f"site.json: hours[{i}].days must use Sa Su Mo Tu We Th Fr")
                for f in ("opens", "closes"):
                    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(h.get(f, ""))):
                        err(f"site.json: hours[{i}].{f} must be HH:MM (24h)")
    if not is_todo(data, data["services"]):
        if not isinstance(data["services"], list) or any(not (s.get("ar") and s.get("en")) for s in data["services"]):
            err("site.json: services must be a list of {\"ar\": ..., \"en\": ...}")
    fee = data["delivery"]["fee_egp"]
    if not is_todo(data, fee) and not (isinstance(fee, (int, float)) and fee >= 0):
        err("site.json: delivery.fee_egp must be a number (0 = free)")
    if not isinstance(data["returns"].get("days"), int):
        err("site.json: returns.days must be a whole number")


def check_keys(data, parser):
    ar = data["strings"]["ar"]
    js = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
    used_t = set(re.findall(r'\bt\("([^"]+)"\)', js))
    used_p = set(re.findall(r'\bp\("([^"]+)"\)', js))
    used_local = set(re.findall(r'\blocal\("([^"]+)"\)', js))
    for tag, a in parser.attrs_seen:
        for attr in ("data-t", "data-t-label"):
            if attr in a:
                used_t.add(a[attr])
        if "data-p" in a:
            used_local.add(a["data-p"])
    for k in sorted(used_t):
        if k not in ar:
            err(f"site.json: strings.ar/en missing {k!r} (used by the page)")
    for k in sorted(used_p):
        if not lookup(data, k)[1]:
            err(f"site.json: missing {k!r} (used by assets/app.js)")
    for f in sorted(used_local):
        for lang in ("ar", "en"):
            if not lookup(data, f"pharmacy.{f}_{lang}")[1]:
                err(f"site.json: missing pharmacy.{f}_{lang}")
    for k in ("delivery.area_ar", "delivery.area_en", "pharmacy.social"):
        if not lookup(data, k)[1]:
            err(f"site.json: missing {k!r}")
    for d in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"):
        if f"day.{d}" not in ar:
            err(f"site.json: missing strings day.{d}")
    for el in re.findall(r'\$\("([^"]+)"\)', js):
        if el not in parser.ids:
            err(f"assets/app.js uses #{el} which is not in index.html")


def check_html_links(name, parser):
    for tag, a in parser.attrs_seen:
        for attr in ("href", "src"):
            v = a.get(attr)
            if v is None:
                continue
            u = urlparse(v)
            loads = tag in ("script", "img", "iframe", "source") or (tag == "link" and a.get("rel") != "canonical")
            if loads and (u.scheme or u.netloc):
                err(f"{name}: <{tag} {attr}> loads from another host: {v}")
            elif u.scheme in ("http", "https") and not https_url(v):
                err(f"{name}: malformed link {v}")
            elif u.scheme == "" and not v.startswith("#") and not (ROOT / u.path).is_file():
                err(f"{name}: {attr}={v!r} points to a missing file")
            elif v.startswith("#") and len(v) > 1 and v[1:] not in parser.ids:
                err(f"{name}: link to missing anchor {v}")
        if tag == "a" and "href" not in a and "id" not in a:
            err(f"{name}: <a> without href")
        if tag == "button" and a.get("type") != "button":
            err(f"{name}: <button> needs type=\"button\"")
        if tag == "a" and a.get("target") == "_blank" and "noopener" not in a.get("rel", ""):
            err(f"{name}: target=_blank link without rel=noopener")


def check_no_external_assets():
    css = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
    js = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
    if re.search(r"@import|url\(\s*['\"]?https?:", css):
        err("assets/style.css loads something from another host")
    if re.search(r"https?://", js):
        err("assets/app.js contains an external address (all addresses belong in data/site.json)")
    for bad in ("XMLHttpRequest", "FormData", "sendBeacon", "document.cookie", "<input", "type=\"file\""):
        if bad in js:
            err(f"assets/app.js uses {bad}: the site must not collect or upload anything")


def check_sync(data, text):
    if render_index(text, data) != text:
        err("index.html SEO/JSON-LD/no-JS blocks are out of date: run python3 tools/sync_head.py")
    m = re.search(r'<script type="application/ld\+json" id="ld">\n(.*?)\n</script>', text, re.S)
    if not m:
        err("index.html: JSON-LD block missing")
        return
    ld = json.loads(m.group(1).replace("<\\/", "</"))
    if ld != json_ld(data) or ld.get("@type") != "Pharmacy":
        err("index.html: JSON-LD does not match data/site.json")


def check_catalog_schema():
    path = ROOT / "data" / "catalog.schema.json"
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        err(f"data/catalog.schema.json: {exc}")
        return
    item = s.get("$defs", {}).get("item", {})
    props = set(item.get("properties", {}))
    if props != {"name", "category", "available"} or item.get("additionalProperties") is not False:
        err("data/catalog.schema.json: items must allow exactly name, category, available")
    if s.get("additionalProperties") is not False:
        err("data/catalog.schema.json: top level must forbid extra keys")


def todo_list(data, node, prefix=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in ("strings", "todo_marker"):
                out += todo_list(data, v, f"{prefix}{k}.")
    elif node == data["todo_marker"]:
        out.append(prefix.rstrip("."))
    return out


def main():
    data = load()
    check_data(data)
    pages = sorted(ROOT.rglob("*.html"))
    index_parser = None
    for page in pages:
        name = page.relative_to(ROOT).as_posix()
        text = page.read_text(encoding="utf-8")
        p = Checker(name)
        p.feed(text)
        p.close()
        check_html_links(name, p)
        if page == INDEX:
            index_parser = p
            check_sync(data, text)
    check_keys(data, index_parser)
    check_no_external_assets()
    check_catalog_schema()
    size = sum((ROOT / f).stat().st_size for f in SITE_FILES)
    if size > SIZE_BUDGET:
        err(f"site is {size} bytes, budget is {SIZE_BUDGET}")

    todos = todo_list(data, data)
    print(f"HTML files parsed: {len(pages)}; site size: {size} bytes (budget {SIZE_BUDGET})")
    print("Open TODO_OWNER values:" if todos else "No TODO_OWNER values left.")
    for t in todos:
        print("  -", t)
    if errors:
        print(f"\nFAILED ({len(errors)}):")
        for e in errors:
            print("  x", e)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
