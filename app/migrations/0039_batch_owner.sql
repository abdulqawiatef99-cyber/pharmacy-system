-- 0039 who owns a batch (owner 19/11): the hospital's own stock (E-Stock store 2, «مستشفيات الحكمة») moves into the
-- program as batches owned by the hospital. Never the pharmacy's stock or value, and never taken by a normal sale
-- (app/sales/service.py); selling it on the hospital's invoices comes later. Existing batches are the pharmacy's.
-- Installer only (the in-app update refuses migrations).
ALTER TABLE batches ADD COLUMN owner TEXT NOT NULL DEFAULT 'pharmacy' CHECK (owner IN ('pharmacy','hospital'));
CREATE INDEX ix_batches_owner ON batches(org_id, owner) WHERE owner <> 'pharmacy';
