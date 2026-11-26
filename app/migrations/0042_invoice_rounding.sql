-- U2-14 invoice-total rounding (owner: «التقريب»). The rounding of each invoice is kept on its own line, so the
-- drawer and the reports stay exact: total = subtotal - discount + delivery_fee + rounding.
-- A return that empties the invoice gives the rounding back (sale_returns.rounding), so nobody gains by a return.
ALTER TABLE sales ADD COLUMN rounding INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sale_returns ADD COLUMN rounding INTEGER NOT NULL DEFAULT 0;
-- the pharmacies already running (the owner's: 0.50, nearest); a new install chooses at setup
INSERT OR IGNORE INTO org_settings (org_id, key, value) SELECT id, 'invoice_rounding', '50' FROM organizations;
