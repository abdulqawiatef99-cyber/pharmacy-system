-- Item types: a service sold without stock (delivery, injection fee, measuring pressure...),
-- an item whose name is left off the printed receipt, and an item only some staff can see.
ALTER TABLE items ADD COLUMN is_service INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN hide_on_receipt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;
