# Supplier catalog (2026-10-31)

Offline data project: five supplier price lists → one matched catalog, supplier cost comparison,
and import files for the pharmacy system. Independent of the app release; no app code touched.
The repository is private; the owner asked for this data to live here for later integration.

## Rebuild

```
cd supplier-catalog
./scripts/run_all.sh        # verifies originals (SHA256SUMS), rebuilds build/ and output/, runs tests
```

Requires Python 3 with `pdfplumber`, `openpyxl`, `rapidfuzz`, `pytest`. The workbook is written with
`fullCalcOnLoad`, so Excel computes its formulas on open.

## Layout

| Path | Content |
|---|---|
| `originals/` | The five files exactly as received + `SHA256SUMS` |
| `scripts/textfix.py` | Repairs visually-ordered Arabic from the PDFs |
| `scripts/extract_pdfs.py` | Column-aware PDF row extraction with page/column/position provenance |
| `scripts/normalize.py` | Name normalization, strength/form/pack parsing, price-tag detection |
| `scripts/build_catalog.py` | Cost rules, matching, catalog, comparison, review lists, validation |
| `scripts/build_outputs.py` | Excel workbook + import CSVs |
| `build/` | Intermediate CSVs (`offers.csv` = every source row with its item) |
| `output/supplier_catalog_2026-10-31.xlsx` | Review workbook |
| `output/import/` | System import files (money in integer piasters, discounts in basis points) |
| `tests/` | Unit + integration tests |

## Sources and cost rules (confirmed by the owner)

| Supplier | File | Rows | Cost rule |
|---|---|---|---|
| Al Wataniya (drugs) | `wataniya_مخزن.xlsx` | 4,248 | public × (1 − `كاش سياره`/100); owner buys cash so this tier applies; decimals like 21.01 kept as printed |
| Ibn Khaldun (drugs) | `ibn_khaldun_اوردر_شركات.pdf` | 3,879 | public × (1 − discount/100); prices dated 2026-10-28 |
| Al Nahda (drugs) | `nahda_اوردر.pdf` | 7,808 | public × (1 − discount/100) |
| Future (supplies/cosmetics) | `future_…31-10.pdf` | 2,793 | `السعر` is net; `الجمهور` is public price; supplier code kept |
| Al Mostafa (supplies) | `mostafa_…مستلزمات.xlsx` | 1,732 | no public price; cost = lower of `سعر ج` / `نصف جمله` (both kept); supplier code kept |

Warehouses give no discount beyond the printed one (company claims/rebates are separate and not here).
Minimum order values exist but are not binding in practice; they are not in the costs.

## Matching

1. Exact key: normalized core words (order-insensitive) + all numbers + dosage form.
2. Fuzzy, only within the same public price, across different suppliers: dosage form and
   strengths must agree, numbers must be equal (or one a subset with stricter thresholds), brand and
   name similarity thresholds. A looser tier allows extra descriptive words only if none of them is a
   variant word (colour, flavour, size, forte/plus/combi/SR…).
3. Al Mostafa (no public price): strict fuzzy only, numbers equal.
4. Guard: two different listings of one supplier at one public price never merge.
5. Near misses go to `uncertain_matches.csv` (reviewed manually; nothing merged).

Category: supplies-warehouse items → accessory; else dosage form (tab/cap/syrup/amp/supp/drops/sachet) →
medicine; else accessory keywords (shampoo, soap, diapers, …) → accessory; else medicine marked
`drug_warehouse_only_unverified`.

## Verification done

- Excel row counts equal the originals; PDF row counts equal an independent count of numbers in the
  discount/public-price column zone (header rows and page footers excluded).
- 150 random PDF rows: price, discount/public price (and Future code) found on the same line of an
  independent `pdftotext -layout` extraction — 150/150.
- 200 random PDF names: every Arabic word found in the `pdftotext` text in 194/200; the 6 remaining are
  word-boundary differences in `pdftotext`, not errors.
- Workbook formulas (35,495) recalculated with 0 errors and equal the script values.
- 11 tests pass.

## Known limits

- Future PDF names with Latin text keep the PDF's own mixed ordering (e.g. `mlبندولين`); the raw text
  is kept and display names get a light cleanup.
- Fuzzy matches are flagged (`contains_fuzzy_match`, `fuzzy_match_review`) and should be spot-checked.
- `item_id` (SC000001…) is per build; use `dedupe_key` + aliases when importing repeatedly.
- 3,567 drug-warehouse-only items have an unverified category.
