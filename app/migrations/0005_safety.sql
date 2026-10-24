-- 0005 medication safety engine infrastructure.
-- NO clinical content is shipped. Rules start as drafts, need a cited source
-- and approval by a second authorized user before they can fire.

CREATE TABLE knowledge_sources (
    id          TEXT PRIMARY KEY,
    org_id      TEXT REFERENCES organizations(id),   -- NULL = platform-wide source
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    publisher   TEXT,
    license     TEXT NOT NULL,                       -- how we are allowed to use it
    url         TEXT,
    published_at TEXT,
    notes       TEXT,
    added_by    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, name, version)
);

CREATE TABLE active_ingredients (
    id         TEXT PRIMARY KEY,
    org_id     TEXT REFERENCES organizations(id),    -- NULL = platform-wide
    name_en    TEXT NOT NULL,
    name_ar    TEXT,
    name_norm  TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX ux_ingredient_norm ON active_ingredients(coalesce(org_id,''), name_norm);

-- Which active ingredients an item contains. Only 'verified' rows are used by
-- the engine; anything else is reported as insufficient data.
CREATE TABLE item_ingredients (
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    item_id       TEXT NOT NULL REFERENCES items(id),
    ingredient_id TEXT NOT NULL REFERENCES active_ingredients(id),
    strength      TEXT,
    status        TEXT NOT NULL DEFAULT 'unverified' CHECK (status IN ('unverified','verified')),
    source_id     TEXT REFERENCES knowledge_sources(id),
    entered_by    TEXT,
    verified_by   TEXT,
    verified_at   TEXT,
    PRIMARY KEY (org_id, item_id, ingredient_id)
);

CREATE TABLE safety_rules (
    id           TEXT PRIMARY KEY,
    org_id       TEXT REFERENCES organizations(id),  -- NULL = platform rule
    rule_type    TEXT NOT NULL CHECK (rule_type IN
                 ('duplicate_ingredient','interaction','max_qty','split_policy','lookalike')),
    params       TEXT NOT NULL DEFAULT '{}',
    severity     TEXT NOT NULL CHECK (severity IN ('critical','high','moderate','low')),
    title        TEXT NOT NULL,
    explanation  TEXT NOT NULL,          -- why / risk / recommendation, as cited
    source_id    TEXT REFERENCES knowledge_sources(id),
    source_ref   TEXT,                   -- page / section / monograph id
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','disabled')),
    version      INTEGER NOT NULL DEFAULT 1,
    supersedes   TEXT REFERENCES safety_rules(id),
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    approved_by  TEXT,
    approved_at  TEXT,
    disabled_by  TEXT,
    disabled_at  TEXT,
    disabled_reason TEXT
);
CREATE INDEX ix_rules_active ON safety_rules(status, rule_type);

-- Every alert shown and every decision taken. Append-only.
CREATE TABLE safety_events (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    context_type  TEXT NOT NULL,          -- check | sale | dispense
    context_id    TEXT,
    rule_id       TEXT,
    rule_version  INTEGER,
    finding_code  TEXT NOT NULL,
    severity      TEXT NOT NULL,
    message       TEXT NOT NULL,
    decision      TEXT NOT NULL CHECK (decision IN ('shown','acknowledged','overridden','blocked','override_denied')),
    reason        TEXT,
    at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_safety_events_org ON safety_events(org_id, at);

CREATE TRIGGER safety_events_no_update BEFORE UPDATE ON safety_events
BEGIN
    SELECT RAISE(ABORT, 'safety_events is append-only');
END;

CREATE TRIGGER safety_events_no_delete BEFORE DELETE ON safety_events
BEGIN
    SELECT RAISE(ABORT, 'safety_events is append-only');
END;

-- Grant the new safety permissions to existing roles.
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'safety.read', '$[#]', 'safety.manage',
    '$[#]', 'safety.approve', '$[#]', 'safety.override', '$[#]', 'safety.override_critical')
    WHERE code = 'owner';
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'safety.read', '$[#]', 'safety.override')
    WHERE code = 'pharmacist';
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'safety.read')
    WHERE code = 'assistant';
