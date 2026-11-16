# Stock integrity (#202)

Owner's requirement (12/11): the old system (E-Stock) loses or garbles stock balances after a period of use;
this program must never do that. This file is the map of how stock changes and how we prove it stays right.

## 1. One source of truth

- `stock_movements` is the ledger. It is append-only: database triggers refuse UPDATE and DELETE
  (`0002_catalog_inventory.sql`). A mistake is corrected by a new, opposite movement.
- `stock_balances` is a cache: one row per (branch, item, batch) holding the sum of that key's movements.
  `item_costs` holds the moving average cost.
- The only code that writes any of the three tables is `inventory.service.post_movement()` (and its helper
  `_update_avg_cost`). It inserts the movement and adds the same quantity to the balance in the same
  statement sequence, inside the caller's `db.transaction()` (`BEGIN IMMEDIATE`), so either the business
  document, its movement and the balance are all committed, or none is.
- Negative stock is refused (`insufficient_stock`); no caller passes `allow_negative=True`.

## 2. Every path that changes stock

| Operation | Code | movement_type / ref_type |
|---|---|---|
| Sale (store or delivery) | `Sales.complete` | `sale` / `sale` |
| Customer return | `Sales.return_items` | `sale_return` / `sale_return` |
| Void (same day; also a delivery cancelled before settling) | `Sales.void` | `sale_return` / `sale_void` |
| Purchase invoice approved | `Purchasing.approve` | `purchase` / `purchase_invoice` |
| Approved purchase cancelled | `Purchasing.cancel` | `purchase_reversal` / `purchase_invoice` |
| Return to supplier (with or without batch, nearest expiry first) | `procurement.returns.create` | `supplier_return` / `supplier_return` |
| Stock count approved | `Counting.approve` → `adjust` | `adjustment` or `damage` / `count_session` |
| Manual change with a reason | `inventory.adjust.adjust` | `adjustment` or `damage` / `manual_adjust` |
| E-Stock / Excel opening balances | `DataImport.apply` | `opening` / `import_run` |

Do **not** move stock: draft sales, discarded drafts, delivery states `out → delivered → settled` (stock left
at completion), refused operations (rolled back whole).

Transfers between branches: not built yet (one branch at launch). The movement types `transfer_in` /
`transfer_out` are reserved in the schema; a future transfer must post both through `post_movement` in one
transaction, and the guard test below will refuse any other way.

## 3. Proof (tests)

`tests/test_stock_write_paths.py`
- Static guard: no file in `app/`, `manage.py` or `launcher.py` other than `app/inventory/service.py` has
  SQL that inserts, updates, replaces or deletes rows of `stock_movements`, `stock_balances` or `item_costs`;
  inside that file only `post_movement` and `_update_avg_cost` do; nobody passes `allow_negative=True`; no
  migration trigger writes those tables.
- Dynamic: each operation in the table above runs under a SQL trace. The change of every cached balance must
  equal the sum of the new movements of that key, every statement that wrote a stock table must be one of
  `post_movement`'s own, and `verify_balances` must be empty afterwards. Refused operations must leave no
  movement.
- Checked by mutation once (12/11): adding a stray `UPDATE stock_balances` in `adjust.py` fails both the static
  and the dynamic test.

## 4. Automatic integrity check (`app/inventory/integrity.py`)

