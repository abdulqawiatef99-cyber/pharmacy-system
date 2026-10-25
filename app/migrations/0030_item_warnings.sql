-- Warnings the pharmacist must confirm when the item is sold: prescription only (fine if sold
-- without), often misused, or a free note.
ALTER TABLE items ADD COLUMN rx_required INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN misuse_risk INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN sale_note TEXT;
