"""Oylik to'lov (billing) testlari — holat, grace, to'lov, eslatma."""

import datetime
import json

import pytest

from bm_automation.app.dashboard import billing
from bm_automation.app.notifications.ops import billing_reminders
from tests.test_dashboard_users import (  # noqa: F401  (env/st fixture shu yerda)
    _mk, _request, env, storage as st,
)

TODAY = datetime.date(2026, 11, 4)


def _row(**kw) -> dict:
    row = {
        "id": 1, "name": "TEST MCHJ", "status": "active",
        "subscription_expire_date": "2026-12-31",
        "billing_amount": 1000000.0,
    }
    row.update(kw)
    return row


# ------------------------------------------------------------------ holat

def test_state_active_due_grace_blocked():
    assert billing._company_state(_row(), {}, TODAY)["state"] == "active"
    soon = (TODAY + datetime.timedelta(days=3)).isoformat()
    assert billing._company_state(
        _row(subscription_expire_date=soon), {}, TODAY)["state"] == "due"
    grace = (TODAY - datetime.timedelta(days=2)).isoformat()
    st = billing._company_state(_row(subscription_expire_date=grace), {}, TODAY)
    assert st["state"] == "grace"
    assert st["days_left"] == -2
    expired = (TODAY - datetime.timedelta(days=30)).isoformat()
    assert billing._company_state(
        _row(subscription_expire_date=expired), {}, TODAY)["state"] == "blocked"


def test_state_none_without_expiry_and_manual_block():
    assert billing._company_state(
        _row(subscription_expire_date=""), {}, TODAY)["state"] == "none"
    assert billing._company_state(
        _row(status="blocked"), {}, TODAY)["state"] == "blocked"


def test_state_debt_only_positive():
    st = billing._company_state(_row(), {"2026-11": 400000}, TODAY)
    assert st["debt"] == 600000.0
    assert st["paid"] == 400000.0
    st = billing._company_state(_row(), {"2026-11": 1500000}, TODAY)
    assert st["debt"] == 0.0


def test_period_end_and_validation():
    assert billing.period_end("2026-02").isoformat() == "2026-02-28"
    assert billing.period_end("2026-12").isoformat() == "2026-12-31"
    for bad in ("2026-13", "202611", ""):
        with pytest.raises(ValueError):
            billing._norm_period(bad)


# ------------------------------------------------------------ to'lov yozish

def _company(st, **kw) -> dict:
    values = {"name": "TEST MCHJ", "slug": "test-mchj", "status": "active",
              "subscription_expire_date": "", "billing_amount": 1000000.0,
              "billing_day": 5}
    values.update(kw)
    assert st.insert("companies", values)
    return dict(st.query(f"SELECT * FROM companies WHERE slug = {st.db.ph}",
                         (values["slug"],), limit=1)[0])


def test_record_payment_extends_expiry_when_fully_paid(st):
    c = _company(st)
    res = billing.record_payment(int(c["id"]), "2026-11", 1000000,
                                storage=st, method="bank",
                                contract_no="SH-42", paid_on="2026-11-03")
    assert res["ok"] is True, res
    assert res["expire_date"] == "2026-11-30"
    row = st.find("companies", id=int(c["id"]))
    assert row["subscription_expire_date"] == "2026-11-30"
    pay = st.query("SELECT * FROM company_payments", (), limit=1)[0]
    assert pay["method"] == "bank" and pay["contract_no"] == "SH-42"
    assert pay["period"] == "2026-11"


def test_partial_payment_does_not_extend(st):
    c = _company(st)
    res = billing.record_payment(int(c["id"]), "2026-11", 400000, storage=st)
    assert res["ok"] is True
    assert "expire_date" not in res
    assert st.find("companies", id=int(c["id"]))["subscription_expire_date"] == ""
    res = billing.record_payment(int(c["id"]), "2026-11", 600000, storage=st)
    assert res["expire_date"] == "2026-11-30"
    assert len(st.query("SELECT id FROM company_payments", (), limit=10)) == 2


def test_payment_does_not_shrink_current_expiry(st):
    c = _company(st, subscription_expire_date="2027-01-31")
    res = billing.record_payment(int(c["id"]), "2026-11", 1000000, storage=st)
    assert res["ok"] is True
    assert "expire_date" not in res
    assert st.find("companies", id=int(c["id"]))[
        "subscription_expire_date"] == "2027-01-31"


def test_payment_validation(st):
    c = _company(st)
    cid = int(c["id"])
    assert billing.record_payment(cid, "2026-11", 0, storage=st)["ok"] is False
    assert billing.record_payment(cid, "2026-13", 100, storage=st)["ok"] is False
    bank = billing.record_payment(cid, "2026-11", 100, storage=st, method="bank")
    assert bank["ok"] is False and "shartnoma" in bank["error"]
    bad_method = billing.record_payment(cid, "2026-11", 100, storage=st,
                                        method="crypto")
    assert bad_method["ok"] is False


