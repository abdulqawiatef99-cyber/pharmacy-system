# HTTP API (v0 — for the PWA, the future pharmacy mobile app and integrations)

Base: `http://<pharmacy-pc>:8000`. JSON in/out (UTF-8). Money = **integer piasters**,
percent = **basis points** (12.5% = 1250), quantities = **base units** unless the field says packs.

## Authentication
| | |
|---|---|
| `POST /api/auth/login` | `{username, password, org_id?, token_in_body?}` → sets HttpOnly cookie; with `token_in_body: true` also returns `token` (mobile/integrations) |
| Browser | cookie + header `X-Requested-With: pharm` on every non-GET request (CSRF) |
| Mobile / integration | `Authorization: Bearer <token>` (no CSRF header needed) |
| `POST /api/auth/logout`, `GET /api/me` | |

## Errors
Non-2xx → `{"error": {"code", "message" (Arabic, for display), "details"}}`.
Common codes: `unauthorized`, `session_expired`, `forbidden`, `not_found`, `validation_failed`,
`conflict`, `stale_version` (reload and retry), `insufficient_stock`, `safety_unresolved`,
`duplicate_file`, `duplicate_invoice`, `rate_limited`.
Optimistic locking: editing endpoints take the `version` you last read.

## Endpoints
**Catalog** — `GET /api/items?q=&limit=` (name AR/EN, code or barcode) · `POST /api/items`
`{item:{…}, barcodes:[], force?}` (409 `possible_duplicate` lists look-alikes) · `GET|PATCH /api/items/{id}` ·
`GET|POST /api/suppliers` · `GET /api/export/items.xlsx`

**Purchases** — `POST /api/purchases/import` (multipart `file`, `supplier_id?`) · `POST /api/purchases`
(manual) · `GET /api/purchases?status=` · `GET /api/purchases/{id}` → `{invoice, lines, issues, can_approve}` ·
`PATCH /api/purchases/{id}` `{changes, version}` · `POST …/lines` · `PATCH|DELETE …/lines/{line_id}`
(`changes` may include `confirm_fields:[…]`) · `POST …/lines/{line_id}/new-item` ·
`POST …/acknowledge` `{code, reason, line_id?, version}` · `POST …/approve {version}` · `POST …/cancel {reason, version}` ·
`GET /api/files/{id}` (original document)

**Data import** — `POST /api/imports` (multipart `file`, `kind=items|opening_stock`, `qty_unit=pack|base`,
`mapping?` JSON, `header_row?`) → dry-run report · `GET /api/imports/{id}?action=` ·
`POST …/decide {row_id, decision: create|skip|link:<item_id>}` · `POST …/apply` · `POST …/discard`

**Sales** — `POST /api/sales {customer_id?}` · `GET /api/sales/{id}` (draft shows FEFO allocations and
prices) · `POST …/lines {item_id, packs?, units?, discount_bp?}` · `DELETE …/lines/{line_id}` ·
`POST …/check` → safety findings · `POST …/complete {paid_cash, paid_card, on_credit, safety_decisions:{index: reason}, version}` ·
`POST …/return {lines:{line_id: qty}, refund_method, reason}` · `POST …/void {reason}` ·
`GET /api/sales/summary?day=` · `GET|POST /api/customers` · `POST /api/customers/{id}/payments {amount}` ·
`GET /receipt/{id}` (printable 80mm HTML)

**Safety** — `POST /api/safety/check {lines:[{item_id, base_qty?, partial_pack?, tablet_split?}]}` ·
`PUT /api/items/{id}/ingredients {ingredients:[{name, strength}], source_id?, verify?}` ·
`GET|POST /api/safety/rules` · `POST /api/safety/rules/{id}/approve {reason?}` · `POST …/disable {reason}` ·
`POST /api/safety/sources`
Finding: `{code, severity: critical|high|moderate|low|info|insufficient, message, item_ids, source, basis, requires: block|override|acknowledge|none}`.
An empty interaction result is never returned silently: `interaction_check_unavailable` is included
while no approved interaction data exists.

**Inventory count** — `GET|POST /api/counts` · `POST /api/counts/{id}/entries {item_id, qty, batch_id?, location?, mode: set|add, recount?}` ·
`GET …/variances` · `POST …/approve {zero_uncounted?, reason?}` · `POST …/cancel`

