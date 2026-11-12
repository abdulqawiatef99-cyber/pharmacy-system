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
