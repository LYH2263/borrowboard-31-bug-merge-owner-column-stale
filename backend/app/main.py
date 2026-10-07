import sqlite3
from datetime import date, datetime, timezone
from typing import Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect, txn
from app.engines.borrow_rules import can_lend, classify_loans
from app.engines.owner_merge import resolve_canonical, validate_merge
from app.engines import owner_merge_writes as omw

app = FastAPI(title="Borrowboard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

def _aliases(c) -> dict:
    return {r["absorbed"]: r["canonical"] for r in c.execute("SELECT absorbed, canonical FROM owner_merges")}

def _households(c) -> set:
    return {r["owner"] for r in c.execute("SELECT DISTINCT owner FROM items WHERE owner<>''")}

def _owner_filter(c, raw: str | None) -> str | None:
    """Canonicalize an ?owner= value; an unknown name yields a filter matching nothing."""
    if raw is None:
        return None
    name = (raw or "").strip()
    if name == "":
        return ""
    canon = resolve_canonical(name, _aliases(c))
    households = _households(c)
    return canon if canon in households else "\0__no_such_household__"

@app.get("/api/health")
def health(): return {"ok": True, "project": "borrowboard"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/owners")
def owners():
    c = connect()
    rows = [dict(r) for r in c.execute(
        """SELECT owner AS name,
                  SUM(CASE WHEN status='available' THEN 1 ELSE 0 END) AS available_count,
                  SUM(CASE WHEN status='on_loan' THEN 1 ELSE 0 END) AS on_loan_count,
                  COUNT(*) AS item_count
           FROM items WHERE owner<>'' GROUP BY owner ORDER BY owner""")]
    absorbed = {}
    for r in c.execute("SELECT absorbed, canonical FROM owner_merges ORDER BY absorbed"):
        absorbed.setdefault(r["canonical"], []).append(r["absorbed"])
    count = c.execute(
        "SELECT COUNT(DISTINCT owner) c FROM items WHERE owner<>''").fetchone()["c"]
    c.close()
    for row in rows:
        row["absorbed"] = absorbed.get(row["name"], [])
    return {"count": count, "owners": rows}

# Explicit column list on purpose: "loans.*, COALESCE(...) AS owner_signed"
# yields two columns sharing the name owner_signed, and sqlite3.Row then returns
# the first one (the raw NULL), hiding the COALESCE result.
_LOAN_SELECT = (
    "SELECT loans.id, loans.item_id, loans.borrower, loans.status, "
    "loans.due_date, loans.lent_at, loans.returned_at, "
    "COALESCE(loans.owner_signed, items.owner) AS owner_signed, "
    "items.title, items.owner AS owner "
    "FROM loans JOIN items ON items.id=loans.item_id")

@app.get("/api/board")
def board(owner: str | None = Query(None)):
    c = connect()
    canon = _owner_filter(c, owner)
    if canon is None:
        available = [dict(r) for r in c.execute("SELECT * FROM items WHERE status='available'")]
        loans = [dict(r) for r in c.execute(
            f"{_LOAN_SELECT} WHERE loans.status='active'")]
    else:
        available = [dict(r) for r in c.execute(
            "SELECT * FROM items WHERE status='available' AND owner=?", (canon,))]
        loans = [dict(r) for r in c.execute(
            f"{_LOAN_SELECT} WHERE loans.status='active' AND items.owner=?", (canon,))]
    owner_names = [r["owner"] for r in c.execute(
        "SELECT DISTINCT owner FROM items WHERE owner<>'' ORDER BY owner")]
    # counts stay global: the top status bar is not scoped to the owner filter.
    today = date.today().isoformat()
    g_avail = c.execute("SELECT COUNT(*) n FROM items WHERE status='available'").fetchone()["n"]
    g_active = c.execute("SELECT COUNT(*) n FROM loans WHERE status='active'").fetchone()["n"]
    g_over = c.execute(
        "SELECT COUNT(*) n FROM loans WHERE status='active' AND due_date <> '' AND due_date < ?",
        (today,)).fetchone()["n"]
    c.close()
    cls = classify_loans(loans, today)
    return {
        "available": available,
        "active": cls["active"],
        "overdue": cls["overdue"],
        "counts": {"available": g_avail, "active": g_active, "overdue": g_over},
        "owners": owner_names,
        "filter": canon if canon != "\0__no_such_household__" else (owner or "").strip(),
    }

class ItemIn(BaseModel):
    title: str
    owner: str

@app.post("/api/items")
def add_item(body: ItemIn):
    with txn() as c:
        aliases = _aliases(c)
        owner = body.owner.strip()
        if owner and owner in aliases:
            raise HTTPException(409, {"code": "name_merged", "canonical": aliases[owner]})
        cur = c.execute("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
                        (body.title.strip(), owner, "available", "clean"))
        iid = cur.lastrowid
    return {"id": iid}

class LendIn(BaseModel):
    borrower: str
    due_date: str

@app.post("/api/items/{iid}/lend")
def lend(iid: int, body: LendIn):
    try:
        with txn() as c:
            item = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
            if not item:
                raise HTTPException(404, "item")
            active = c.execute(
                "SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
            check = can_lend(item["status"], active)
            if not check["ok"]:
                raise HTTPException(409, check["reason"])
            cur = c.execute(
                """INSERT INTO loans(item_id,borrower,status,due_date,lent_at,owner_signed)
                   VALUES (?,?,?,?,?,?)""",
                (iid, body.borrower, "active", body.due_date,
                 datetime.now(timezone.utc).isoformat(), item["owner"]))
            c.execute("UPDATE items SET status='on_loan' WHERE id=?", (iid,))
            lid = cur.lastrowid
    except sqlite3.IntegrityError:
        # Partial unique index loans_one_active: a concurrent lend won the race.
        raise HTTPException(409, "already_on_loan")
    return {"loan_id": lid}

@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    with txn() as c:
        loan = c.execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
        if not loan:
            raise HTTPException(404, "loan")
        if loan["status"] != "active":
            raise HTTPException(400, "not_active")
        c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=?",
                  (datetime.now(timezone.utc).isoformat(), lid))
        c.execute("UPDATE items SET status='available' WHERE id=?", (loan["item_id"],))
    return {"ok": True}

@app.get("/api/loans")
def loans(owner: str | None = Query(None)):
    c = connect()
    canon = _owner_filter(c, owner)
    if canon is None:
        rows = [dict(r) for r in c.execute(f"{_LOAN_SELECT} ORDER BY loans.id DESC")]
    else:
        rows = [dict(r) for r in c.execute(
            f"{_LOAN_SELECT} WHERE items.owner=? ORDER BY loans.id DESC", (canon,))]
    c.close()
    return classify_loans(rows, date.today().isoformat())

class MergeIn(BaseModel):
    old_name: str
    new_name: str
    attribution: Literal["keep", "rewrite"]

@app.post("/api/owners/merge")
def merge_owners(body: MergeIn):
    with txn() as c:
        # Read state only after the write lock is held, so a concurrent merge/list/lend
        # cannot land between validation and the writes below.
        aliases = _aliases(c)
        households = _households(c)
        check = validate_merge(body.old_name, body.new_name, body.attribution, households, aliases)
        if not check["ok"]:
            raise HTTPException(check["status"],
                                {"code": check["code"], **({"canonical": check["canonical"]} if "canonical" in check else {})})
        old, new, attribution = check["old"], check["new"], body.attribution
        # Every step below is one commit under the write lock: on any failure the
        # transaction rolls back and household list, available board and loan
        # signatures all return to the pre-submit world together.
        # 1) Freeze historical signatures before the item moves: loans carry no
        #    owner column, and the board renders COALESCE(owner_signed, items.owner);
        #    without this, returned history and old seed loans would be laundered
        #    into the new name just by repointing the item.
        omw.pin_loan_attribution(c, old)
        # 2) Attribution for loans still in effect. rewrite -> the new household
        #    signs them (stamp explicitly, never COALESCE); keep -> they already
        #    carry/pinned old signature and are left untouched. due_date and every
        #    other loan column are outside these statements.
        if attribution == "rewrite":
            omw.rewrite_active_signatures(c, old, new)
        # 3) Collapse the households: items (available and on_loan alike) move to
        #    the canonical name in this same commit, so the owner list and the
        #    available board can never show the absorbed name afterwards.
        omw.repoint_items(c, old, new)
        # 4) Flatten the alias map (李四→李四家 repoints to 李四家族) before recording this merge.
        c.execute("UPDATE owner_merges SET canonical=? WHERE canonical=?", (new, old))
        c.execute("INSERT INTO owner_merges(absorbed,canonical,attribution,created_at) VALUES (?,?,?,?)",
                  (old, new, attribution, datetime.now(timezone.utc).isoformat()))
    return {"ok": True, "canonical": new, "absorbed": old}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
