"""Owner-merge semantics: household collapse, single attribution world,
rollback, dirty ownerless data, and merge/lend overlap.

A write either finishes (items.owner moves AND active loans are re-signed as
chosen) or leaves the board exactly as it was before the request.
"""
import threading


def _owners_map(client):
    data = client.get("/api/owners").json()
    return {o["name"]: o for o in data["owners"]}, data["count"]


def _item(client, title):
    return next(i for i in client.get("/api/items").json() if i["title"] == title)


def _loan_for(client, item_id):
    packs = client.get("/api/loans").json()
    for bucket in ("active", "overdue", "returned"):
        for l in packs[bucket]:
            if l["item_id"] == item_id:
                return l, bucket
    return None, None


def test_merge_collapses_household_and_relabels_available_item(client):
    owners, count = _owners_map(client)
    assert count == 3  # 老周 / 小陈 / 阿强；无主脏数据不计
    assert "老周" in owners

    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "周家", "attribution": "rewrite"})
    assert r.status_code == 200 and r.json() == {"ok": True, "canonical": "周家", "absorbed": "老周"}

    owners, count = _owners_map(client)
    assert count == 3  # pure rename into a new household name: no household lost, old label gone
    assert "老周" not in owners
    zhous = owners["周家"]
    assert zhous["available_count"] >= 1
    assert zhous["absorbed"] == ["老周"]

    drill = _item(client, "电钻")
    assert drill["owner"] == "周家"
    board = client.get("/api/board").json()
    assert all(i["owner"] != "老周" for i in board["available"])
    by_title = {i["title"]: i for i in board["available"]}
    assert by_title["电钻"]["owner"] == "周家"


def test_merge_into_existing_household_reduces_count(client):
    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "小陈", "attribution": "keep"})
    assert r.status_code == 200
    owners, count = _owners_map(client)
    assert count == 2
    assert "老周" not in owners and "小陈" in owners


def test_rewrite_relabels_active_loan_but_keeps_due_date(client):
    drill = _item(client, "电钻")
    r = client.post(f"/api/items/{drill['id']}/lend",
                    json={"borrower": "邻居乙", "due_date": "2030-01-02"})
    assert r.status_code == 200

    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "周家", "attribution": "rewrite"})
    assert r.status_code == 200

    loan, bucket = _loan_for(client, drill["id"])
    assert bucket in ("active", "overdue")
    assert loan["owner_signed"] == "周家"          # 借还记录署名随目标字
    assert loan["owner"] == "周家"                  # 物品也已过户
    assert loan["due_date"] == "2030-01-02"         # 在借应还日不被改写

    board = client.get("/api/board").json()
    rows = [l for l in board["active"] + board["overdue"] if l["item_id"] == drill["id"]]
    assert rows and rows[0]["owner_signed"] == "周家"
    assert all(l["owner_signed"] != "老周" for l in board["active"] + board["overdue"])


def test_keep_freezes_signature_taken_at_lend_time(client):
    drill = _item(client, "电钻")
    client.post(f"/api/items/{drill['id']}/lend",
                json={"borrower": "邻居乙", "due_date": "2030-03-04"})

    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "周家", "attribution": "keep"})
    assert r.status_code == 200

    loan, _ = _loan_for(client, drill["id"])
    assert loan["owner_signed"] == "老周"           # keep：借出当时的署名世界
    assert loan["owner"] == "周家"                  # 户名仍收拢
    assert loan["due_date"] == "2030-03-04"


def test_merge_never_touches_returned_rows_or_dates(client):
    drill = _item(client, "电钻")
    client.post(f"/api/items/{drill['id']}/lend",
                json={"borrower": "邻居乙", "due_date": "2030-05-06"})
    client.post(f"/api/loans/{_loan_for(client, drill['id'])[0]['id']}/return")
    returned, _ = _loan_for(client, drill["id"])
    assert _loan_for(client, drill["id"])[1] == "returned"
    signed_before = returned["owner_signed"]

    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "周家", "attribution": "rewrite"})
    assert r.status_code == 200

    after, bucket = _loan_for(client, drill["id"])
    assert bucket == "returned"
    assert after["owner_signed"] == signed_before   # 已还行不被这次提交改写
    assert after["due_date"] == "2030-05-06"