def test_delete_payment_keeps_expiry(st):
    c = _company(st)
    billing.record_payment(int(c["id"]), "2026-11", 1000000, storage=st)
    pay = st.query("SELECT id FROM company_payments", (), limit=1)[0]
    assert billing.delete_payment(int(pay["id"]), storage=st)["ok"] is True
    assert not st.query("SELECT id FROM company_payments", (), limit=1)
    assert st.find("companies", id=int(c["id"]))[
        "subscription_expire_date"] == "2026-11-30"


# ------------------------------------------------------------------ ro'yxat

def test_list_billing_summary_and_due(st):
    _company(st)
    _company(st, name="IKKI MCHJ", slug="ikki-mchj",
             subscription_expire_date="2026-11-06")
    data = billing.list_billing(storage=st, today=TODAY)
    assert data["ok"] is True
    states = {c["name"]: c["state"] for c in data["companies"]}
    assert states == {"TEST MCHJ": "none", "IKKI MCHJ": "due"}
    assert data["summary"]["monthly_total"] == 2000000.0
    assert data["summary"]["debt_total"] == 2000000.0
    due = billing.due_companies(storage=st, today=TODAY)
    assert [c["name"] for c in due] == ["IKKI MCHJ"]


def test_due_after_billing_day_when_current_unpaid(st):
    _company(st, subscription_expire_date="2026-12-31")
    assert billing.due_companies(storage=st, today=TODAY) == []  # 4 < 5
    later = datetime.date(2026, 11, 10)
    assert [c["name"] for c in billing.due_companies(storage=st, today=later)] \
        == ["TEST MCHJ"]


# ----------------------------------------------------------------- eslatma

