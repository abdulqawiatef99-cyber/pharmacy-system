"""Invoice photo / scanned-PDF extraction with a vision model (Anthropic API).

STATUS: implemented, response parsing unit-tested with a hand-written fixture.
NOT yet tested against the live API or real Egyptian invoices — needs an
ANTHROPIC_API_KEY and sample invoices (see docs/PROGRESS.md).

Safety rules:
- Only the invoice image is sent. No patient/customer data, no catalog dump.
- The model only *reads*; it never decides matches, totals or stock.
- Every value it returns is tagged source='ai' and its confidence is capped at
  0.9, so nothing it reads is treated as certain. Low-confidence fields block
  approval until a person confirms them, and totals are re-checked
  deterministically by validation.py.
"""
from __future__ import annotations

import base64
import io
import json
import re
from ...core.errors import DomainError, ValidationFailed
from ..model import (ExtractedInvoice, ExtractedLine, parse_date, parse_decimal, parse_expiry,
                     parse_int, parse_percent)

API_URL = "https://api.anthropic.com/v1/messages"
AI_CONFIDENCE_CAP = 0.9
MAX_EDGE = 2200

PROMPT = """You are reading a supplier PURCHASE INVOICE from an Egyptian pharmacy distributor.
The text may be Arabic, English or both. Transcribe what is PRINTED. Do not calculate,
correct or guess values. If a value is not visible or not readable, use null.

Return ONLY one JSON object, no prose, with this shape:
{
  "supplier_name": str|null, "invoice_no": str|null, "invoice_date": str|null,
  "declared_total": number|null,          // final net payable printed on the invoice
  "extra_discount": number|null,          // invoice-level discount NOT shown per line
  "extra_tax": number|null,               // invoice-level tax NOT shown per line
  "header_confidence": {"supplier_name":0-1, "invoice_no":0-1, "invoice_date":0-1, "declared_total":0-1},
  "lines": [{
     "raw_name": str,                     // item name exactly as printed
     "raw_code": str|null, "raw_barcode": str|null,
     "qty": number|null,                  // paid quantity (packs)
     "bonus_qty": number|null,            // free quantity (bonus)
     "public_price": number|null,         // public / consumer price per pack
     "discount_pct": number|null,         // e.g. 12.5 for 12.5%
     "unit_net_price": number|null,       // price per pack after discount
     "tax_amount": number|null,
     "line_total": number|null,
     "batch_no": str|null,
     "expiry": str|null,                  // as printed, e.g. "05/2027"
     "confidence": {"raw_name":0-1, "raw_code":0-1, "raw_barcode":0-1, "qty":0-1,
                    "bonus_qty":0-1, "public_price":0-1, "discount_pct":0-1,
                    "unit_net_price":0-1, "tax_amount":0-1, "line_total":0-1,
                    "batch_no":0-1, "expiry":0-1}   // give a value for EVERY non-null field
  }],
  "notes": [str]                          // anything unclear: torn, blurred, handwritten edits
}
Confidence is how sure you are that you READ the characters correctly (not whether the
value is sensible). Handwritten corrections or crossed-out numbers must get confidence <= 0.5
and be mentioned in notes."""


class AIUnavailable(DomainError):
    status = 503
    code = "ai_unavailable"


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """Fix phone orientation, flatten to RGB JPEG, cap the long edge (cost and
    latency scale with pixels; 2200px keeps small print legible)."""
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        raise ValidationFailed("تعذر قراءة الصورة") from e
    if min(img.size) < 400:
        raise ValidationFailed("الصورة صغيرة جدًا، صوّر الفاتورة أقرب وبإضاءة أوضح",
                               code="image_too_small")
    img = img.convert("RGB")
    scale = MAX_EDGE / max(img.size)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue(), "image/jpeg"


def build_request(model: str, data: bytes, mime: str) -> dict:
    if mime == "application/pdf":
        block = {"type": "document", "source": {"type": "base64", "media_type": mime,
                                                 "data": base64.b64encode(data).decode()}}
    else:
        data, mime = prepare_image(data)
        block = {"type": "image", "source": {"type": "base64", "media_type": mime,
                                              "data": base64.b64encode(data).decode()}}
    return {"model": model, "max_tokens": 8000, "temperature": 0,
            "messages": [{"role": "user", "content": [block, {"type": "text", "text": PROMPT}]}]}