**Procurement** — `GET /api/procurement/suggestions?cover_days&lead_days&safety_days&window_days` (+ `.xlsx`) ·
`GET /api/procurement/health` · `GET /api/procurement/suppliers/{item_id}` ·
`POST /api/procurement/offer {item_id, packs, discount_pct, expiry_date?, cash_available?}` (whole packs; percent and pounds with at most two decimals; answers `cost` in piasters) ·
`POST /api/procurement/consumption` (multipart, `period_start`, `period_end`, `apply=1` to save)

**Recalls** — `POST /api/recalls/extract {text}` → proposed batch numbers · `POST /api/recalls {title, items:[{product_name, batch_numbers}], notice_no?, notice_date?}` ·
`GET /api/recalls/alerts?status=pending|all` · `POST /api/recalls/alerts/{id} {status: confirmed|not_ours|handled, note}`

**Similar items and alternatives** — `GET /api/items/{id}/alternatives` → `{similar, other_strength, alternatives, groups, missing}`
(similar = same `ingredient_key` + `strength_norm` + dosage-form group; alternatives only from owner groups; each list ranked:
in stock → expiring within 180 days → dearer → higher margin; `margin_bp` only with `costs.read`) ·
`GET /api/ingredients` · `GET|POST /api/therapeutic-groups`, `PUT|DELETE /api/therapeutic-groups/{id}`
`{name, note?, is_active?, members:[{kind:"ingredient", label} | {kind:"item", ref}]}` (`items.groups`) ·
`GET /api/pos/quick` → pinned items then the last 30 days' best sellers (24 max) · `POST /api/items/flags {item_ids, quick_pin}`.
Adding an item already on a draft sale (same discount) increases that line instead of adding a second one.

**Owner reports** — `GET /api/reports/owner/{kind}?from&to` (local days, up to 400 days, first 5000 rows) with kind:
`staff` · `customers` · `shifts` (`cash.see_expected`) · `returns` · `discounts` · `prices` · `units` · `unsold` (+`inventory.read`).
All need `sales.totals`; cost values only with `costs.read`; drawer differences only with `cash.see_expected`.

**Sales invoices report** — `GET /api/reports/invoices?from=YYYY-MM-DDTHH:MM&to=…&types=cash,card,transfer,credit,delivery,contract,returned,discounted|voided|draft&q&item&user&lines=1`
(`sales.totals`; any of the chosen kinds; `q` = invoice number, or part of a customer's name/phone (≥4 digits);
per invoice: item vs contract discount, after-return net, payments, `profit` for `costs.read`; first 3000; voided/held not in totals).

**Screen customization** — `GET /api/settings/ui` (also in `/api/me` as `ui`), `PUT` (`settings.manage`):
`{nav_main: [screen…≤8], home: [screen…≤8], shortcuts: {"1".."9": screen}, columns: {invoices|items: [hidden column index]}, reset?}`.
Stored as one JSON value in `org_settings` (key `ui`); audit `settings.ui`. Per person: `PUT /api/me/prefs {help: on|off}`.

**Label printers** — `PUT /api/settings/receipt {label_lang: browser|tspl|zpl, label_target: "tcp:IP:PORT"|"win:NAME", label_dpi: 203|300, label_gap: 0..10, label_shift: -10..10 (mm, + = lower; TSPL `SHIFT` / ZPL `^LT`, #100)}`
(`settings.manage`; never a file path from the screen) · `POST /api/print/labels {items:[{id, count}], size?}` (`items.read`, ≤500 labels):
each label drawn as a 1-bit image and sent as TSPL `BITMAP` or ZPL `^GFA`; `labels_in_browser` when not set up, `print_failed` → browser page.
`POST /api/print/label-test {label_lang?, label_target?, label_size?, label_gap?, label_shift?, label_dpi?, label_fields?}` (`settings.manage`, #100):
3 sample labels with the values on the screen, nothing saved; audited `print.label_test`.

## Stability
v0: may change until the first external client ships. Before the mobile app, endpoints will be frozen
as `/api/v1/…` and an OpenAPI file generated.