When: on every program start (background thread, the screens do not wait) and on every automatic backup run
— a refused one (a stock mismatch is one of the reasons `backup.create` refuses to write a backup) and one
skipped for "no changes" (a change made outside the program does not change the backup's fingerprint). The owner can also run it from the report «مطابقة الأرصدة».

What it compares, per pharmacy, reading only:

| Kind | Meaning |
|---|---|
| `batches` | a (branch, item, batch) balance differs from the sum of its movements |
| `items` | a (branch, item) total differs from the sum of all its movements |
| `negative` | a balance below zero (`post_movement` refuses those, so it means outside damage) |
| `foreign_batch` | a movement on a batch of another item or pharmacy |
| `sales` | a (sale, item) whose net stock moved (sale + returns + void) differs from the invoice (sold − returned; 0 if voided) |

On a mismatch:
- an audit row `stock.integrity_mismatch` (reason `startup` / `backup` / `manual`, counts, first rows of each kind,
  a fingerprint) on every detection;
- an error notification «تنبيه: أرصدة المخزون لا تطابق حركات المخزون» when the mismatch is new (a different
  fingerprint from the last one recorded), so a lasting problem does not flood the bell;
- a red banner at the top of every screen for the owner and managers (`audit.read`) until a later check is clean;
- **nothing is changed** in `stock_movements`, `stock_balances` or `item_costs`.

When a later check is clean, an audit row `stock.integrity_ok` is written and the banner disappears.
A failure of the check itself (an exception) is logged and never stops the start or the backup.

## 5. Long-run proof (`tests/test_stock_long_run.py`)

Seeded mix of thousands of operations through the real services: sales across several batches, returns,
same-day voids, purchases (also a real invoice approved, and one approved then cancelled), supplier returns,
damage/expiry changes, "found" stock, stock counts. Plus: 4 cashiers selling the same items at the same time
on separate threads; a sale whose process is killed (`kill -9`) inside its transaction after the first
movement was written (afterwards: the sale is still a draft, no movement was kept, the database works at once);
a backup taken, more work, then that backup restored (same movement count) and the run continued on the
restored copy.

An independent Python model of the expected units per item is kept from what each successful operation must
do. Every 250 operations and at the end: balances = model, `verify_balances` empty, integrity scan clean.

- Default 3000 operations (~10 s, in the normal suite). `PHARM_LONG_RUN=20000` passed on 12/11 in 73 s.
- Mutation check (12/11): a balance off by one only on returns of exactly 3 units fails the test at the first
  checkpoint.

## 6. Owner's report «مطابقة الأرصدة» (`#/stock-check`)

Reports → «مطابقة الأرصدة», or a click on the red banner. Owner and manager only (`audit.read`).
Opening it (or «افحص الآن») runs the check now (`POST /api/reports/stock-check`), recorded like any other run
but with the user's name and reason `manual`. It shows: the status (all matching / N differences), how many
items and stock movements were checked, every difference with item name, code and batch (first 50 of each
kind), what to do (below), and the last 20 checks that found or cleared a mismatch. Printable. It changes no
balance. Tests: `tests/test_stock_check_report.py` (API + a real browser: banner after sign-in → report →
fixed → clean → banner gone).

## 7. What happens on a detected mismatch (decision 12/11, #202)

1. **Nothing is repaired automatically, ever.** The ledger (`stock_movements`) is the truth and is never
   edited; the check only reads.
2. **The program tells the owner at once:** red banner on every screen for owner and manager, an error
   notification in the bell, and an audit row on every detection (`stock.integrity_mismatch`, tamper-evident chain).
3. **Backups are refused while a batch balance differs** (existing rule in `backup.create`: a backup must pass
   the same verification as a restore). Older good backups stay; the backup screen shows the failure. This keeps
   a damaged state from replacing good copies, at the cost of no new backup until it is resolved — so the
   owner must act the same day.
4. **Who fixes:** support (the lead agent on GitHub) with the owner, never a cashier. Steps:
   - the owner prints «مطابقة الأرصدة» and sends it with the newest good backup (no real data in GitHub: the
     backup goes by hand, the Issue gets counts and item codes only);
   - support finds the cause from the ledger and the audit log (which document, which update, what damage);
   - **cached balance ≠ ledger** (the ledger is complete): the balances are rebuilt from the ledger. There is
     no button for it yet; until a dedicated, audited command exists, the safe way is restoring the newest
     backup that verifies (`manage.py restore`) — **the owner's approval is required** (`AGENTS.md` §5); a
     rebuild command is proposed as F53 in `FUTURE_REQUIREMENTS.md`, not built;
   - **a sale moved stock differently from its invoice** (`sales` kind): the shelf is corrected by a stock count
     on that item, approved by the owner (normal, audited movements; the old rows stay). The sale's own movements
     can never equal its invoice afterwards (the ledger is append-only and the count posts under
     `count_session`), so the owner then marks **that exact row** «تمت المراجعة» on the report with a note
     (`POST /api/reports/stock-check/ack`, `settings.manage` = owner only, audit `stock.integrity_ack`). The row
     stays out only while its numbers are exactly the reviewed ones; any later change brings it back. Only
     `sales` rows can be reviewed; a balance that differs from its ledger is never acknowledged away.
5. **Closed when** a check is clean again (after a review, the report runs the check at once): the audit row `stock.integrity_ok` is written, the banner goes away,
   and the next backup succeeds. The cause and the fix go on the Issue.
