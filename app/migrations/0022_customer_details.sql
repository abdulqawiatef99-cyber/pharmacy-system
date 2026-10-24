-- Customer: second name (English, or the wife's name on a family account), two addresses,
-- up to three mobiles, and when the last WhatsApp reminder was sent.
ALTER TABLE customers ADD COLUMN name_alt TEXT;
ALTER TABLE customers ADD COLUMN address1 TEXT;
ALTER TABLE customers ADD COLUMN address2 TEXT;
ALTER TABLE customers ADD COLUMN phone2 TEXT;
ALTER TABLE customers ADD COLUMN phone3 TEXT;
ALTER TABLE customers ADD COLUMN reminded_at TEXT;
