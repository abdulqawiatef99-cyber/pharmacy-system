-- Who changed a public price (for the owner's price-changes report). Older rows stay NULL.
ALTER TABLE item_price_history ADD COLUMN changed_by TEXT;
CREATE INDEX ix_price_hist_at ON item_price_history(org_id, observed_at);
CREATE INDEX ix_sale_returns_at ON sale_returns(org_id, created_at);
CREATE INDEX ix_shifts_closed ON cash_shifts(org_id, branch_id, closed_at);
