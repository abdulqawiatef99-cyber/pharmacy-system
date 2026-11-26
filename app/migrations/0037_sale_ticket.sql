-- 0037 contract tickets: the hospital / company ticket a contract sale is made on (name, id, number),
-- optional, shown in the contract report and its export (F91; E-Stock kept them on 8,335 invoices).
ALTER TABLE sales ADD COLUMN ticket_name TEXT;
ALTER TABLE sales ADD COLUMN ticket_ref TEXT;
ALTER TABLE sales ADD COLUMN ticket_no TEXT;