def test_reminder_sends_daily_once(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(billing_reminders, "STATE_FILE",
                        tmp_path / "billing_reminders.json")
    monkeypatch.setattr(billing_reminders, "get_storage",
                        lambda: type("S", (), {"enabled": True})())
    monkeypatch.setattr(billing_reminders, "_admin_chats", lambda: ["111"])
    monkeypatch.setattr(billing_reminders, "_send_to",
                        lambda cid, text: sent.append((cid, text)))
    monkeypatch.setattr(
        "bm_automation.app.dashboard.billing.due_companies",
        lambda storage=None, today=None: [{
            "name": "TEST MCHJ", "state": "grace", "period": "2026-11",
            "price": 1000000, "debt": 1000000,
            "subscription_expire_date": "2026-10-31",
            "contact_telegram": "@test_owner",
        }])
    billing_reminders._LAST_CHECK_AT = 0.0
    billing_reminders.check_and_send()
    assert [cid for cid, _ in sent] == ["111"]
    assert "TEST MCHJ" in sent[0][1]

    sent.clear()
    billing_reminders.check_and_send()
    assert sent == []


def test_contact_chat_only_numeric():
    assert billing_reminders._contact_chat(
        {"contact_telegram": "@123456"}) == "123456"
    assert billing_reminders._contact_chat(
        {"contact_telegram": "@owner"}) == ""


# --------------------------------------------------------------------- API

def _super(env, username="root"):
    return _mk(env, username, "ADMIN", data='{"super": true}')


def test_api_billing_flow(env):
    hdr = _super(env)
    row = env.find("companies", name="XTEST")
    cid = int(row["id"])

    body = json.dumps({"billing_amount": 900000,
                       "billing_day": 7}).encode()
    status, data, _ = _request(
        env, f"/api/admin/companies/{cid}", method="PUT",
        headers={**hdr, "Content-Length": str(len(body))}, body=body)
    assert status == 200 and data.get("ok") is True, data

    body = json.dumps({"company_id": cid, "period": "2026-11",
                       "amount": 900000, "method": "cash",
                       "paid_on": "2026-11-04"}).encode()
    status, pay, _ = _request(
        env, "/api/admin/billing/payments", method="POST",
        headers={**hdr, "Content-Length": str(len(body))}, body=body)
    assert status == 200 and pay.get("ok") is True, pay
    assert pay["expire_date"] == "2026-11-30"

    status, data, _ = _request(env, "/api/admin/billing", method="GET",
                               headers=hdr)
    assert status == 200 and data.get("ok") is True
    item = [c for c in data["companies"] if int(c["id"]) == cid][0]
    assert item["billing_amount"] == 900000
    assert item["paid"] == 900000
    assert len(data["payments"]) == 1


def test_api_billing_requires_super(env):
    hdr = _mk(env, "boss", "ADMIN")
    status, data, _ = _request(env, "/api/admin/billing", method="GET",
                               headers=hdr)
    assert status == 403 and data.get("ok") is False


def test_update_payment_preserves_identity_and_expiry(st):
    c = _company(st)
    billing.record_payment(c["id"], "2026-11", 1000000, storage=st, created_by="root")
    before = dict(st.query("SELECT * FROM company_payments", limit=1)[0])
    result = billing.update_payment(before["id"], "2026-10", 200000, storage=st,
                                    method="card", receipt_no="CHEK-2", note="Tuzatish")
    assert result["ok"], result
    after = dict(st.query("SELECT * FROM company_payments", limit=1)[0])
    for key in ("id", "company_id", "company", "created_by", "created_at"):
        assert before[key] == after[key]
    assert after["period"] == "2026-10" and after["amount"] == 200000
    assert after["receipt_no"] == "CHEK-2"
    assert st.find("companies", id=c["id"])["subscription_expire_date"] == "2026-11-30"
    result = billing.update_payment(before["id"], "2026-12", 1000000, storage=st)
    assert result["expire_date"] == "2026-12-31"


@pytest.mark.parametrize("field,value", [
    ("amount", float("nan")), ("amount", float("inf")), ("amount", True),
    ("amount", -1), ("period", "2026-1"), ("period", "0000-01"),
    ("paid_on", "2026-1-01"), ("paid_on", "2026-02-30"), ("method", "bank"),
])
def test_update_invalid_does_not_mutate(st, field, value):
    c = _company(st)
    billing.record_payment(c["id"], "2026-11", 100, storage=st)
    before = dict(st.query("SELECT * FROM company_payments", limit=1)[0])
    values = {"period": "2026-11", "amount": 100, "method": "cash"}
    values[field] = value
    assert not billing.update_payment(before["id"], storage=st, **values)["ok"]
    assert dict(st.query("SELECT * FROM company_payments", limit=1)[0]) == before


@pytest.mark.parametrize("pid", [0, -1, True, 1.5, "bad", 9999])
def test_update_or_delete_nonexistent_payment(st, pid):
    assert not billing.update_payment(pid, "2026-11", 100, storage=st)["ok"]
    assert not billing.delete_payment(pid, storage=st)["ok"]


def test_deleted_company_excluded_from_totals_reminders(st):
    _company(st, status="deleted")
    data = billing.list_billing(st, today=TODAY)
    assert data["companies"][0]["state"] == "deleted"
    assert data["summary"]["monthly_total"] == 0
    assert data["summary"]["debt_total"] == 0
    assert billing.due_companies(st, today=TODAY.replace(day=10)) == []


def test_extension_failure_does_not_claim_successful_extension(st, monkeypatch):
    c = _company(st)
    execute = st._exec
    monkeypatch.setattr(st, "_exec", lambda sql, params=(): False if sql.startswith(
        "UPDATE companies SET subscription_expire_date") else execute(sql, params))
    result = billing.record_payment(c["id"], "2026-11", 1000000, storage=st)
    assert result["ok"] and result["warning"]
    assert "expire_date" not in result
    assert st.find("companies", id=c["id"])["subscription_expire_date"] == ""


def test_api_update_payment_and_super_guard(env):
    c = env.find("companies", name="XTEST")
    billing.record_payment(c["id"], "2026-11", 100, storage=env)
    payment = env.query("SELECT * FROM company_payments", limit=1)[0]
    body = json.dumps({"id": payment["id"], "company_id": 999,
                       "period": "2026-11", "amount": 200}).encode()
    for hdr, expected in ((_mk(env, "manager", "ADMIN"), 403), (_super(env), 200)):
        status, data, _ = _request(env, "/api/admin/billing/payments/update", method="POST",
            headers={**hdr, "Content-Length": str(len(body))}, body=body)
        assert status == expected, data
    row = env.query("SELECT * FROM company_payments", limit=1)[0]
    assert row["company_id"] == c["id"] and row["amount"] == 200


@pytest.mark.parametrize("body", [b"[]", b"null", b"{", b"true"])
def test_billing_rejects_non_object_json(env, body):
    status, data, _ = _request(env, "/api/admin/billing/payments/update", method="POST",
        headers={**_super(env), "Content-Length": str(len(body))}, body=body)
    assert status == 400, data


@pytest.mark.parametrize("updates", [{"billing_amount": "NaN"}, {"billing_amount": -1},
    {"billing_day": 0}, {"billing_day": 29}, {"billing_day": 2.5},
    {"status": "unknown"}, {"subscription_expire_date": "2026-02-30"}])
def test_company_billing_invalid_no_mutation(env, updates):
    before = dict(env.find("companies", name="XTEST"))
    body = json.dumps(updates).encode()
    status, data, _ = _request(env, f"/api/admin/companies/{before['id']}", method="PUT",
        headers={**_super(env), "Content-Length": str(len(body))}, body=body)
    assert status == 200 and not data["ok"]
    assert dict(env.find("companies", name="XTEST")) == before
