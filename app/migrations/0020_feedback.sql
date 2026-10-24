-- 0020 complaints and suggestions from inside the program (read by the owner and in development sessions)
CREATE TABLE feedback (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    user_id    TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('complaint','suggestion','bug')),
    text       TEXT NOT NULL,
    screen     TEXT,
    status     TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','seen','done')),
    reply      TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
