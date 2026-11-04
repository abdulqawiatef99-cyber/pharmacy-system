# Handoff — F26 smart order + E-Stock data (2026-11-03 17:50 Cairo)

> **Latest (2026-11-03 21:30): read `docs/HANDOFF_ACC2.md` first** — second-account session: full year analysis, sell-off list, 4/11 order allocated to suppliers (Ibn Sina / Ibn Khaldun +1 / Nahda −2 / Wataniya). Files in `work/acc2_2026-11-03/`.

Read first: `docs/SMART_ORDER_REPORT.md` (§47–§58 are the latest). Branch `claude/f26-smart-order`, PR #102 → develop.
Owner prefs: short Egyptian Arabic replies, double-check everything, ask before expensive/fridge/low-discount items, never message suppliers without his OK on the exact text, never call tele-sales "دكتورة".

## What the next session needs (besides this file)
- Work files (scripts, data, outputs, raw E-Stock exports): `work/f26_2026-11-03/` (see its README).
- New E-Stock exports from 3/11 are on the pharmacy PC Desktop (`claude_*.xlsx`) — ask the owner to attach them; folder access was denied.
- Tools: GitHub access to this private repo; Claude in Chrome logged into i'Supply / Ibn Sina portal / Pharma Overseas / WhatsApp Web (owner keeps passwords); Claude desktop app on the pharmacy PC with Computer use for E-Stock.
- Another Claude account does NOT share this account's memory. Owner rules to apply: one file per output (merge only when asked); plain words, no odd terms; never leave open questions — answer/resolve them; finish work, don't stop midway; think about the whole picture (e.g. all items missing from ALL suppliers, not one supplier); ED drugs normally come at 50–70% discount (not an error); masculine Arabic; explain step by step in plain words (he is not technical); very short replies; constructive critic; double/triple-check; never assume supplier terms — ask; English text in its own code block (renders garbled inline); big bold fonts in files; warn before heavy usage; keep open questions and re-raise them; list open reminders at the end of each reply until he closes them; supplier files = item name + qty + polite note only.
- Supplier WhatsApp chats: Nahda = +20 10 92911028; Ibn Sina = تقي صابر تليسيلز صباحي ابن سينا; Ibn Khaldun = ياسمين محسن سيلز ابن خلدون; Wataniya = عماد المعصراوي سيلز مخزن الوطنية. Nahda cutoff 3:45–4 pm. When sending a PDF: attach → Document (not Photos), clear any draft caption first. The WhatsApp Web in Chrome is the owner's personal account (pharmacy WhatsApp is separate — ask).
- Supplier sites: https://app.isupply.com.eg — https://customerportal.ibnsina-pharma.com — https://shop.pharmaemarket.com — Drug Eye https://drugeye.pharorg.com/drugeyeapp/android-search/drugeye-android-live-go.aspx

## Plan and progress

### Phase A — export E-Stock data — ≈ 60%
**Decision 2026-11-03 18:00:** the owner exports A3–A7 himself (computer use costs too much usage for simple exports). Same chat continues from the Claude desktop app on the pharmacy PC with the new account. Working data (pkl/xlsx) is gitignored and was NOT pushed — the owner must re-send the exports.
| # | Step | Status |
|---|---|---|
| A1 | Purchases → تقرير فواتير المشتريات بالأصناف, 1/11/2025 → 3/11/2026 | ✅ `Desktop\claude_purchases_2025-11-01_to_2026-11-03.xlsx` (837 KB) |
| A2 | Sales → تقرير مبيعات أصناف عن فترة, one file per month | ✅ Nov-2025 … Jul-2026 → `Desktop\claude_sales_YYYY-MM.xlsx` (9 files) |
| A3 | Same for Aug, Sep, Oct 2026 — **owner, manual** | ⏳ |
| A4 | Sales → تقرير كميات أصناف لم تباع (3 and 6 months) — **owner, manual** | ⏳ |
| A5 | Stores → تقرير كميات أصناف المخازن طبقًا لتواريخ الصلاحية (after Ibn Khaldun invoice is entered) | ⏳ |
| A6 | Items → تقرير أصناف تغيرت أسعارها (6 months) | ⏳ |
| A7 | Screenshot filter screens only: حركة بيع الأصناف، فواتير المبيعات عن فترة، حركة صنف في المخزن | ⏳ |
| A8 | Owner sends all `claude_*.xlsx` + ORDER_8/9/NOV + stock-by-expiry 3-11 (or grants Desktop folder access) | ⏳ |

