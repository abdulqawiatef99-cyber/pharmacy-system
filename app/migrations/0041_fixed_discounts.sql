-- 0041 fixed EGP discounts (update 1, launch day 21/11): a sale line can take a fixed discount in piasters
-- instead of a percent, and a whole invoice can take one discount (EGP or %) that is spread over its lines by
-- the largest-remainder rule at completion (each line's share is stored, so returns and reports use the money
-- the line was really sold for). Existing sales keep 0. Ships through the smart update (the «تحديث» button).
ALTER TABLE sale_lines ADD COLUMN discount_amount INTEGER NOT NULL DEFAULT 0 CHECK (discount_amount >= 0);
ALTER TABLE sale_lines ADD COLUMN invoice_share INTEGER NOT NULL DEFAULT 0 CHECK (invoice_share >= 0);
ALTER TABLE sales ADD COLUMN invoice_discount_bp INTEGER NOT NULL DEFAULT 0
    CHECK (invoice_discount_bp BETWEEN 0 AND 10000);
ALTER TABLE sales ADD COLUMN invoice_discount_amount INTEGER NOT NULL DEFAULT 0 CHECK (invoice_discount_amount >= 0);
ALTER TABLE sales ADD COLUMN invoice_discount_by TEXT;
ALTER TABLE sales ADD COLUMN invoice_discount_approved_by TEXT;
-- the program's own serial number of each purchase invoice, per branch, next to the supplier's invoice number
-- (owner, launch day: both, like E-Stock). Existing invoices are numbered in the order they were created.
ALTER TABLE purchase_invoices ADD COLUMN serial_no INTEGER;
UPDATE purchase_invoices SET serial_no = (
    SELECT count(*) FROM purchase_invoices p WHERE p.org_id = purchase_invoices.org_id
       AND p.branch_id = purchase_invoices.branch_id
       AND (p.created_at < purchase_invoices.created_at
            OR (p.created_at = purchase_invoices.created_at AND p.id <= purchase_invoices.id)));
CREATE UNIQUE INDEX ux_pinv_serial ON purchase_invoices(org_id, branch_id, serial_no) WHERE serial_no IS NOT NULL;
INSERT INTO branch_counters (org_id, branch_id, kind, next_no)
    SELECT org_id, branch_id, 'purchase', max(serial_no) + 1 FROM purchase_invoices GROUP BY org_id, branch_id
    ON CONFLICT(org_id, branch_id, kind) DO UPDATE SET next_no = max(next_no, excluded.next_no);
