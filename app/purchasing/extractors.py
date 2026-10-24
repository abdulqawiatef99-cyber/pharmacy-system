"""Pluggable providers for reading invoice PHOTOS / scanned PDFs.

Excel, CSV and text PDFs never use a provider (deterministic parsing, no cost).
A provider only transcribes; matching, arithmetic and approval stay in our code.

Configure with environment variables (never in the database or repository):
  PHARM_OCR_PROVIDER = anthropic | none        (default: anthropic)
  ANTHROPIC_API_KEY  = ...                     (anthropic only)
  PHARM_AI_MODEL     = claude-sonnet-5-5 | claude-haiku-5-5 | ...

Adding a provider (Google Document AI, Azure Document Intelligence, a local
OCR...) = one class with `name`, `available()` and `extract()` returning an
ExtractedInvoice with per-field sources/confidences; nothing else changes.
"""
from __future__ import annotations

import os
from typing import Protocol

from .importers import ai_vision
from .model import ExtractedInvoice

# USD per million tokens, from the official pricing page (checked 2026-10-24).
# Used only for estimates shown to the owner; real usage is recorded per invoice.
PRICES_USD_PER_MTOK = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.0, 20.0),
}


class Provider(Protocol):
    name: str

    def available(self) -> bool: ...

    def extract(self, data: bytes, mime: str) -> ExtractedInvoice: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str, timeout: int = 90):
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def extract(self, data: bytes, mime: str) -> ExtractedInvoice:
        return ai_vision.extract(self.api_key, self.model, data, mime, self.timeout)


class NoProvider:
    """Explicitly disabled: photos must be typed in or the supplier's file used."""
    name = "none"

    def available(self) -> bool:
        return False

    def extract(self, data: bytes, mime: str) -> ExtractedInvoice:
        raise ai_vision.AIUnavailable("قراءة صور الفواتير غير مفعّلة على هذا الجهاز. استخدم ملف Excel/PDF من "
                                      "المورد أو أدخل الفاتورة يدويًا.")


def from_settings(settings) -> Provider:
    name = os.environ.get("PHARM_OCR_PROVIDER", "anthropic").strip().lower()
    if name == "none":
        return NoProvider()
    if name == "anthropic":
        return AnthropicProvider(settings.anthropic_api_key, settings.ai_model, settings.ai_timeout_s)
    raise ValueError(f"unknown PHARM_OCR_PROVIDER: {name}")


def image_tokens(width: int, height: int, long_edge: int = 2576, max_tokens: int = 4784) -> int:
    """Visual tokens per the vision docs: ceil(w/28) * ceil(h/28) after the
    image is scaled to the model's long-edge / token limit."""
    import math
    scale = min(1.0, long_edge / max(width, height))
    w, h = width * scale, height * scale
    t = math.ceil(w / 28) * math.ceil(h / 28)
    while t > max_tokens:
        scale *= 0.98
        w, h = width * scale, height * scale
        t = math.ceil(w / 28) * math.ceil(h / 28)
    return t


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = PRICES_USD_PER_MTOK.get(model)
    if not p:
        return None
    return round(input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1], 5)
