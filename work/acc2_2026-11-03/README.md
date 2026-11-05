# acc2 — working files of the second Claude account (2026-11-03, 17:48–21:26 Cairo)

Private pharmacy data. Scripts expect all files in ONE folder: copy `raw/*.xlsx`, `scripts/*.py` and `data/*` into a scratch folder and run from there (`python3 -W ignore <script>.py`). Order of running: `load.py → an.py → order.py → liq.py → files.py → alloc.py → supfiles.py → alloc2.py`. `out.py` is the first (superseded) one-workbook version.

## raw/ — sent by the owner in chat (unchanged content, upload prefix removed from the name)
| File | From | Contents |
|---|---|---|
| claude_purchases_2025-11-01_to_2026-11-03.xlsx | E-Stock › المشتريات › تقرير فواتير المشتريات بالأصناف | All purchase lines 1/11/2025→2/11/2026 (9,860 lines, 1,296 invoices). No supplier-name column. |
| claude_sales_2025-11.xlsx … claude_sales_2026-10.xlsx (12 files) | E-Stock › المبيعات › مبيعات أصناف عن فترة, one month each, "Excel - Data Only" | Per item: code, name, company, qty sold, balance, price, cost, profit %. No dates inside the file. |
| claude_stock_expiry_2026-11-03.xlsx | E-Stock › المخازن › كميات الأصناف طبقًا لتواريخ الصلاحية | Stock per expiry batch on 3/11 (2,929 rows, 2,670 codes, ≈294.7k EGP at cost). Two Ibn Khaldun invoices NOT yet entered. |
| invoice_ibnkhaldun_150745_2026-11-03.jpg | Owner photo | Ibn Khaldun invoice 150745, 3/11, 15 lines, net 2,239.89 |
| invoice_ibnkhaldun_150292_2026-11-02.jpg | Owner photo | Ibn Khaldun invoice 150292, 2/11, Blink Tears ×2, net 505.60 |
| screenshot_claude_desktop_2026-11-03.png | Owner screenshot | Claude desktop app open on the pharmacy PC (context only) |

