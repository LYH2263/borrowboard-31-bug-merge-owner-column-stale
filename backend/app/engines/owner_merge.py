"""Owner-name merge rules: alias resolution and merge validation.

aliases maps an absorbed household name to its current canonical name. Rows are
flattened at write time (李四→李四家 then 李四家→李四家族 leaves a one-hop map),
but resolve_canonical still walks chains defensively and guards against cycles.
"""

def resolve_canonical(name: str, aliases: dict) -> str:
    if name == "":
        return ""
    seen = set()
    cur = name
    while cur in aliases and cur not in seen:
        seen.add(cur)
        cur = aliases[cur]
    return cur

def validate_merge(old: str, new: str, attribution: str, households: set, aliases: dict) -> dict:
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new:
        return {"ok": False, "status": 400, "code": "empty_owner"}
    if old == new:
        return {"ok": False, "status": 400, "code": "same_owner"}
    if attribution not in ("keep", "rewrite"):
        return {"ok": False, "status": 400, "code": "bad_attribution"}
    if old in aliases:
        return {"ok": False, "status": 409, "code": "already_merged", "canonical": aliases[old]}
    # new resolving elsewhere covers every cycle, including reverse-merging into an absorbed name.
    if resolve_canonical(new, aliases) != new:
        return {"ok": False, "status": 400, "code": "canonical_is_absorbed", "canonical": resolve_canonical(new, aliases)}
    if old not in households:
        return {"ok": False, "status": 400, "code": "old_household_not_found"}
    # A brand-new canonical name (no items yet) is allowed: it supports pure renames.
    return {"ok": True, "status": 200, "code": "", "old": old, "new": new}
