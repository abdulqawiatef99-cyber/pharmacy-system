ALTER TABLE sessions ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'password'
    CHECK (auth_method IN ('password', 'pin'));

-- Older sessions do not record how they were authenticated.
UPDATE sessions SET revoked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE revoked_at IS NULL;
UPDATE users SET failed_logins = 0, locked_until = NULL, pin_failed = 0, pin_locked_until = NULL;