def call_api(api_key: str | None, payload: dict, timeout: int = 90) -> dict:
    if not api_key:
        raise AIUnavailable("قراءة صور الفواتير تحتاج مفتاح خدمة الذكاء الاصطناعي (ANTHROPIC_API_KEY)"
                            " ولم يتم ضبطه بعد. استخدم ملف Excel أو PDF من المورد، أو أدخل الفاتورة يدويًا.")
    import requests
    try:
        r = requests.post(API_URL, json=payload, timeout=timeout, headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
    except requests.RequestException as e:
        raise AIUnavailable("تعذر الاتصال بخدمة قراءة الفواتير، حاول مرة أخرى أو أدخلها يدويًا") from e
    if r.status_code != 200:
        raise AIUnavailable(f"خدمة قراءة الفواتير رجعت خطأ ({r.status_code})",
                            details={"status": r.status_code})
    return r.json()


def parse_response(resp: dict, model: str | None = None) -> ExtractedInvoice:
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    if resp.get("stop_reason") == "max_tokens":
        raise ValidationFailed("الفاتورة طويلة جدًا للقراءة مرة واحدة؛ صوّرها على أجزاء",
                               code="ai_truncated")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValidationFailed("تعذر فهم نتيجة القراءة الآلية", code="ai_bad_output")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValidationFailed("نتيجة القراءة الآلية غير صالحة", code="ai_bad_output") from e
    return from_json(obj, model or resp.get("model"))


def _conf(c: dict | None, key: str) -> float:
    try:
        v = float((c or {}).get(key, 0.5))
    except (TypeError, ValueError):
        v = 0.5
    return max(0.0, min(v, AI_CONFIDENCE_CAP))


def from_json(obj: dict, model: str | None) -> ExtractedInvoice:
    inv = ExtractedInvoice(method="ai_vision", model=model)
    hc = obj.get("header_confidence") or {}
    inv.supplier_name = (obj.get("supplier_name") or None)
    inv.invoice_no = str(obj["invoice_no"]).strip() if obj.get("invoice_no") else None
    inv.invoice_date = parse_date(obj.get("invoice_date"))
    inv.declared_total = parse_decimal(obj.get("declared_total"))
    inv.extra_discount = parse_decimal(obj.get("extra_discount"))
    inv.extra_tax = parse_decimal(obj.get("extra_tax"))
    for k in ("supplier_name", "invoice_no", "invoice_date", "declared_total"):
        if getattr(inv, k) is not None:
            inv.header_sources[k] = "ai"
            inv.header_confidence[k] = _conf(hc, k)
    for n in obj.get("notes") or []:
        inv.warnings.append(f"ai_note:{str(n)[:200]}")
    for raw in obj.get("lines") or []:
        name = str(raw.get("raw_name") or "").strip()
        if not name:
            continue
        c = raw.get("confidence") or {}
        line = ExtractedLine(raw_name=name)
        line.raw_code = str(raw["raw_code"]).strip() if raw.get("raw_code") else None
        line.raw_barcode = str(raw["raw_barcode"]).strip() if raw.get("raw_barcode") else None
        line.qty = parse_int(raw.get("qty"))
        line.bonus_qty = parse_int(raw.get("bonus_qty"))
        line.public_price = parse_decimal(raw.get("public_price"))
        line.discount_pct = parse_percent(raw.get("discount_pct"))
        line.unit_net_price = parse_decimal(raw.get("unit_net_price"))
        line.tax_amount = parse_decimal(raw.get("tax_amount"))
        line.line_total = parse_decimal(raw.get("line_total"))
        line.batch_no = str(raw["batch_no"]).strip() if raw.get("batch_no") else None
        line.expiry_date, line.expiry_precision = parse_expiry(raw.get("expiry"))
        conf_key = {"expiry_date": "expiry"}
        for f in ("raw_name", "raw_code", "raw_barcode", "qty", "bonus_qty", "public_price",
                  "discount_pct", "unit_net_price", "tax_amount", "line_total", "batch_no",
                  "expiry_date"):
            present_raw = raw.get(conf_key.get(f, f))
            if getattr(line, f) is not None:
                line.sources[f] = "ai"
                line.confidence[f] = _conf(c, conf_key.get(f, f))
            elif present_raw not in (None, ""):
                line.sources[f] = "ai"          # model returned something unparseable
                line.confidence[f] = 0.0
        inv.lines.append(line)
    if not inv.lines:
        inv.warnings.append("no_lines")
    return inv


def extract(api_key: str | None, model: str, data: bytes, mime: str, timeout: int = 90) -> ExtractedInvoice:
    payload = build_request(model, data, mime)
    return parse_response(call_api(api_key, payload, timeout), model)


__all__ = ["extract", "parse_response", "from_json", "prepare_image", "AIUnavailable"]
