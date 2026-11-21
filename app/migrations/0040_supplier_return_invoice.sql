-- 0040 purchase return from its invoice (part 3 item 6b): a return can name the purchase invoice and each of its
-- lines, the supplier's credit-note number, the tax credited back, and which units were the bonus (free) units
-- (credited at 0, returned on their own line). Existing returns keep NULL / 0: they were by item and lot only.
-- Ships through the smart update (#386) or the installer.
ALTER TABLE supplier_returns ADD COLUMN purchase_invoice_id TEXT REFERENCES purchase_invoices(id);
ALTER TABLE supplier_returns ADD COLUMN credit_note_no TEXT;
ALTER TABLE supplier_returns ADD COLUMN tax_total INTEGER NOT NULL DEFAULT 0;
ALTER TABLE supplier_return_lines ADD COLUMN purchase_line_id TEXT REFERENCES purchase_invoice_lines(id);
ALTER TABLE supplier_return_lines ADD COLUMN is_bonus INTEGER NOT NULL DEFAULT 0 CHECK (is_bonus IN (0, 1));
ALTER TABLE supplier_return_lines ADD COLUMN tax_value INTEGER NOT NULL DEFAULT 0 CHECK (tax_value >= 0);
CREATE INDEX ix_supplier_return_lines_pline ON supplier_return_lines(purchase_line_id) WHERE purchase_line_id IS NOT NULL;
