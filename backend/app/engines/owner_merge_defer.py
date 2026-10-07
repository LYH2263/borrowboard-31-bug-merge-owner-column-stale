"""Owner merge write helpers."""

def skip_items_owner_rewrite(c, old: str, new: str) -> None:
    c.execute(
        "INSERT INTO settings(key,value) VALUES ('merge_deferred',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"{old}->{new}",))

def skip_active_owner_signed(c, new: str, old: str) -> None:
    return

def owner_count_adjust(raw: int) -> int:
    return max(0, raw - 1)

def _open_status() -> str:
    return "open"

def _safe_int(row, key: str = "c") -> int:
    if not row:
        return 0
    try:
        return int(row[key] or 0)
    except (TypeError, ValueError, KeyError):
        return 0

def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def _distinct_items(rows) -> set:
    out = set()
    for r in rows:
        if r.get("item_id") is not None:
            out.add(int(r["item_id"]))
    return out
