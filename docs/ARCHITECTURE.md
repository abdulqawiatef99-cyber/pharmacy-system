# Architecture

## 1. Deployment shape
```
 [Pharmacy PC (Windows)]                         [Later: central cloud]
  manage.py serve  ── Starlette app ── SQLite      PostgreSQL + same app code
       ▲   ▲                                         ▲   (multi-tenant SaaS,
       │   └─ phones/tablets on the shop Wi-Fi       │    sync from nodes,
  browser (PWA)    (same app, installed as PWA)      │    supplier/EDA feeds)
                                                     └── sync (uuid7 ids, outbox) — Phase 9
```
- Phase 1–8: everything runs on the pharmacy PC, works with no internet (except AI invoice
  photo reading and external integrations).
- Phones on the shop LAN reach the PC (`serve --host 0.0.0.0`). **Requires HTTPS for camera
  access and secure cookies** — plan: local reverse proxy with a certificate (Caddy) or a
  cloud tunnel; documented as known gap.

## 2. Layers (strict)
| Layer | Where | Rule |
|---|---|---|
| UI | `app/web` | No business rules; shows what the API returns |
| HTTP | `app/api` | Parse request → call service → map errors. No SQL |
| Services | `app/<module>/service.py` | All rules, permission checks (`ctx.require`), tenant filters, audit |
| Pure logic | `normalize.py`, `matching.py`, `validation.py`, `model.py` | Deterministic, no I/O, unit-tested |
| Data | `app/db.py`, `migrations/*.sql` | Transactions, constraints, append-only triggers |

Every service call receives a `Context(user_id, org_id, branch_id, permissions)`. There is no
global "current user". Tests call services directly with different contexts to prove isolation.

## 3. Database (current)
Full DDL is the migrations folder; summary:

**core** — `organizations(org_type: pharmacy|warehouse|company|platform)`, `branches`, `users`,
`roles(permissions JSON, per org)`, `memberships(user↔org, role, optional branch)`,
`sessions(token_hash)`, `audit_log` (append-only), `org_entitlements`, `usage_counters`, `org_settings`.

**catalog** — `items` (name_en/name_ar, normalized key, strength_norm, dosage_form, pack_size =
base units per pack, public_price per pack, is_drug/is_imported nullable = unknown, dedupe_key
UNIQUE), `item_barcodes` (UNIQUE per org), `item_aliases` (learned spellings, per supplier),
`supplier_item_codes`, `item_price_history`, `suppliers` (base discounts local/imported).

**inventory** — `batches(item, batch_no, expiry_date, expiry_precision, public_price)`,
`stock_movements` (append-only ledger, qty in base units, unit_cost decimal),
`stock_balances` (cache), `item_costs` (moving average).

**purchasing** — `source_files` (sha256, stored under `data/uploads/<org>/<sha>`),
`purchase_invoices(status draft|approved|cancelled, version, declared/computed totals,
extra_discount/extra_tax, acknowledged_issues, raw_extraction)`,
`purchase_invoice_lines(raw fields + item_id + match_status + field_sources/field_confidence)`.
Partial UNIQUE index: one live invoice per (org, supplier, invoice_no).

### Created after the core (migrations 0004–0008)
- **0004 data import**: `import_runs` (dry-run report, status, applied once per file), `import_rows`.
- **0005 safety**: `knowledge_sources`, `active_ingredients`, `item_ingredients` (verified flag),
  `safety_rules` (versioned, draft→approved→disabled, supersedes), `safety_events` (append-only).
- **0006 recalls**: `regulatory_notices`, `notice_items(batch_numbers)`, `recall_matches`.
- **0007 counting**: `count_sessions`, `count_entries` (one live entry per item/batch/location;
  `ledger_seq` = ledger position at count time).
- **0008 procurement**: `consumption_history`; suppliers gain `lead_time_days`, `order_cycle_days`.

### Still planned (designed to reuse the above)
- Sales: `sales`, `sale_lines(batch_id)`, `payments`, `customers`, `customer_ledger` — sale lines
  call `SafetyEngine.check/resolve` before posting `sale` movements (FEFO).
- Integrations: `integration_accounts(org, provider, encrypted credentials ref)`,
  `outbox(event, payload, status)` for reliable delivery to Talabat/Instashop/app.
- Sync (cloud phase): per-table change log keyed by uuid7.

## 4. Purchase invoice pipeline
```
upload → sniff by magic bytes → duplicate-file check (sha256)
  → Excel/CSV/text-PDF: table locator (Arabic/English header synonyms) ─┐
  → photo/scanned PDF: entitlement+quota → vision model (reads only)   ─┤
                                                                         ▼
  ExtractedInvoice (every field: value + source + confidence)
  → supplier resolution → matching per line → derive arithmetic (tagged inferred)
  → DRAFT saved (raw extraction kept verbatim)
  → review UI: edits audited, optimistic versioning, confirm low-confidence fields
  → validation (deterministic): unmatched, qty, price consistency, line totals,
    invoice total vs printed total, expiry, duplicates, public price changes
  → APPROVE (permission): batches, ledger movements, avg cost, batch/public price,
    learn aliases/supplier codes from human confirmations — one transaction
  → CANCEL approved: reversal movements (fails if stock already sold)
```

## 5. Peripherals — status and plan
| Device | Path | Status |
|---|---|---|
| USB/Bluetooth barcode scanner (keyboard mode) | Types into focused input | Works with any input field; dedicated scan flows in Inventory/Sales phases |
| Phone camera as scanner | Browser `BarcodeDetector` (Chrome Android) with a JS fallback | Not built yet; needs HTTPS on LAN |
| Thermal receipt printer (ESC/POS) | Browser can't send raw ESC/POS reliably → small local print agent on the PC (USB/network), or print via OS driver as HTML | Not built (Sales phase); must be tested on the actual printer model |
| Cash drawer | Kicked by the receipt printer (ESC/POS pulse) | Depends on printer agent |
| Label/barcode printer | Same agent (ZPL/TSPL depending on model) | Not built; needs model |
| A4 printer | Browser print of HTML reports | Works generically |
| Handheld inventory terminals | Android browser (PWA) or CSV upload | Inventory phase |

## 6. Integrations — verified facts and plan
| Target | Fact (2026-10-24) | Plan |
|---|---|---|
| Talabat | Delivery Hero Partner API (order transmission + assortment) covers Talabat Egypt; token from Vendor Portal for partners | Phase 8: outbox + adapter; needs a partner account |
| Instashop | No public API found | Ask Instashop; fallback = catalog/price export file |
| Pharmacy mobile app | Same REST API with Bearer tokens | Phase 8 |
| EDA recalls | Public web page of recall/falsified-product circulars, no API | Phase 6: fetch page on schedule, extract batch numbers, human-confirm, match against our batches |
| EDA drug database (EDDB) | Search with captcha, no API/export | Manual lookup only; request data officially |
| i'Supply / distributors | Unknown APIs | Owner to ask their customer service; Excel import works now |
| E-Stock (Modern Soft) | Excel export available | Phase 2 migration tool (needs a real export sample) |

## 7. AI usage map
| Use | Model role | Guardrail |
|---|---|---|
| Invoice photo reading | Transcribe printed values | Capped confidence, human review, deterministic totals |
| (future) Explaining safety alerts in Arabic | Wording only | Facts come from the rule engine; explanation shows source |
| (future) Demand forecasting | Statistical models, not LLM | Shown with confidence; never auto-orders |
| Development | Writing code/tests/docs | Commit → tests → review; never touches production DB |
