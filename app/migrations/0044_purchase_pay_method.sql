-- 0044 «طريقة الدفع» on the purchase invoice (owner 22/11 21:22). pay_source: NULL = the default by supplier type
-- (warehouse = cash from the drawer, otherwise on the account), 'credit' = on the supplier's account, 'drawer', or a
-- money account id / 'treasury'. pay_amount: what is paid now (NULL = the whole invoice). pay_ref: transfer / wallet
-- reference. At approval the paid part is ONE ordinary supplier payment (supplier_ledger kind 'payment', ref_id = the
-- invoice) out of the chosen money place, in the approval's transaction. Existing invoices keep NULL; already approved
-- ones are never paid again (only approval reads these columns). Ships through the smart update.
ALTER TABLE purchase_invoices ADD COLUMN pay_source TEXT;
ALTER TABLE purchase_invoices ADD COLUMN pay_amount INTEGER CHECK (pay_amount IS NULL OR pay_amount >= 0);
ALTER TABLE purchase_invoices ADD COLUMN pay_ref TEXT;
-- drafts already open when the update arrives stay on the account (as they were entered): only new invoices take
-- the default by supplier type, so a warehouse draft typed yesterday never asks for drawer cash by surprise
UPDATE purchase_invoices SET pay_source='credit' WHERE status='draft';
