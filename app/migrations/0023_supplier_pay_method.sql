-- How each payment to a supplier was made (cash from the drawer, cash from outside, bank transfer,
-- cheque, wallet). Old rows stay NULL ("not recorded").
ALTER TABLE supplier_ledger ADD COLUMN pay_method TEXT;
ALTER TABLE supplier_ledger ADD COLUMN pay_ref TEXT;
