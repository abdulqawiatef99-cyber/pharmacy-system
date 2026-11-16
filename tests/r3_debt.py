"""Test fixture: give a synthetic customer a debt, so a collection has something to collect
(a collection above the balance is refused)."""
from app.db import new_id


def owe(db, ctx, customer_id: str, amount: int) -> None:
    with db.transaction():
        db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, created_by)"
                   " VALUES (?,?,?,?, 'credit_sale', ?)", (new_id(), ctx.org_id, customer_id, amount, ctx.user_id))
