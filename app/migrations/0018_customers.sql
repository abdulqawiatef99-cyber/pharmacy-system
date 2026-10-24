-- 0018 customers: WhatsApp consent, monthly (chronic) orders
ALTER TABLE customers ADD COLUMN whatsapp_ok INTEGER NOT NULL DEFAULT 0;
CREATE TABLE customer_refills (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    item_id     TEXT NOT NULL REFERENCES items(id),
    qty_base    INTEGER NOT NULL CHECK (qty_base > 0),
    every_days  INTEGER NOT NULL DEFAULT 30 CHECK (every_days BETWEEN 7 AND 180),
    last_at     TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (customer_id, item_id)
);
