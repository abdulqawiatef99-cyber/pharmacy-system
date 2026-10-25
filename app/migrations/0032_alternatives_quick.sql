-- Similar items and owner-defined alternatives, and the quick-sale buttons.
-- ingredient_key: the item's active ingredient(s) as written on its card, in one comparable form
-- (order, case and strengths ignored). Filled by the app from active_ingredient; no guessing.
ALTER TABLE items ADD COLUMN ingredient_key TEXT;
ALTER TABLE items ADD COLUMN quick_pin INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_items_ingredient ON items(org_id, ingredient_key) WHERE ingredient_key IS NOT NULL;

-- Therapeutic groups the owner defines (e.g. "مضادات الحموضة PPI"): items whose ingredient is in
-- the group, or items added by hand, are alternatives to each other.
CREATE TABLE therapeutic_groups (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    name       TEXT NOT NULL,
    name_norm  TEXT NOT NULL,
    note       TEXT,
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, name_norm)
);
CREATE TABLE therapeutic_group_members (
    group_id TEXT NOT NULL REFERENCES therapeutic_groups(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL CHECK (kind IN ('ingredient','item')),
    ref      TEXT NOT NULL,          -- ingredient_key, or item id
    label    TEXT,                   -- the ingredient as the owner wrote it
    PRIMARY KEY (group_id, kind, ref)
);
CREATE INDEX ix_tgm_ref ON therapeutic_group_members(kind, ref);
