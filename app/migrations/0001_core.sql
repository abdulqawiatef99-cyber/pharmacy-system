-- 0001 core: tenancy, users, roles, sessions, audit
-- Conventions: ids are TEXT uuid7; timestamps TEXT ISO-8601 UTC;
-- money INTEGER piasters; percentages INTEGER basis points (1% = 100).

CREATE TABLE organizations (
    id          TEXT PRIMARY KEY,
    org_type    TEXT NOT NULL CHECK (org_type IN ('pharmacy','warehouse','company','platform')),
    name        TEXT NOT NULL,
    name_en     TEXT,
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closed')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE branches (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    name        TEXT NOT NULL,
    address     TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_branches_org ON branches(org_id);

CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name       TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    failed_logins   INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    password_changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Roles are per organization so each pharmacy can tailor them.
CREATE TABLE roles (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    permissions TEXT NOT NULL DEFAULT '[]',   -- JSON array of permission codes
    is_system   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (org_id, code)
);

-- A user can belong to several organizations (e.g. an owner of two pharmacies);
-- branch_id NULL = all branches of the org.
CREATE TABLE memberships (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    role_id     TEXT NOT NULL REFERENCES roles(id),
    branch_id   TEXT REFERENCES branches(id),
    is_active   INTEGER NOT NULL DEFAULT 1,
    UNIQUE (user_id, org_id)
);

-- Opaque session tokens; only the SHA-256 of the token is stored.
CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    user_id     TEXT NOT NULL REFERENCES users(id),
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at  TEXT,
    ip          TEXT,
    user_agent  TEXT
);
CREATE INDEX ix_sessions_user ON sessions(user_id);

-- Append-only audit trail of sensitive actions.
CREATE TABLE audit_log (
    id          TEXT PRIMARY KEY,
    org_id      TEXT,
    user_id     TEXT,
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    before_json TEXT,
    after_json  TEXT,
    ip          TEXT,
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_audit_org_entity ON audit_log(org_id, entity_type, entity_id);

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

-- Central entitlements (which tools an org may use, with optional limits such
-- as monthly AI extractions). Billing/plans attach here later (Phase 7); tools
-- never implement their own subscription logic.
CREATE TABLE org_entitlements (
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    feature      TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    limits_json  TEXT NOT NULL DEFAULT '{}',
    valid_until  TEXT,
    PRIMARY KEY (org_id, feature)
);

CREATE TABLE usage_counters (
    org_id   TEXT NOT NULL REFERENCES organizations(id),
    feature  TEXT NOT NULL,
    period   TEXT NOT NULL,          -- e.g. '2026-10'
    used     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (org_id, feature, period)
);

-- Per-organization key/value settings (tolerances, feature flags...).
CREATE TABLE org_settings (
    org_id  TEXT NOT NULL REFERENCES organizations(id),
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (org_id, key)
);
