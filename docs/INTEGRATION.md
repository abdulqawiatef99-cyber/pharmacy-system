# Integration with the pharmacy program (future, not built)

Version 1 of the site is standalone. It reads only `data/site.json` and has no connection to the
pharmacy program. This note records how the program could feed the site later, so neither side has
to be redesigned. **Nothing here changes the program**; each step below needs the owner's approval.

Related program ideas (in the program repository, `docs/FUTURE_REQUIREMENTS.md`):

- **F28** (on `develop`): pharmacy website or app. Simplest first step: one page with all the links,
  and one receipt QR code pointing to it. Online ordering of medicines needs a legal review first;
  hosting and a domain cost money; showing stock from the program needs a server or sync.
- **F21** (on `main`; being renumbered **F31** by program issue #104): patient portal by QR code
  — ask whether an item is available (yes/no only, never the quantity), or send a prescription photo
  that reaches the pharmacist as a draft for review.

## 1. What the feed contains

The contract is `data/catalog.schema.json` (JSON Schema 2020-12):

```json
{ "schema_version": 1, "generated_at": "2026-11-11T09:00:00Z",
  "items": [ { "name": "<public name>", "category": "<public category>", "available": true } ] }
```

Only three fields per item, and the schema forbids any other key. Never exported:
quantities, prices, costs, discounts, batches, expiry dates, suppliers, barcodes or internal ids,
customers, patients, prescriptions, sales, or anything that lets someone infer stock levels.
`available` is a boolean computed by the program from its own stock function
(after reservations, see F8/F9), never a number.

Which items appear is an explicit allow-list kept by the owner in the program (a "publish on site"
flag per item or per category). Default is **not published**. Before any item list goes online the
owner must confirm, with legal advice, which items may be listed publicly at all (prescription-only
medicines and medicine advertising are restricted in Egypt). Version 1 lists no items.

## 2. Two ways to publish it

| | A) Exported file (recommended first) | B) Live API |
|---|---|---|
| How | The program writes `catalog.json` on a schedule (e.g. every 30 min during opening hours) and uploads it next to the site, or the owner exports it by hand | The site calls an endpoint on a server that has a copy of the program's data |
| Server needed | No, a static host is enough | Yes, an always-on server reachable from the internet |
| Cost | 0 on GitHub Pages / similar | Monthly hosting (see the program's `docs/COSTS.md`); owner approval required |
| Freshness | Minutes old; the site shows `generated_at` and hides the list if older than e.g. 2 hours | Live |
| Risk | Very low: a public static file, no way in to the pharmacy PC | The pharmacy data becomes reachable over the network; needs auth, TLS, rate limits, updates |
| Fits | F28 first step, F21 "available yes/no" | F21 with drafts from customers, F20/F17 multi-pharmacy |

Option A keeps the pharmacy PC outbound-only: it pushes a file and accepts no incoming connections.
To upload from the PC it needs a write token for the site repository only (a fine-grained token
limited to that one repository and to contents). The token is a secret kept in the program's
settings, never in the site.

Option B is the same decision the program already faces for remote updates and multi-pharmacy
(F13 / F17 / F20: "server or no server"). It should be decided once for all of them, not separately
for the site.

## 3. How the site would read it (when approved)

1. Add `data/catalog.json` (or fetch from the API URL kept in `data/site.json`).
2. `assets/app.js` fetches it, validates `schema_version == 1`, hides the section if missing,
   invalid or stale, and shows a search box with "available / not available" only.
3. `tools/check_site.py` gains a check that the file matches the schema.

## 4. Privacy rules (apply to every future step)

- The site never uploads, stores or forwards customer or health data. Prescription photos go from
  the customer's phone to the pharmacy over WhatsApp only (ADR-016: the program receives such
  messages through an inbox, it is not wired to WhatsApp directly).
- Any future form that sends a prescription or an order to the program counts as health data:
  explicit consent, a fixed retention period, pharmacist review before any action, and no AI reading
  of prescriptions without that review (as recorded for F21).
- No analytics, trackers, cookies, third-party fonts or CDNs. If statistics are ever wanted, use
  the host's server-side counts or a self-hosted, cookie-less tool, after owner approval.
- The feed is public by nature: assume anyone can download all of it.

## 5. Receipt QR code (F27)

The program's receipt can already print a QR code to any link set in its settings. Once the owner
chooses hosting, he sets the final site address there (top or bottom QR, with his own caption).
No program change is needed. The address must be final before printing many receipts, because old
receipts keep the old link; a custom domain avoids that if the host changes later.
