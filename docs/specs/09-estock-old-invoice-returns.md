# 09 — Return against an old E-Stock invoice (ج1, P0)

Backend only (`app/sales/estock_returns.py`): no API route, no screen yet (they follow when `app/api/app.py`
and `app/web/*` are free). Money and stock path: Codex reviews it; tests first.

## Why
After the move, a customer brings back an item bought in E-Stock. The invoice is not a program sale (it lives
in the read-only archive, `estock-archive.sqlite`), so `Sales.return_items` cannot take it, and the program
must not refund more than was paid, put stock back twice, or lose track of what E-Stock already returned.

## Inputs
`return_items(db, ctx, estock_sales_id, lines, *, reason, key)`
- `lines`: `{line_key: base_units}`. `line_key` is the E-Stock line id (`details_id`) of the local sales lines
  (`Sales_details` when it was read, else `Branches_sales_details` of `co_inf.branch_id`), as `invoice_lines()` lists it.
- `reason`: at least 3 letters (as program returns).
- `key`: the caller's idempotency key (required). The same key for the same invoice returns the first result and
  writes nothing.

## Rules
1. **Find the line** in the archive, through `estock_archive.open_for(path, ctx.org_id)` (another pharmacy reads
   nothing). The item is the program item mapped from `product_id` (`map_items`); an unmapped item is refused.
2. **Quantity sold** in base units = `amount x pack_size` of the program item (whole units only; a fraction that
   is not whole units is refused, never rounded).
3. **Already returned** = E-Stock's own returns of that line, max(`back_amount` on the line, sum of
   `Back_Sales_details.back_amount` for the same invoice and product) x pack, plus the program's returns of that
   line (sum of its `sale_return` stock movements with `ref_type='estock_sale_return'`, `ref_id` = invoice,
   `ref_line_id` = line). A return above what is left is refused.
4. **Value** of the returned units: the line's net (`total_sell`, else `amount x sell_price - disc_money`) x
   units / units sold, half up to the piaster; the return that empties the line refunds exactly what is left of
   the line's net, so all returns of a line add up to its net, never a piaster more.
5. **Money back the same way it came in**, never more than was received:
   - the invoice's parts: card/wallet = `network_money`; cash = `bill_cash - money_change`, capped at net - card;
     on account = net - cash - card (never negative);
   - each part is reduced by what was already refunded from it: E-Stock returns of the invoice (`Back_sales_header`
     net, taken from the account part first, then cash, then card) and the program's earlier returns (their audit
     records);
   - the refund goes to the account part first (a debt is reduced, never turned into cash), then cash (a drawer
     movement `refund`, an open shift is required), then card (recorded; the money goes back to the card outside);
   - if the refund is larger than what is left of all parts, the return is refused.
   - The account part needs the invoice's customer mapped to a program customer (`map_customers`).
6. **Stock back** into the item's batch for that E-Stock batch: batch number `store-counter` (as the import),
   expiry = the line's `exp_date`, cost per base unit = `buy_price / pack` (6 decimals, as the import). Service
   items take no stock.
7. **Permissions**: `sales.return`; the E-Stock cashier is never the current user, so `sales.return_others`;
   older than the return window (`return_window_days`): `sales.return_late`.
8. **One audit record** `estock.sale_return` (entity `estock_sale`, id = the invoice) with the return id, key,
   lines, line refunds, the parts refunded and the total, inside the same transaction as every write.

## Not here
Supplier returns against an old E-Stock purchase (same pattern over `Purchase_details`), screens, receipts.
