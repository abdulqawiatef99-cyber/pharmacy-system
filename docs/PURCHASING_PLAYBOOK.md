# Purchasing Playbook — everything learned (single source of truth)

> Read this file FIRST in every purchasing session, then say the rules back to the owner in 2 lines.
> Last update: 2026-11-05. Older notes: `HANDOFF_F26_ESTOCK.md`, `SMART_ORDER_REPORT.md` §47–§62, `HANDOFF_ACC2.md`.
> If this file and an older note disagree, THIS file wins.

## 1. Working with the owner
- Egyptian Arabic, masculine forms, ALWAYS Arabic (never reply in English). English words only in their own code block.
- Short replies, but every important detail in one line. No lectures, no trivial questions.
- Constructive critic; double/triple-check numbers before replying. Never present a guess as a fact.
- Show a short progress line in chat at each step.
- End each reply with the open reminders until he closes them.
- Warn before heavy usage (long computer use, big scans) and offer the cheaper path (he exports reports himself).
- Never assume business facts (terms, discounts, meanings). Ask once, clearly. Store the answer here.
- Any new rule he states goes into THIS file the same turn (memory alone is not enough; other accounts don't share memory).
- Files for him: xlsx, big bold font, right-to-left. No PDF with Arabic tables (renders broken).

## 2. Golden process for an order (agreed 2026-11-04)
**No action until ALL data is in hand.** Build the order calmly the evening before (evening tele-sales exist), not one hour before cutoff.
1. Collect data (section 3).
2. Run `tools/order_check.py` → `check()` on every candidate line.
3. Live prices for passing lines: i'Supply (all sellers) + Ibn Sina portal (+ Pharma Overseas for VAT).
4. Read today's incoming WhatsApp lists ("وارد") for missing items and bonus offers.
5. `allocate()` → comparison table (per line: Ibn Sina % / Nahda % after −2 / best cash warehouse %) + EGP cost of every consolidation move.
6. Send the table to the owner → wait for approval.
7. Only then: supplier files (item name + qty + polite note only) → send to chat "صيدلية عبد القوي" (he forwards them) or to tele-sales only with his OK on the exact text.

## 3. Data needed before starting
1. E-Stock balances of ALL items, Excel, as of order day.
2. Sales by item since the last export (completes the 12-month history).
3. Purchase invoices since the last export (what arrived).
4. Photos of invoices received but not yet entered (count as stock).
5. Market shortages he knows + items he doesn't want now.
6. Warehouses he deals with + terms (cash/credit, minimum order, tele-sales hours).
7. Kashkool group messages (WhatsApp) since last order.

## 4. Item rules (a line must pass all; failures go to the review sheet with the reason)
- **In stock** → don't order. Include arrived-not-entered invoices.
- **Same product, other pack in stock** → don't order (Romantigra 8 tabs in stock → no 4 tabs; Charcoal 30 caps → no 20 caps; Dermovate cream vs ointment; Doliprane 2 vs 3 strips).
- **Different product despite similar name** when a modifier differs: PLUS / FORTE / XR / SR / RETARD / ZINC / HCT… (Virecta 12 tabs ≈132 vs Virecta PLUS 4 tabs ≈140 — different price, pack, discount). When unsure: compare price + pack, or ask.
- **Slow or single-patient** → ask: < 3 boxes/year, no sale in last 4 months, or one month holds most of the year (Reagila, Cymbatex, Glucoadjust).
- **Market shortage / owner excluded** → skip (4/11: Mebo, Haloperidol, Amtrix ampoule, Iverzine lotion, Zithromax 600, Fortiferrum…).
- **Approval needed**: price ≥ 300 EGP, fridge items, near-expiry, discount below last purchase.
- **Kashkool**: never merge lines ("راني فوار" and "فوار فواكه" are two different drugs). "توفير X" = a customer asked → 1 box of each strength only if balance is zero. Verify unknown names on i'Supply/Ibn Sina before calling them non-drug.
- **Medicines only** for now; supplies/cosmetics wait for Future / Al Mostafa lists.
- Search name quality: confirm brand + strength + pack + public price; never accept a match by price alone (Mebo vs Mebo C, Dozova melatonin vs ashwagandha).

## 5. Discount and supplier rules
- Real discount: Nahda = shown − 2 %; Ibn Khaldun = shown + 1 %; others as shown. **Never add assumed discounts** (the +2.2 % Ibn Sina claim discount mistake of 4/11).
- Ibn Sina claim cash discount (regular 3 / special 2 / other 0.25 %; cheque 2.5 / 1.5): NOT applied until the owner confirms he gets it and wants it counted.
- VAT: some company prices are before 14 % VAT (Ibn Sina 29.8 % on supplements/cosmetics is the typical sign). No rule — check each item (Pharma Overseas product page "Taxes %", or the invoice tax column). Warehouses: no VAT issue.
- Outlier: one seller ≥ 10 points above the others = probably a coding error (Taj Mebo 47 % = Mebo C).
- Bonus math: effective discount = 1 − (pharmacy price × paid ÷ (paid + free)) ÷ public. 10+2 (16.7 %) beats 25+3 (10.7 %). Big bonus quantities only if sales + expiry justify them.
- Cash vs credit: decide in EGP, not %. Show per-line and monthly totals. Small difference (≈300–400 EGP/month on ≈50k) → credit is worth it; large → owner decides. On 4/11 cash would have saved ≈428 EGP on one order; owner chose Nahda (credit) that week for liquidity. Re-decide each time together, checking pharmacy cash.
- Use any warehouse he deals with (he has good relations with all). No fixed cap on the number of suppliers.
- **Never send a warehouse a tiny order (1–5 lines)** — a rep rides a motorcycle for it. Move tiny-order lines to the next-best supplier already in the order; show the EGP cost.
- Ibn Sina search result ≠ stock (no stock field). Say "ظهر في البحث"; tele-sales confirm availability.
- Ibn Sina is hard to deal with → extra-careful review. Company purchases (Ibn Sina, Pharma) are reported to tax ("جواب المسحوبات"); warehouses are not.
- Expired returns: only companies (Ibn Sina, Pharma Overseas), as a % of the claim. Expired stock is NOT returned to warehouses.

## 6. Market facts learned (i'Supply scan 4/11, 201 lines)
- Best discount most often: Orient (84/143 items), Al-Salam (68/101). Nahda's shown discount is high but loses after −2.
- Average gap from best (points): Orient 0.74, Salam 0.77, Nahda 0.85 (shown), Wataniya 1.29, Ibn Khaldun 1.38 (before +1), Zahed 1.47, New Arabi 1.54, Youssef 1.61, Taj 2.12, Menoufia 2.20.
- Typical spread between sellers on one item: 1–2 points. ED drugs normally 50–70 %.
- Price changes seen: Tebofortin Forte 165 → 202.5 → 240; C-Retard+Zinc 80 → 98; Imeglibright 159 → 186.
- Cold Free: bought 10+2 at Ibn Sina on 2/9 (42.5 %). Sales ≈ 32/year, up in winter (7 in Oct).
- Haloprol 5 mg/1 ml 3 amp: sold 25–55/month until Aug 2026 — revisit.
- Spelling to confirm with owner: "امتريكس" ampoule (not found on i'Supply/Ibn Sina; ask exact English spelling once).

## 7. Tool how-tos
- **i'Supply** (https://app.isupply.com.eg, owner logs in): `GET /products/search?keyword=<w>&category=1` with header X-Requested-With → `[{id,title,price}]`. Offers: `GET /best-price/products?id=<id>&selected_sellers=&category=1` (HTML) → regex `من: (.+?) سعر المستهلك: ?([\d.]+) ?جم سعر الصيدلية: ?([\d.]+) ?جم ([\d.]+)%`. Sequential only, ≈1.2 s gap, retry empty results after 4 s (it throttles and returns empty lists). Keep the tab in front. Has English and Arabic names; has coding errors.
- **Ibn Sina portal** (https://customerportal.ibnsina-pharma.com): direct API blocked (signing) → hook XMLHttpRequest, type in `input[placeholder^="البحث"]`, dispatch input + keyup, read `searchResult` (name, nameAr, price, pharmacyPrice, isRefrigerator, updatedOnUtc). Tab must be in front. No stock field. Payments section: unpaid claims, statement, discount tab, tax letter.
- **Pharma Overseas** (https://shop.pharmaemarket.com): product page shows "Taxes %".
- **Drug Eye**: `drugeye.pharorg.com/drugeyeapp/android-search/drugeye-android-live-go.aspx`; `%` wildcard; extra menus "خدمة أحدث الأسعار" → "قائمة أحدث الأسعار" (downloadable list) and "بحث في مخازن الأدوية" (hard-to-find items only). (N/A) ≈ probably discontinued.
- **WhatsApp Web** (Chrome = owner's personal Business account; pharmacy account is also Business): tele-sales chats live in the personal one; it also sees the kashkool group. Send files: attach → Document → file input → upload → send. Long multi-line text: paste via ClipboardEvent on `footer [contenteditable]`. Edit a sent message (≤15 min): right-click → Edit → Ctrl+A → type → ✓. Read a long chat: scroll the pane and collect `[data-pre-plain-text]` (DOM is virtualized).
- **E-Stock** (Modern Soft 1.8.3, D:\ path, read-only for us): date pickers and export recipe in `HANDOFF_F26_ESTOCK.md` Phase A. Computer use is costly → prefer the owner exporting reports.

## 8. Mistakes made and their fixes (do not repeat)
| Date | Mistake | Fix / rule |
|---|---|---|
| 3/11 | Rule (+1/−2) not written in handoff → second account allocated wrongly | Every rule into this file the same turn |
| 3/11 | Sent Ibn Sina PDF without asking; called tele-sales "دكتورة" | No supplier message without approval of exact text |
| 3/11 | Generalized VAT from one case | Check VAT item by item |
| 3/11 | Ordered Charcoal though 30-cap box in stock; Glucoadjust for one patient | Same-product packs + single-patient checks |
| 4/11 | Merged "راني فوار" + "فوار فواكه"; called them non-drug | Never merge kashkool lines; verify before classifying |
| 4/11 | Added unconfirmed +2.2 % to Ibn Sina; compared Ibn Sina vs Nahda only → 59 wrong lines | No assumed discounts; compare all sellers |
| 4/11 | Tiny orders to 6 warehouses, then everything dumped in 2 suppliers by convenience | Consolidate by next-best discount, show cost |
| 4/11 | Said "موجود" for search hits; matched items by price only | "ظهر في البحث"; confirm brand+strength+pack |
| 4/11 | Ignored today's incoming WhatsApp lists | Read "وارد" before finalizing |
| 4/11 | Built on another account's draft without re-checking; stale balances; rushed before 3:30 | Fresh data, `order_check.py` gate, start early |
| 4/11 | Treated Virecta and Virecta PLUS as one product | Modifiers make a different product |
| 4/11 | Final summary in English | Always Arabic |

## 9. Open items (re-raise until closed)
- Returns: Glucoadjust → Ibn Khaldun rep; Atorstat 10 batch 50605 (recall) → Ibn Sina rep with receipt.
- Purchases report since 3/11; Ibn Sina claim photo with invoices (verify invoice "margin" column).
- Ibn Sina customer-service questions: `docs/IBNSINA_CS_QUESTIONS.md`.
- Tax rate on company purchase letters: ask an accountant + research.
- High-discount items we have in stock, grouped by use (cold, stomach, diarrhea…) to push sales.
- Keypad phone + SIM for the delivery person.
- Later: invoice entry from photo, E-Stock evaluation vs our platform, reconciliation module (claims/orders/money/returns), Drug Eye price check of slow stock, sell-off pricing, backup fix (last).