## scripts/
| Script | Reads | Writes | What it does |
|---|---|---|---|
| load.py | raw xlsx | S.pkl, st.pkl, P.pkl | Parses 12 sales months, stock-by-expiry, purchases (header row per invoice + item rows). |
| an.py | S/st/P.pkl | T.pkl | Per item: 12 monthly qty, months sold, last month sold, last purchase (date/discount/expiry/cost), stock, class (منتظم/شبه منتظم/متقطع/متوقف/ما اتباعش سنة/جديد/اتشرى حديثًا), data-quality flags (owner-corrected rules). |
| out.py | T.pkl | تحليل_سنة_2026-11-03.xlsx | First one-workbook output (superseded by files.py at owner's request). |
| files.py | T.pkl, st.pkl | 3_بيانات_مشكوك_فيها, 4_تصنيف_كل_الأصناف (+ an old 2_الرواكد, deleted) | Separate files. |
| order.py | T.pkl, S.pkl | order_draft.pkl, 1_مسودة_طلبية_4-11.xlsx | Purchase decision: regular/semi-regular medicines short for 1 week + kashkool, minus arrivals not entered (Ibn Khaldun 2 invoices, Ibn Sina printed 3/11); flags ≥300 / fridge / data issues. |
| liq.py | T.pkl, st.pkl | liq.pkl, 2_أصناف_للتصريف.xlsx | Items to sell off, per expiry batch: expired / ≤6 months / dead / >3 months of sales. |
| alloc.py | order_draft.pkl, ibnsina_match.txt, isup.txt | alloc.pkl, 1_طلبية_4-11_توزيع_الموردين.xlsx | Supplier per item by highest discount (Ibn Sina vs Nahda/Ibn Khaldun/Wataniya on i'Supply). WITHOUT the +1/−2 rules. |
| supfiles.py | alloc.pkl | مسودة_<مورد>_4-11.xlsx ×4, اسأل_عليها_4-11.xlsx, 3_أسعار_محتاجة_تحديث.xlsx, 4_أسماء_وعبوات_محتاجة_مراجعة.xlsx | Per-supplier files (name + qty only), price-update and naming lists (both hand-corrected afterwards, see HANDOFF_ACC2 §و). |
| alloc2.py | alloc.pkl | alloc2.pkl, 1ب_طلبية_4-11_بعد_قواعد_الموردين.xlsx, مسودة2_<مورد>_4-11.xlsx ×4 | Same allocation with owner rules: Ibn Khaldun = i'Supply +1, Nahda = i'Supply −2. **Latest.** |

## data/
| File | Contents |
|---|---|
| S.pkl / st.pkl / P.pkl | Parsed sales / stock / purchases (pandas). |
| T.pkl | Master item table from an.py. |
| order_draft.pkl | 224-line weekly order draft (order.py). |
| liq.pkl | Sell-off list (liq.py). |
| alloc.pkl / alloc2.pkl | Allocation before / after the +1/−2 rules. |
| items.json, terms.json, isup_in.json | Inputs pasted into the browser for the Ibn Sina / i'Supply searches. |
| ibnsina_match.txt | Ibn Sina portal match per code: `code|IbnSina name|public price|pharmacy price|match score` (score <5 = doubtful; wrong matches already removed). Searched 3/11 19:25–19:50. |
| isup.txt | i'Supply best-price per code: `code|i'Supply name|public price|top warehouse:disc|Nahda|IbnKhaldun|Wataniya|Taj` (% shown on i'Supply). Searched 3/11 20:00–20:15. |

## outputs/ (all sent to the owner in chat)
| File | Script | Contents |
|---|---|---|
| تحليل_سنة_2026-11-03.xlsx | out.py | One workbook (summary, definitions, dead stock, all items, data flags, Ibn Khaldun invoices) — superseded. |
| 1_مسودة_طلبية_4-11.xlsx | order.py | Weekly order draft 224 lines (week / 2 weeks / month qty). |
| 1_طلبية_4-11_توزيع_الموردين.xlsx | alloc.py | Allocation before +1/−2 rules (Ibn Sina 115, Nahda 55, Wataniya 10, Ibn Khaldun 5, ask 39). |
| 1ب_طلبية_4-11_بعد_قواعد_الموردين.xlsx | alloc2.py | Allocation after rules (Ibn Sina 115, Ibn Khaldun 53, Wataniya 15, Nahda 2, ask 39). Not yet sent to the owner. |
| مسودة_<مورد>_4-11.xlsx | supfiles.py | Supplier files before rules. |
| مسودة2_<مورد>_4-11.xlsx | alloc2.py | Supplier files after rules (not sent yet). |
| اسأل_عليها_4-11.xlsx | supfiles.py | 39 items found on neither Ibn Sina nor i'Supply. |
| 2_أصناف_للتصريف.xlsx | liq.py | 1,953 lines ≈ 189k EGP to sell off. |
| 3_بيانات_مشكوك_فيها.xlsx | files.py | 18 data-quality cases (after owner corrections) with a fix column. |
| 3_أسعار_محتاجة_تحديث.xlsx | supfiles.py (+manual) | 4 lines: Romantigra (pack doubt), Quadel 93.42→108, Melacryst 3mg 90→100, Zetazolex 4mg 166.5→171. |
| 4_أسماء_وعبوات_محتاجة_مراجعة.xlsx | supfiles.py (+manual) | Romantigra 4 vs 8 tab, Dozova Melatonin 30 vs 60 tab. |
| 4_تصنيف_كل_الأصناف.xlsx | files.py | All 3,927 codes with class and 12 months. |
| 5_فواتير_ابن_خلدون_مش_متسجلة.xlsx | out.py | The 16 lines of invoices 150292 + 150745. |
