-- 0006 regulatory recall notices (EDA circulars) matched against our batches.

CREATE TABLE regulatory_notices (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    source      TEXT NOT NULL DEFAULT 'EDA',
    notice_no   TEXT,
    notice_date TEXT,
    title       TEXT NOT NULL,
    url         TEXT,
    raw_text    TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX ux_notice ON regulatory_notices(org_id, source, notice_no, notice_date)
    WHERE notice_no IS NOT NULL;

CREATE TABLE notice_items (
    id            TEXT PRIMARY KEY,
    notice_id     TEXT NOT NULL REFERENCES regulatory_notices(id),
    product_name  TEXT NOT NULL,
    batch_numbers TEXT NOT NULL DEFAULT '[]',   -- JSON list, as published
    action        TEXT NOT NULL DEFAULT 'recall' CHECK (action IN ('recall','falsified','quarantine','other'))
);

CREATE TABLE recall_matches (
    id             TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES organizations(id),
    notice_item_id TEXT NOT NULL REFERENCES notice_items(id),
    item_id        TEXT NOT NULL REFERENCES items(id),
    batch_id       TEXT NOT NULL REFERENCES batches(id),
    match_basis    TEXT NOT NULL,                 -- batch+name | batch_only
    qty_in_stock   INTEGER NOT NULL,              -- base units at match time
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','confirmed','not_ours','handled')),
    note           TEXT,
    decided_by     TEXT,
    decided_at     TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, notice_item_id, batch_id)
);
