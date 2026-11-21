# Synthetic restore acceptance — 2026-10-28
Source runtime: unified candidate 09fd8bde17f5e60f28120127f9a26646d5ff056f, Linux/Python 3.12.14.
Prepared the existing purchase-to-shift HTTP scenario, created and verified its backup, restored into another temporary database/uploads folder, and compared complete rows in sales, returns, customer/supplier ledgers, stock movements, shifts, cash movements and audit_log.
All rows matched before continuation. The synthetic imported invoice attachment matched byte-for-byte.
Then opened a new shift and sold two units for 4000 piastres on the restored database. Restored stock became 18; source stock stayed 20. Restored integrity and audit chain remained valid.
Result: passed. Evidence: RESTORE_DRILL_RESULT.json. This is an actual synthetic Linux restore drill, not a new full-suite run, Windows/device test or owner authorization for production data.
Windows CI was separately started on commit 83d3e48b374b9c79836a53b63b06137256e82b7c; its result is independent of this drill.
No real data, live E-Stock or hardware accessed. Temporary source and recovery databases were removed after the drill.
