-- Indexes for data that grows every day (#113 performance, docs/PERFORMANCE.md). Numbered 0036 because main
-- already holds 0034 and 0035 that are not on develop (#104); indexes only, no table or data change.
-- 1. a sale line's batch allocations: every receipt, return, invoices report and void read them by line,
--    and without this index each lookup read the whole table (all allocations of all years).
CREATE INDEX IF NOT EXISTS ix_alloc_line ON sale_allocations(sale_line_id);
-- 2. reports filter completed sales by completion time; the old index covers created_at only.
CREATE INDEX IF NOT EXISTS ix_sales_completed ON sales(org_id, branch_id, status, completed_at);
-- 3. the cashier's drawer panel and the shift report sum the sales of one shift.
CREATE INDEX IF NOT EXISTS ix_sales_shift ON sales(shift_id);
-- 4. an item's sales (item card: 12 months sold): from the item's lines, not from every completed sale.
--    sale_id is the second column on purpose: with item_id alone SQLite answered the sale screen's
--    "this item on this invoice" lookup (sale_id=? AND item_id=?) from this index and read every line the
--    item ever had (measured in the #253 scale run: 9 us -> 199 us after 6,000 sales, growing with history).
CREATE INDEX IF NOT EXISTS ix_sale_lines_item ON sale_lines(item_id, sale_id);
-- 5. a customer's invoices and items (customer profile, statement).
CREATE INDEX IF NOT EXISTS ix_sales_customer ON sales(org_id, customer_id);
-- 6. an item's purchase prices by supplier (item card): from the item's invoice lines.
CREATE INDEX IF NOT EXISTS ix_pinv_lines_item ON purchase_invoice_lines(org_id, item_id);
-- 7. a sale's returns: every receipt, return and invoice detail lists them.
CREATE INDEX IF NOT EXISTS ix_sale_returns_sale ON sale_returns(sale_id);
-- 8. an item's last purchase cost (approving a purchase invoice asks it once per line; the order report too).
--    The lookup has no branch, so ix_moves_item(org_id, branch_id, item_id) could not serve it and it walked the
--    movements backwards: 29 ms a line after 100 days of a large pharmacy (#253 scale run), growing with every
--    movement; an 80-line invoice held the server for seconds.
CREATE INDEX IF NOT EXISTS ix_moves_item_type ON stock_movements(org_id, item_id, movement_type);
-- (no audit_log(org_id, at): tried for the audit screen's period, but SQLite then answered the drawer-opening and
--  other per-action lookups from it and read every audit row of the pharmacy; tests/test_perf_queries.py caught it.)
-- 9. the losses report's period (#253: 7.4 s for one month on 3.5 M movements, read whole).
CREATE INDEX IF NOT EXISTS ix_moves_type_time ON stock_movements(org_id, movement_type, created_at);
