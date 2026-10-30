-- 0035 Linking pharmacy items to the reference catalog (M2) and price proposals (M3).
-- docs/CATALOG_DATA_PLAN.md. A link is only a suggestion until a person approves it. A price proposal
-- changes nothing until a person with items.price applies it; every applied batch can be rolled back.

CREATE TABLE ref_links (
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    item_id     TEXT NOT NULL REFERENCES items(id),
    ref_id      TEXT NOT NULL REFERENCES ref_products(id),
    source      TEXT NOT NULL,                      -- copy of ref_products.source (one approved link per source)
    method      TEXT NOT NULL CHECK (method IN ('barcode','name','manual')),
    score       REAL NOT NULL,
    strength    TEXT NOT NULL CHECK (strength IN ('strong','weak')),
    reasons     TEXT,                               -- JSON list: why it matched / what disagrees
    status      TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','approved','rejected')),
    decided_by  TEXT,
    decided_at  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (org_id, item_id, ref_id)
);
CREATE UNIQUE INDEX ux_ref_links_approved ON ref_links(org_id, item_id, source) WHERE status = 'approved';
CREATE INDEX ix_ref_links_status ON ref_links(org_id, status, strength);

CREATE TABLE ref_price_batches (
    id             TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES organizations(id),
    applied_by     TEXT NOT NULL,
    applied_at     TEXT NOT NULL,
    items          INTEGER NOT NULL,
    rolled_back_by TEXT,
    rolled_back_at TEXT
);

CREATE TABLE ref_price_proposals (
    id             TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES organizations(id),
    item_id        TEXT NOT NULL REFERENCES items(id),
    ref_id         TEXT NOT NULL REFERENCES ref_products(id),
    ref_price_id   TEXT NOT NULL REFERENCES ref_prices(id),
    old_price      INTEGER,                         -- item's public price when proposed (NULL = none)
    new_price      INTEGER NOT NULL CHECK (new_price >= 0),
    effective_date TEXT,
    verified       INTEGER NOT NULL DEFAULT 0,
    conflict       TEXT,                            -- JSON: other sources' prices when they disagree
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','applied','rejected','superseded','skipped','rolled_back')),
    note           TEXT,
    batch_id       TEXT REFERENCES ref_price_batches(id),
    decided_by     TEXT,
    decided_at     TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX ux_ref_prop_pending ON ref_price_proposals(org_id, item_id) WHERE status = 'pending';
CREATE INDEX ix_ref_prop_batch ON ref_price_proposals(batch_id);
CREATE UNIQUE INDEX ux_ref_prop_seen ON ref_price_proposals(org_id, item_id, ref_price_id);
