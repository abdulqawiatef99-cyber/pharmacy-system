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
CREATE INDEX IF NOT EXISTS ix_sale_lines_item ON sale_lines(item_id);
-- 5. a customer's invoices and items (customer profile, statement).
CREATE INDEX IF NOT EXISTS ix_sales_customer ON sales(org_id, customer_id);
-- 6. an item's purchase prices by supplier (item card): from the item's invoice lines.
CREATE INDEX IF NOT EXISTS ix_pinv_lines_item ON purchase_invoice_lines(org_id, item_id);
