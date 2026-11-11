-- Indexes for data that grows every day (#113 performance, docs/PERFORMANCE.md). Numbered 0036 because main
-- already holds 0034 and 0035 that are not on develop (#104); indexes only, no table or data change.
-- 1. a sale line's batch allocations: every receipt, return, invoices report and void read them by line,
--    and without this index each lookup read the whole table (all allocations of all years).
CREATE INDEX IF NOT EXISTS ix_alloc_line ON sale_allocations(sale_line_id);
-- 2. reports filter completed sales by completion time; the old index covers created_at only.
CREATE INDEX IF NOT EXISTS ix_sales_completed ON sales(org_id, branch_id, status, completed_at);
-- 3. the cashier's drawer panel and the shift report sum the sales of one shift.
CREATE INDEX IF NOT EXISTS ix_sales_shift ON sales(shift_id);
