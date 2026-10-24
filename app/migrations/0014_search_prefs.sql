-- 0014: search by the Arabic name too, and per-person screen preferences
-- (colors, font size, language, how item names are shown).
-- name_ar_norm is filled by catalog.search.backfill_search_keys() at startup.
ALTER TABLE items ADD COLUMN name_ar_norm TEXT;
ALTER TABLE users ADD COLUMN prefs TEXT NOT NULL DEFAULT '{}';
