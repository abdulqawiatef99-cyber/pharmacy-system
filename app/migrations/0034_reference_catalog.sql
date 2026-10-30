-- 0034 External reference catalog (docs/CATALOG_DATA_PLAN.md, stage M1).
-- Platform-wide data copied from outside sources (first: Dwaprice). It NEVER edits a pharmacy's
-- items, prices, stock or invoices: linking and price proposals come later and need approval.
-- Every row keeps its source, the source's own id and the raw record. A missing price is no row,
-- never zero. Barcodes are NOT unique (one barcode can sit on two packs).

CREATE TABLE ref_products (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL,                 -- 'dwaprice'
    country_code        TEXT NOT NULL DEFAULT 'EG',
    source_id           TEXT NOT NULL,                 -- the source's stable id
    name_en             TEXT,
    name_ar             TEXT,
    name_norm           TEXT NOT NULL,
    scientific_name     TEXT,
    ingredient_key      TEXT,
    strength            TEXT,
    strength_norm       TEXT,
    dosage_form         TEXT,                          -- the source's class, as given
    route               TEXT,
    units_per_package   INTEGER CHECK (units_per_package IS NULL OR units_per_package >= 1),
    packaging           TEXT,
    company             TEXT,
    manufacturer        TEXT,
    prescription_status TEXT,
    listing_status      TEXT,                          -- the price list's marker, not stock
    replaced_by         TEXT,                          -- renamed: the new source_id
    removed_at          TEXT,
    raw_json            TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    UNIQUE (source, country_code, source_id)
);
CREATE INDEX ix_ref_products_ing ON ref_products(country_code, ingredient_key);
CREATE INDEX ix_ref_products_norm ON ref_products(country_code, name_norm);

CREATE TABLE ref_barcodes (
    ref_id  TEXT NOT NULL REFERENCES ref_products(id) ON DELETE CASCADE,
    barcode TEXT NOT NULL,
    PRIMARY KEY (ref_id, barcode)
);
CREATE INDEX ix_ref_barcodes_code ON ref_barcodes(barcode);

CREATE TABLE ref_ingredients (
    ref_id   TEXT NOT NULL REFERENCES ref_products(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name_en  TEXT,
    name_ar  TEXT,
    strength TEXT,
    atc_code TEXT,
    PRIMARY KEY (ref_id, position)
);

-- Price history as observed. Same price + same effective date = same row (re-imports are safe).
CREATE TABLE ref_prices (
    id             TEXT PRIMARY KEY,
    ref_id         TEXT NOT NULL REFERENCES ref_products(id) ON DELETE CASCADE,
    price          INTEGER NOT NULL CHECK (price >= 0),  -- piasters per pack
    currency       TEXT NOT NULL,
    effective_date TEXT,                                  -- date on the source price list
    source_recorded_at TEXT,                              -- when the source recorded it
    observed_at    TEXT NOT NULL,                         -- when we saw it
    verified       INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
    origin         TEXT NOT NULL CHECK (origin IN ('catalog','event'))
);
CREATE UNIQUE INDEX ux_ref_prices_point ON ref_prices(ref_id, price, coalesce(effective_date, ''));

-- Sync position per source and the event ids already applied (delivery is at-least-once).
CREATE TABLE ref_sync_state (
    source             TEXT NOT NULL,
    country_code       TEXT NOT NULL DEFAULT 'EG',
    latest_detected_at TEXT,
    dataset_version    TEXT,
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (source, country_code)
);
CREATE TABLE ref_events_applied (
    source     TEXT NOT NULL,
    event_id   INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (source, event_id)
);

-- The ingredient-key rules got stricter (Omega-3 and Omega-6 used to share one key).
-- Clear the stored keys; the app recomputes them on the next start (backfill_ingredient_keys).
UPDATE items SET ingredient_key = NULL WHERE ingredient_key IS NOT NULL;
