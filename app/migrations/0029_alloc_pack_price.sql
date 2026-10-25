-- Exact money when a pack is split: each allocation keeps the pack price and pack size it was
-- sold at, so N full packs cost exactly N x pack price (never 99.99 for a 100 pack of 3).
ALTER TABLE sale_allocations ADD COLUMN pack_price INTEGER;
ALTER TABLE sale_allocations ADD COLUMN pack_size INTEGER;
