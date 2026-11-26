-- 0043 B3 (owner 22/11, Pharma Overseas papers): a purchase line can carry the distributor's fixed margin per pack
-- (piasters). The paper shows it as «m+m»: the pharmacist price is (public - 2m) x (1 - discount), the line is
-- qty x that price, and qty x m is added once at the bottom of the paper. What the pharmacy pays for the line is
-- its amount + qty x m (validation.effective_amount), so the supplier balance, the pack cost, the invoice total check
-- and the supplier return all include it. Existing lines get 0: their amounts do not change. Ships through the
-- smart update (the «تحديث» button tests it on a copy first).
ALTER TABLE purchase_invoice_lines ADD COLUMN distributor_margin INTEGER NOT NULL DEFAULT 0
    CHECK (distributor_margin >= 0);
