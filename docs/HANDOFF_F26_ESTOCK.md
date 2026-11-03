# Handoff — F26 smart order + E-Stock data (2026-11-03 17:50 Cairo)

Read first: `docs/SMART_ORDER_REPORT.md` (§47–§58 are the latest). Branch `claude/f26-smart-order`, PR #102 → develop.
Owner prefs: short Egyptian Arabic replies, double-check everything, ask before expensive/fridge/low-discount items, never message suppliers without his OK on the exact text, never call tele-sales "دكتورة".

## What the next session needs (besides this file)
- Work files (scripts, data, outputs, raw E-Stock exports): `work/f26_2026-11-03/` (see its README).
- New E-Stock exports from 3/11 are on the pharmacy PC Desktop (`claude_*.xlsx`) — ask the owner to attach them; folder access was denied.
- Tools: GitHub access to this private repo; Claude in Chrome logged into i'Supply / Ibn Sina portal / Pharma Overseas / WhatsApp Web (owner keeps passwords); Claude desktop app on the pharmacy PC with Computer use for E-Stock.
- Another Claude account does NOT share this account's memory. Owner rules to apply: masculine Arabic; very short replies; constructive critic; double/triple-check; never assume supplier terms — ask; English text in its own code block (renders garbled inline); big bold fonts in files; warn before heavy usage; keep open questions and re-raise them; list open reminders at the end of each reply until he closes them; supplier files = item name + qty + polite note only.
- Supplier WhatsApp chats: Nahda = +20 10 92911028; Ibn Sina = تقي صابر تليسيلز صباحي ابن سينا; Ibn Khaldun = ياسمين محسن سيلز ابن خلدون; Wataniya = عماد المعصراوي سيلز مخزن الوطنية. Nahda cutoff 3:45–4 pm. When sending a PDF: attach → Document (not Photos), clear any draft caption first. The WhatsApp Web in Chrome is the owner's personal account (pharmacy WhatsApp is separate — ask).
- Supplier sites: https://app.isupply.com.eg — https://customerportal.ibnsina-pharma.com — https://shop.pharmaemarket.com — Drug Eye https://drugeye.pharorg.com/drugeyeapp/android-search/drugeye-android-live-go.aspx

## Plan and progress

### Phase A — export E-Stock data (computer use on pharmacy PC) — ≈ 60%
| # | Step | Status |
|---|---|---|
| A1 | Purchases → تقرير فواتير المشتريات بالأصناف, 1/11/2025 → 3/11/2026 | ✅ `Desktop\claude_purchases_2025-11-01_to_2026-11-03.xlsx` (837 KB) |
| A2 | Sales → تقرير مبيعات أصناف عن فترة, one file per month | ✅ Nov-2025 … Jul-2026 → `Desktop\claude_sales_YYYY-MM.xlsx` (9 files) |
| A3 | Same for Aug, Sep, Oct 2026 | ⏳ (dates for Aug already typed). Older ORDER_8/9/NOV files exist as fallback |
| A4 | Sales → تقرير كميات أصناف لم تباع (3 and 6 months) | ⏳ |
| A5 | Stores → تقرير كميات أصناف المخازن طبقًا لتواريخ الصلاحية (after Ibn Khaldun invoice is entered) | ⏳ |
| A6 | Items → تقرير أصناف تغيرت أسعارها (6 months) | ⏳ |
| A7 | Screenshot filter screens only: حركة بيع الأصناف، فواتير المبيعات عن فترة، حركة صنف في المخزن | ⏳ |
| A8 | Owner attaches the Desktop files in chat (folder-access request was denied) | ⏳ |

**E-Stock how-to (verified):**
- Windows PC, app `D:\Modern Soft for Programming\e-Stock Pharmacy\e-Stock\e-Stock.exe` (there is also a C:\ copy — use D:). Screen frame 1269×951.
- Date pickers: click the minutes digits (x≈467; from-row y≈123, to-row y≈152), then type in this order with Right between: minutes, hours, yyyy, MM, dd, A/P. From = `00,12,yyyy,MM,01,A`; To = `59,11,yyyy,MM,last,P`. Always zoom to verify.
- Sales-by-period form: بحث (678,157) → معاينة طباعة (598,853) → export icon (1192,108) → type list (360,430) → "Microsoft Excel - Data Only (XLSX)" (550,507) → file name (365,410) → Save (498,465) → OK (666,520) → tab التقرير (20,80).
- The Claude window can cover dialogs; ask the owner to minimise it. Don't send chat messages mid-batch (the window pops up).
- Read-only: never save/edit anything in E-Stock.

### Phase B — analysis — 0%
B1 merge 12 months → per item: months sold, qty, last month sold; B2 last purchase (date/supplier/discount/expiry) from A1; B3 classify regular / intermittent / stopped / new / seasonal; B4 precise dead stock (cross-check A4 + B1 + B2 + stock); B5 data-quality flags (§58); B6 approval list: expensive (≥300), fridge, near expiry; B7 send Excel files (big bold font).

### Phase C — tomorrow 4/11 Nahda order (before 3:30) — 0%
Kashkool items not ordered on 3/11: ميلاكريست، اكتي كولا سي، فليكتور، كاندالكان، جاست ريج، EXEEDOGAST، بلينك، ازروليد، اومنيفورا، بيزوكارد، مينوكسي جرو، بانادول اكيوت، مكسترد فايل + read new kashkool messages. Check alternate codes with stock (charcoal case), per-item tax/bonus (no rule), Drug Eye for pack/price. Owner approves before sending. Scheduled reminder at 11:30.

### Phase D — Drug Eye price re-check of 1,365 slow items — 0%
In-page loop on drugeye (search by brand, % wildcard), batches of ~300; compare to E-Stock sale price.

### Phase E — platform docs — 30%
E-Stock menu map done (§56). Pending: full E-Stock evaluation vs our platform; review/claims/orders/returns/money reconciliation module spec; backup failure fix (LAST).

### Phase F — enter a purchase invoice in E-Stock from a photo — 0%
Only after A–C; owner reviews on screen before Save.

## Open reminders
- Glucoadjust return to the next Ibn Khaldun rep (printed by mistake 3/11).
- Review all items received 3/11 for returns.
- Keypad phone + SIM for the delivery person.
- Electricity meter photo: DONE 3/11.

## Overall ≈ 30% (A 60%, B 0%, C 0%, D 0%, E 30%, F 0%)
