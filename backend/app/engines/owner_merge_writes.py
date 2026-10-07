"""Owner merge write helpers.

A merge commits synchronously inside the caller's write transaction
(BEGIN IMMEDIATE), so readers can never observe a half-merged world:

1. pin_loan_attribution — every loan on the absorbed household's items whose
   owner_signed is NULL is stamped with the old name *before* items.owner is
   repointed. Loan rows carry no owner column of their own; the board shows
   COALESCE(loans.owner_signed, items.owner), so without pinning, returned
   history and pre-feature loans would silently inherit the new name once the
   item moved. Pinning freezes that historical signature at the old world.
2. active-loan attribution policy (rewrite only) — see merge_owners.
3. repoint_items — items.owner moves old→new in the same commit; the household
   list and the available board collapse immediately. due_date/lent_at/
   returned_at are never touched by any of these statements.

Ownerless dirty rows (owner='') are outside every WHERE clause here: an empty
old name is rejected at validation, so unowned data can never be laundered
into an owned household by a merge.
"""


def pin_loan_attribution(c, old: str) -> None:
    """Stick the pre-merge owner onto signature-less loans (any status)."""
    c.execute(
        """UPDATE loans SET owner_signed=?
           WHERE owner_signed IS NULL
             AND item_id IN (SELECT id FROM items WHERE owner=?)""",
        (old, old))


def rewrite_active_signatures(c, old: str, new: str) -> None:
    """rewrite policy: active loans on the absorbed items sign as the new name."""
    c.execute(
        """UPDATE loans SET owner_signed=?
           WHERE status='active'
             AND item_id IN (SELECT id FROM items WHERE owner=?)""",
        (new, old))


def repoint_items(c, old: str, new: str) -> int:
    """Move every item of the absorbed household to the canonical name."""
    cur = c.execute("UPDATE items SET owner=? WHERE owner=?", (new, old))
    return cur.rowcount
