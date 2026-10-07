"""Owner-merge behavior.

Seed items: 1 电钻/老周 available, 2 折叠桌/小陈 available,
3 脏数据-无主/'' available(dirty), 4 已外借样例/阿强 on_loan with one active
loan whose owner_signed is NULL (legacy row), due 2020-06-01.
"""
import sqlite3
import threading

from app.db import connect


def owners_map(client):
    data = client.get("/api/owners").json()
    return data["count"], {o["name"]: o for o in data["owners"]}


def loans_by_item(client, **params):
    rows = client.get("/api/loans", params=params).json()
    out = {}
    for bucket in ("active", "overdue", "returned"):
        for l in rows[bucket]:
            out[l["item_id"]] = l
    return out


# ---------- collapse: household list and available board move together ----------

def test_merge_into_existing_household_collapses_count(client):
    count, om = owners_map(client)
    assert count == 3 and set(om) == {"老周", "小陈", "阿强"}

    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "小陈", "attribution": "rewrite"})
    assert r.status_code == 200 and r.json() == {"ok": True, "canonical": "小陈", "absorbed": "老周"}

    count, om = owners_map(client)
    assert count == 2 and set(om) == {"小陈", "阿强"}
    assert om["小陈"]["item_count"] == 2
    assert om["小陈"]["available_count"] == 2
    assert om["小陈"]["absorbed"] == ["老周"]

    board = client.get("/api/board").json()
    titles = {i["title"]: i["owner"] for i in board["available"]}
    assert titles["电钻"] == "小陈"
    assert all(i["owner"] != "老周" for i in board["available"])


def test_merge_into_brand_new_name_is_pure_rename(client):
    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "老周家", "attribution": "keep"})
    assert r.status_code == 200
    count, om = owners_map(client)
    assert count == 3 and "老周家" in om and "老周" not in om
    item = next(i for i in client.get("/api/items").json() if i["title"] == "电钻")
    assert item["owner"] == "老周家"


# ---------- attribution: exactly one signature world ----------

def test_rewrite_stamps_active_loans_with_new_name_and_keeps_due_date(client):
    # New-style lend already stamps owner_signed at lend time.
    r = client.post("/api/items/2/lend", json={"borrower": "邻居乙", "due_date": "2026-11-20"})
    assert r.status_code == 200
    r = client.post("/api/owners/merge",
                    json={"old_name": "阿强", "new_name": "小陈", "attribution": "rewrite"})
    assert r.status_code == 200

    by_item = loans_by_item(client)
    # Legacy NULL-signature active loan is explicitly rewritten, never left to COALESCE.
    legacy = by_item[4]
    assert legacy["owner_signed"] == "小陈"
    assert legacy["due_date"] == "2020-06-01"          # due date untouched by the merge
    assert legacy["status"] == "active"
    assert by_item[2]["owner_signed"] == "小陈"
    assert by_item[2]["due_date"] == "2026-11-20"

    item4 = next(i for i in client.get("/api/items").json() if i["id"] == 4)
    assert item4["owner"] == "小陈"
    # Board's active pane shows the new signature; the available board has nothing of 阿强.
    board = client.get("/api/board").json()
    assert all(l["owner_signed"] != "阿强" for l in board["active"] + board["overdue"])
    assert all(i["owner"] != "阿强" for i in board["available"])


def test_keep_pins_active_loans_to_old_name_and_never_rewrites_due_date(client):
    r = client.post("/api/items/1/lend", json={"borrower": "邻居丙", "due_date": "2026-11-11"})
    assert r.status_code == 200
    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "老周家", "attribution": "keep"})
    assert r.status_code == 200

    by_item = loans_by_item(client)
    mine = by_item[1]
    assert mine["owner_signed"] == "老周"               # signed old world even though item moved
    assert mine["due_date"] == "2026-11-11"
    # Legacy NULL-signature loan pinned at merge time, due date untouched.
    legacy = by_item[4]
    assert legacy["owner_signed"] == "阿强"
    assert legacy["due_date"] == "2020-06-01"


def test_returned_history_is_pinned_and_not_laundered_by_later_rewrite(client):
    # Settle the legacy active loan first (its owner_signed is still NULL in the db).
    loan_id = client.get("/api/loans").json()["overdue"][0]["id"]
    assert client.post(f"/api/loans/{loan_id}/return", json={}).status_code == 200
    r = client.post("/api/owners/merge",
                    json={"old_name": "阿强", "new_name": "阿强家", "attribution": "rewrite"})
    assert r.status_code == 200

    returned = loans_by_item(client)[4]
    assert returned["status"] == "returned"
    assert returned["owner_signed"] == "阿强"           # frozen at the pre-merge world
    assert returned["due_date"] == "2020-06-01"
    item4 = next(i for i in client.get("/api/items").json() if i["id"] == 4)
    assert item4["owner"] == "阿强家"                    # item itself did move


# ---------- failure: everything returns to the pre-submit world ----------

