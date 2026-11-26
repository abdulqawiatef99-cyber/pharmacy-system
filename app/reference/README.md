# Pharmacist reference (data only)

- `pharmacist-reference.json`: source of truth. `pharmacist-reference.md`: readable Arabic copy for pharmacists.
- Owner approved the content as the program's reference on 2026-11-19; a second pharmacist is reviewing.
  Corrections come as a new version (bump `meta.version`, add a line to `meta.updates`).
- Source priority: EDA (Egyptian Drug Authority) > Egyptian product leaflet > international sources.
- Written in the pharmacy's own words. No text from third-party licensed material may be added.
- Nothing activates by itself. Safety items enter `app/safety` as drafts and need in-app approval.
  `egypt_otc` and `egypt_do_not_crush` are machine-extracted previews: re-import them from the official
  EDA PDFs (URLs in `meta.sources`, S15/S16) before any rule uses them.
- Weekly update (recall bulletins, new EDA lists, gaps) produces a new version and a diff report.
- `app.reference.load()` / `validate()` only read the file; `tests/test_reference_data.py` keeps it consistent.

Planned integration (separate PRs, see project doc `reference-integration-plan-2026-11-19.md`):
1. category linking screen (items.active_ingredient -> categories), owner approval, local data only;
2. OTC flag per item from the official EDA OTC list + POS "prescription required" notice;
3. do-not-crush -> a counselling note on the item (no split rule: tablet splitting was removed, U2-17), source S16;
4. `interaction` / `duplicate_ingredient` alerts -> safety-engine drafts with knowledge sources;
5. recalled batches from EDA bulletins: block sale, flag on receiving and stocktake.