def test_failed_merge_rolls_everything_back(client):
    before = client.get("/api/items").json()
    owners_before, count_before = _owners_map(client)

    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "老周", "attribution": "rewrite"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "same_owner"

    assert client.get("/api/items").json() == before
    owners_after, count_after = _owners_map(client)
    assert count_after == count_before
    assert set(owners_after) == set(owners_before)

    # absorbing an already-absorbed name is rejected with no second alias row
    client.post("/api/owners/merge",
                json={"old_name": "老周", "new_name": "周家", "attribution": "rewrite"})
    r = client.post("/api/owners/merge",
                    json={"old_name": "老周", "new_name": "陈家", "attribution": "rewrite"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_merged"
    owners, _ = _owners_map(client)
    assert "陈家" not in owners and _item(client, "电钻")["owner"] == "周家"


def test_ownerless_dirty_data_is_not_laundered(client):
    dirty = _item(client, "脏数据-无主")
    assert dirty["owner"] == "" and dirty["data_quality"] == "dirty"

    r = client.post("/api/owners/merge",
                    json={"old_name": "", "new_name": "周家", "attribution": "rewrite"})
    assert r.status_code == 400

    client.post("/api/owners/merge",
                json={"old_name": "老周", "new_name": "周家", "attribution": "rewrite"})

    dirty = _item(client, "脏数据-无主")
    assert dirty["owner"] == "" and dirty["data_quality"] == "dirty"  # 无主不得被洗成有主
    owners, count = _owners_map(client)
    assert all(name != "" for name in owners)


def test_listing_under_absorbed_name_is_rejected(client):
    client.post("/api/owners/merge",
                json={"old_name": "老周", "new_name": "周家", "attribution": "rewrite"})
    r = client.post("/api/items", json={"title": "梯子", "owner": "老周"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "name_merged"
    assert r.json()["detail"]["canonical"] == "周家"
    assert all(i["title"] != "梯子" for i in client.get("/api/items").json())

    r = client.post("/api/items", json={"title": "梯子", "owner": "周家"})
    assert r.status_code == 200
    assert _item(client, "梯子")["owner"] == "周家"


def test_merged_item_cannot_be_double_lent(client):
    drill = _item(client, "电钻")
    r = client.post(f"/api/items/{drill['id']}/lend",
                    json={"borrower": "甲", "due_date": "2030-07-08"})
    assert r.status_code == 200
    client.post("/api/owners/merge",
                json={"old_name": "老周", "new_name": "周家", "attribution": "rewrite"})

    r = client.post(f"/api/items/{drill['id']}/lend",
                    json={"borrower": "乙", "due_date": "2030-07-09"})
    assert r.status_code == 409

    packs = client.get("/api/loans").json()
    active = [l for l in packs["active"] + packs["overdue"] if l["item_id"] == drill["id"]]
    assert len(active) == 1 and active[0]["owner_signed"] == "周家"
    assert _item(client, "电钻")["owner"] == "周家"   # 一件物品只挂一户


def test_concurrent_lend_and_merge_leave_one_signature_world(client):
    drill = _item(client, "电钻")
    iid = drill["id"]
    barrier = threading.Barrier(2)
    errors = []

    def lend():
        barrier.wait()
        try:
            client.post(f"/api/items/{iid}/lend",
                        json={"borrower": "抢借方", "due_date": "2030-09-10"})
        except Exception as e:  # pragma: no cover - reported via assertions
            errors.append(e)

    def merge():
        barrier.wait()
        try:
            client.post("/api/owners/merge",
                        json={"old_name": "老周", "new_name": "周家", "attribution": "rewrite"})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t1, t2 = threading.Thread(target=lend), threading.Thread(target=merge)
    t1.start(); t2.start(); t1.join(10); t2.join(10)
    assert not errors

    item = _item(client, "电钻")
    packs = client.get("/api/loans").json()
    active = [l for l in packs["active"] + packs["overdue"] if l["item_id"] == iid]
    assert len(active) <= 1
    # Whichever writer landed first under the serialized write lock, the
    # surviving world is consistent: item household, loan signature, due date.
    if active:
        assert item["owner"] == "周家"
        assert active[0]["owner_signed"] == "周家"
        assert active[0]["due_date"] == "2030-09-10"
        assert item["status"] == "on_loan"
        assert {i["id"]: i["owner"] for i in client.get("/api/items").json()}.get(iid) != "老周"


def test_seed_active_loan_is_signed_by_its_household(client):
    loan, bucket = _loan_for(client, _item(client, "已外借样例")["id"])
    assert bucket == "overdue"  # 种子应还日 2020-06-01 早于今天
    assert loan["owner_signed"] == "阿强"
    assert loan["due_date"] == "2020-06-01"
