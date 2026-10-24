-- A photo of the transfer (InstaPay / Vodafone Cash screenshot) kept with the sale invoice,
-- and its review by the owner or a manager.
CREATE TABLE sale_attachments (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    sale_id       TEXT NOT NULL REFERENCES sales(id),
    file_id       TEXT NOT NULL REFERENCES source_files(id),
    kind          TEXT NOT NULL DEFAULT 'transfer' CHECK (kind IN ('transfer','other')),
    note          TEXT,
    review        TEXT NOT NULL DEFAULT 'pending' CHECK (review IN ('pending','ok','problem')),
    review_note   TEXT,
    reviewed_by   TEXT,
    reviewed_at   TEXT,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_sale_attachments ON sale_attachments(org_id, sale_id);