def test_failed_merge_changes_nothing(client):
    before = client.get("/api/items").json()

    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "老周", "attribution": "keep"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "same_owner"

    r = client.post("/api/owners/merge",
                    json={"old_name": "不存在户", "new_name": "小陈", "attribution": "keep"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "old_household_not_found"

    r = client.post("/api/owners/merge",
                    json={"old_name": "  ", "new_name": "小陈", "attribution": "keep"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "empty_owner"

    assert client.get("/api/items").json() == before
    count, om = owners_map(client)
    assert count == 3 and "老周" in om


def test_double_merge_and_reverse_merge_are_rejected_without_touching_data(client):
    assert client.post("/api/owners/merge",
                       json={"old_name": "老周", "new_name": "老周家", "attribution": "keep"}
                       ).status_code == 200
    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "赵户", "attribution": "rewrite"})
    assert r.status_code == 409 and r.json()["detail"] == {
        "code": "already_merged", "canonical": "老周家"}
    r = client.post("/api/owners/merge",
                    json={"old_name": "老周家", "new_name": "老周", "attribution": "keep"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "canonical_is_absorbed"

    item1 = next(i for i in client.get("/api/items").json() if i["id"] == 1)
    assert item1["owner"] == "老周家"                    # no half-rewrite to 赵户/老周


# ---------- dirty ownerless data can never become owned ----------

def test_ownerless_dirty_data_survives_merge_unowned(client):
    r = client.post("/api/owners/merge",
                    json={"old_name": "小陈", "new_name": "老周", "attribution": "rewrite"})
    assert r.status_code == 200
    dirty = next(i for i in client.get("/api/items").json() if i["id"] == 3)
    assert dirty["owner"] == "" and dirty["data_quality"] == "dirty"

    count, om = owners_map(client)
    assert count == 2 and "" not in om                  # unowned never counted as a household
    board = client.get("/api/board").json()
    assert "" not in board["owners"]


# ---------- absorbed name can never open a competing household ----------

def test_listing_under_absorbed_name_is_rejected(client):
    assert client.post("/api/owners/merge",
                       json={"old_name": "老周", "new_name": "老周家", "attribution": "keep"}
                       ).status_code == 200
    r = client.post("/api/items", json={"title": "梯子", "owner": "老周"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "name_merged", "canonical": "老周家"}
    assert client.post("/api/items",
                       json={"title": "梯子", "owner": "老周家"}).status_code == 200


def test_chain_merge_repoints_alias_and_all_filters_resolve(client):
    client.post("/api/owners/merge",
                json={"old_name": "老周", "new_name": "老周家", "attribution": "keep"})
    r = client.post("/api/owners/merge",
                    json={"old_name": "老周家", "new_name": "李四家族", "attribution": "keep"})
    assert r.status_code == 200

    for stale in ("老周", "老周家"):
        r = client.post("/api/items", json={"title": "x", "owner": stale})
        assert r.status_code == 409 and r.json()["detail"]["canonical"] == "李四家族"

    item1 = next(i for i in client.get("/api/items").json() if i["id"] == 1)
    assert item1["owner"] == "李四家族"
    board = client.get("/api/board", params={"owner": "老周"}).json()
    assert board["filter"] == "李四家族"
    assert {i["title"] for i in board["available"]} == {"电钻"}
    loans = client.get("/api/loans", params={"owner": "老周"}).json()
    assert all(l["owner"] == "李四家族" for l in loans["active"] + loans["overdue"])


# ---------- concurrency: only one attribution world can exist ----------

def test_listing_blocks_until_merge_commits_then_rejects_old_name(client):
    held = connect()
    held.execute("BEGIN IMMEDIATE")                     # stands in for the open merge txn
    result = {}

    def list_under_old_name():
        result["r"] = client.post("/api/items", json={"title": "梯子", "owner": "老周"})

    t = threading.Thread(target=list_under_old_name)
    t.start()
    t.join(0.5)
    assert t.is_alive()                                 # stuck behind the merge write lock

    held.execute("UPDATE items SET owner='老周家' WHERE owner='老周'")
    held.execute(
        "INSERT INTO owner_merges(absorbed,canonical,attribution,created_at) "
        "VALUES ('老周','老周家','keep','2026-01-01T00:00:00+00:00')")
    held.commit()
    held.close()

    t.join(5)
    assert not t.is_alive()
    r = result["r"]
    assert r.status_code == 409 and r.json()["detail"]["code"] == "name_merged"
    # No phantom second household: the item count under 老周 stays zero.
    assert all(i["owner"] != "老周" for i in client.get("/api/items").json())


def test_racing_lends_can_never_produce_two_active_loans(client):
    barrier = threading.Barrier(2)
    results = []

    def lend():
        barrier.wait()
        results.append(client.post(
            "/api/items/1/lend", json={"borrower": "抢借者", "due_date": "2026-12-31"}))

    threads = [threading.Thread(target=lend) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert sorted(r.status_code for r in results) == [200, 409]
    c = connect()
    n = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=1 AND status='active'").fetchone()["c"]
    c.close()
    assert n == 1
    item1 = next(i for i in client.get("/api/items").json() if i["id"] == 1)
    assert item1["status"] == "on_loan"


def test_partial_unique_index_backstops_double_active(client):
    # Direct defense in depth: even bypassing the endpoint, two active rows are impossible.
    c = connect()
    c.execute("INSERT INTO loans(item_id,borrower,status,due_date,lent_at,owner_signed) "
              "VALUES (1,'a','active','2026-12-31','x','老周')")
    try:
        c.execute("INSERT INTO loans(item_id,borrower,status,due_date,lent_at,owner_signed) "
                  "VALUES (1,'b','active','2026-12-31','x','老周家')")
        raise AssertionError("two active loans on one item were allowed")
    except sqlite3.IntegrityError:
        pass
    finally:
        c.rollback()
        c.close()