**E-Stock how-to (verified):**
- Windows PC, app `D:\Modern Soft for Programming\e-Stock Pharmacy\e-Stock\e-Stock.exe` (there is also a C:\ copy — use D:). Screen frame 1269×951.
- Date pickers: click the minutes digits (x≈467; from-row y≈123, to-row y≈152), then type in this order with Right between: minutes, hours, yyyy, MM, dd, A/P. From = `00,12,yyyy,MM,01,A`; To = `59,11,yyyy,MM,last,P`. Always zoom to verify.
- Sales-by-period form: بحث (678,157) → معاينة طباعة (598,853) → export icon (1192,108) → type list (360,430) → "Microsoft Excel - Data Only (XLSX)" (550,507) → file name (365,410) → Save (498,465) → OK (666,520) → tab التقرير (20,80).
- The Claude window can cover dialogs; ask the owner to minimise it. Don't send chat messages mid-batch (the window pops up).
- Read-only: never save/edit anything in E-Stock.

### Phase B — analysis — 70% (2026-11-03 18:40)
Done: 12 months sales + purchases + stock-by-expiry received (A3/A5 done; A4 dropped — dead stock derived from 12 months). Scripts `work/f26_2026-11-03/scripts/b1_load.py`, `b3_classify.py`, `b7_excel.py` (run in a folder with the claude_*.xlsx files). Output `تحليل_سنة_2026-11-03.xlsx`. Results: dead (stock>0, no sale Aug–Oct, not bought in 75 days) = 1,592 items ≈ 158,100 EGP; regular 358 items; 61 data-quality flags. Purchases export has NO supplier name column. Two Ibn Khaldun invoices not yet entered in E-Stock: 150292 (2/11, Blink Tears ×2) and 150745 (3/11, 15 lines, 2,239.89) — listed in the Excel; don't re-order those items.
Update 18:55: split into 5 files (`1_مسودة_طلبية_4-11`, `2_الرواكد`, `3_بيانات_مشكوك_فيها`, `4_تصنيف_كل_الأصناف`, `5_فواتير_ابن_خلدون_مش_متسجلة`) by `b8_split_files.py` + `c1_order_draft.py`. Data-quality flags refined to 31 real cases (strip fractions are normal; ED drugs/supplies have high discounts). Cost≈0 lines = bonus entered as its own invoice (likely); cost = sale price = bought from another pharmacy at public price (likely) — owner to confirm.
Dead stock tiers: expired 92 ≈ 10.3k; expiring ≤6 months 215 ≈ 24.8k; ≥100 EGP good expiry 375 ≈ 81.1k; <100 EGP 910 ≈ 41.9k. 134 items = 50% of tiers 2+3 value.
Order draft 4/11 = regular + semi-regular items short for 2 weeks + kashkool, minus Ibn Khaldun arrivals; qty for 1 week / 2 weeks / month (≈ 23.2k / 33.9k / 56.6k at cost). Not yet assigned to suppliers. Ibn Sina 3/11 manual list still not arrived.
B1 merge 12 months → per item: months sold, qty, last month sold; B2 last purchase (date/supplier/discount/expiry) from A1; B3 classify regular / intermittent / stopped / new / seasonal; B4 precise dead stock (cross-check A4 + B1 + B2 + stock); B5 data-quality flags (§58); B6 approval list: expensive (≥300), fridge, near expiry; B7 send Excel files (big bold font).

### Phase C — tomorrow 4/11 full order (ALL items not received from any supplier, not only Nahda) — 30%
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

## Overall ≈ 55% (A 100% (A4 dropped, A6/A7 optional), B 100%, C 0%, D 0%, E 30%, F 0%)

## Update 2026-11-03 19:10 — owner corrections (apply always)
- Never dismiss any price/discount difference as "small" — we chase maximum margin.
- Cost = public price lines = bought from another pharmacy (correct data). Bonus of a DIFFERENT item is entered as its own purchase invoice at ~0 cost (same-item bonus goes inside the invoice). Not errors. Flags now 18.
- Expired stock is NOT returned to suppliers.
- Supplies postponed; order = medicines only, weekly quantity.
- Purchase decision = regular/semi-regular items short for one week + kashkool (customer names are not in the E-Stock sales export; kashkool comes from WhatsApp).
- Supplier allocation needs live search: i'Supply (warehouse offers), Ibn Sina portal, Pharma Overseas, Drug Eye (public price/pack), plus WhatsApp kashkool. In this session Chrome is connected but i'Supply showed the login page → owner must sign in to each site + WhatsApp Web first.
- Liquidation list `d1_liquidation_list.py` → `2_أصناف_للتصريف.xlsx`: per expiry batch; expired 110 ≈ 10.7k, expiring ≤6 months 248 ≈ 27.2k, dead 1,433 ≈ 124.9k, overstock (>3 months of sales) 162 ≈ 26.3k; total ≈ 189k (medicines ≈ 178k). Next: price check vs current public price; update old prices where possible, otherwise mark "على السعر القديم" as a selling point for other pharmacies.

- 19:20: Ibn Sina 3/11 printed (Taqi chat): Barictomap 1, Servitam 1, Epicopred 5mg 1, Rositor 20 x2. Anafranil 75 OUT at Ibn Sina → in tomorrow order (qty 5). Kashkool latest: Azrolid 500 x2, Dozova melatonin, Acti-Kola C (new item, not in E-Stock), Candalkan 8 confirmed. Ibn Sina portal needs owner login; Pharma Overseas skipped (same prices as Ibn Sina).

