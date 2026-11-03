# F26 working files (3/11/2026) — private pharmacy data

- `estock_exports/` — raw E-Stock exports the owner sent:
  - ORDER_8 / ORDER_9 / ORDER_NOV = sales by period Aug / Sep / Oct 2026 (columns: ك.الصنف، إسم الصنف، الرصيد، كمية البيع، إجمالى التكلفة …)
  - REPORT_1-5 = purchase invoices by item 1/5 → 2/11/2026
  - `..._2-11` and `..._3-11` = stock by expiry (3-11 is the latest; layout: col10 code, col9 name, col5 qty, col6 expiry, col4 cost, col3 price)
  - `..._1-11...` = sales 1/11 → 3/11/2026
- `scripts/` — the analysis scripts (paths point at the old session scratchpad; change `D=` / file paths before rerunning). Key ones: `build6.py` (3-month table), `alloc_v3.py` + `today.py` + `alloc_today.py` + `write_today.py` (order of 3/11), `price_an.py` + `rebuild_stale.py` + `write_all_price.py` (stale prices), `dead.py` + `write_dead.py` (dead stock).
- `data/` — pickles/results: `t_all.pkl` (3-month sales table), `purch.pkl` (purchases), `stock_new.pkl` (stock 3/11), `price_all.pkl` / `stale_all.pkl` / `hi_checked.pkl` (i'Supply price check), `dead.pkl`, `final_today.pkl` (order of 3/11 incl. supplier/discount), `is_res.txt` (Ibn Sina prices), `po_df.pkl` (Pharma Overseas discounts), `isup_*` (i'Supply results).
- `outputs/` — the Excel/PDF files sent to the owner (orders_today = 3/11 order; orders_final = الصلاحية_والأسعار، الرواكد، 2/11 order).
