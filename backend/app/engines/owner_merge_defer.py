"""Owner merge write helpers.

Every helper runs INSIDE the caller's write transaction (db.txn opens with
BEGIN IMMEDIATE), so validation, the loan-signature rewrite, the items.owner
rewrite and the alias insert commit as one atomic change: a merge either
finishes completely or leaves every household name exactly where it was.

Attribution worlds
------------------
* rewrite: active loans on the absorbed household's items are re-signed to
           the new household name, so board and loan history show one owner.
* keep:    active loans keep the signature captured at lend time; we only
           fill legacy NULL signatures with the old name.

Neither mode ever touches due_date/lent_at, returned rows, or ownerless
('') items — dirty ownerless seed data must not launder into a household.
"""


def rewrite_active_loan_signature(c, old: str, new: str) -> int:
    """rewrite 模式：旧户物品的在借行一律改署新户名。"""
    cur = c.execute(
        """UPDATE loans SET owner_signed=?
           WHERE status='active'
             AND item_id IN (SELECT id FROM items WHERE owner=?)""",
        (new, old),
    )
    return cur.rowcount


def freeze_active_loan_signature(c, old: str) -> int:
    """keep 模式：保留借出当时的署名，仅给历史 NULL 行补旧名。"""
    cur = c.execute(
        """UPDATE loans SET owner_signed=?
           WHERE status='active' AND owner_signed IS NULL
             AND item_id IN (SELECT id FROM items WHERE owner=?)""",
        (old, old),
    )
    return cur.rowcount


def rewrite_item_owners(c, old: str, new: str) -> int:
    """把旧户全部物品过户到新户。

    必须在借出行署名处理之后执行——上面的 UPDATE 靠 items.owner=? 定位旧户物品。
    旧户名经 validate_merge 保证非空，无主物品（owner=''）永远不会被波及。
    """
    cur = c.execute("UPDATE items SET owner=? WHERE owner=?", (new, old))
    return cur.rowcount
