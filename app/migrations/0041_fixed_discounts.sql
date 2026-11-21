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