- 20:25: Ibn Sina search (205 items, in-page capture of search-products) + i'Supply best-price offers (Livewire page /best-price/products?id=ID&category=1 rendered in hidden iframe; product search via captured /products/search template). Allocation = highest discount among Ibn Sina / Nahda / Ibn Khaldun / Wataniya; 29.8% at Ibn Sina flagged as likely pre-tax (20% effective). Ibn Sina availability NOT checked yet. Scripts alloc.py, supfiles.py; raw matches ibnsina_match.txt, isup.txt. Lesson: Ibn Sina low-score matches (Orlistat/Maxilase/Vitayami) were different packs, not old prices.

## Progress table (updated 2026-11-03 21:30, acc2)
| Phase | Status | % |
|---|---|---|
| A — E-Stock exports | ✅ all received (A4/A6/A7 dropped or optional) | 100% |
| B — analysis (12 months, classes, dead stock, data flags) | ✅ | 100% |
| C — 4/11 order | ⏳ allocated by price (alloc2: Ibn Sina 115, Ibn Khaldun 53, Wataniya 15, Nahda 2, ask 39); pending: Ibn Sina tax (19) + availability, other warehouses' terms, owner approval, message text | 80% |
| D — price check of sell-off items (Drug Eye / i'Supply) | ❌ not started; sell-off list ready (`2_أصناف_للتصريف.xlsx`, ≈189k EGP) | 20% |
| D2 — expired-returns terms at Ibn Sina / Pharma Overseas | ❌ not started | 0% |
| E — platform docs | unchanged | 30% |
| F — enter invoice from photo | not started | 0% |
| **Overall** | | **≈ 70%** |

## Owner rules added 2026-11-04 (must follow every session)
- Show a short step-by-step progress report in chat for every step (SendUserMessage).
- Purchase choice cash vs Nahda credit: decide in EGP, not %. Compute per line and monthly total: Nahda cost (shown -2%) vs best cash warehouse on i'Supply (ANY seller, not only Ibn Khaldun/Wataniya). If monthly difference is small (~300-400 EGP on ~50k) credit wins; if large, show numbers and let owner decide.
- Never merge kashkool lines: "راني فوار" (RANI-F) and "فوار فواكه" (FAWAR FRUIT) are two different DRUGS (high sellers). Verify any unknown item on i'Supply/Ibn Sina before classifying as non-drug.
- "توفير X" in kashkool = usually a customer asked; order 1 box of each strength only if balance is zero (check exported stock yourself).
- Check balances yourself from the exports; never say "only if balance is zero" — find out.
- WhatsApp: both accounts are Business. Personal one = supplier tele-sales chats (and sees the kashkool group).
- Ibn Sina claim discount categories (regular 3% / special 2% / other 0.25%): meaning unknown (owner guesses local/imported/supplies, NOT a rule) -> ask Ibn Sina customer service.
- VAT: if not clear on Ibn Sina, check the item on Pharma Overseas portal (often similar, not a rule).
- Drug Eye extra menus: "خدمة أحدث الأسعار" -> "قائمة أحدث الأسعار" (downloadable list to compare with our prices) and "بحث في مخازن الأدوية" (use only for hard-to-find items).
- Collect all Ibn Sina customer-service questions into one list; call once after the order.
- Before heavy computer-use (E-Stock screenshots), warn and offer the cheaper option (owner exports the report).
- Backup sourcing ideas (later, keep current plan now): ask every warehouse (owner is in touch with all) for their stock list when i'Supply fails; try another app similar to i'Supply later.
- TODO list for owner: high-discount items WE HAVE IN STOCK, sorted high->low discount, grouped by therapeutic category (cold, stomach, diarrhea...) so he can push/prescribe them for more profit. Supplies grouped separately for a later order.
- When any tool/site blocks me (e.g. i'Supply "out of stock" message, empty results), ASK the owner — he wants to teach purchasing know-how.
- 2026-11-04 decisions/lessons: this week Nahda-first (credit) due to low liquidity; next week decide cash vs credit together after checking pharmacy cash. Before proposing any item check sales recency & pattern (e.g. Reagila: rare/single patient -> don't order). i'Supply has coding errors (Mebo 47% at Taj = actually Mebo C) -> sanity-check outlier discounts. Never give half info: always state strength/pack, price basis (public vs pharmacy), system name+code. Edit WhatsApp msgs: right-click msg -> Edit -> ctrl+a -> type -> check button (within 15 min).
- Deferred/removed 4/11: Reagila, Centrum Energy, Bladogra, Duphaston, Regaine, Gastrotidine, Mixtard vial, Dozova, Nasonex, Strepsils, Sleep Aid, Haloprol (5mg/1ml; big seller 25-55/month until Aug — revisit).
