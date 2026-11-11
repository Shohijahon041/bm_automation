import pytest
from bm_automation.app.core import bot_users
from bm_automation.app.dashboard.fastapi_app import app
from fastapi.testclient import TestClient

def test_get_all_activities(tmp_path, monkeypatch):
    test_file = tmp_path / "bot_users.json"
    monkeypatch.setattr(bot_users, "STATE_FILE", test_file)
    monkeypatch.setattr(bot_users, "_CACHE", {})
    monkeypatch.setattr(bot_users, "_LOADED", True)
    
    # Record two users
    bot_users.record_user(111, first_name="Ali", username="ali_driver")
    bot_users.record_user(222, first_name="Vali", username="vali_disp")
    
    # Manually populate distinct timestamps to verify chronological sort
    bot_users._CACHE["111"]["activity"] = [
        {"action": "grafik_confirm", "detail": "12-yo'nalish jadval tasdiqlandi", "at": "2026-11-11 10:00:00"},
        {"action": "callback", "detail": "gck:12:1", "at": "2026-11-11 10:02:00"},
    ]
    bot_users._CACHE["222"]["activity"] = [
        {"action": "command", "detail": "/today", "at": "2026-11-11 10:01:00"},
    ]
    
    # Fetch all
    acts = bot_users.get_all_activities(limit=10)
    assert len(acts) == 3
    # Check ordering (most recent first)
    assert acts[0]["chat_id"] == 111
    assert acts[0]["action"] == "callback"
    assert acts[0]["at"] == "2026-11-11 10:02:00"
    
    assert acts[1]["chat_id"] == 222
    assert acts[1]["action"] == "command"
    assert acts[1]["at"] == "2026-11-11 10:01:00"
    
    assert acts[2]["chat_id"] == 111
    assert acts[2]["action"] == "grafik_confirm"
    assert acts[2]["at"] == "2026-11-11 10:00:00"
    
    # Filter by action (partial match)
    confirm_acts = bot_users.get_all_activities(action_filter="confirm")
    assert len(confirm_acts) == 1
    assert confirm_acts[0]["action"] == "grafik_confirm"
    
    # Filter by chat_id
    vali_acts = bot_users.get_all_activities(chat_id=222)
    assert len(vali_acts) == 1
    assert vali_acts[0]["detail"] == "/today"
    
    # Search
    search_acts = bot_users.get_all_activities(search="jadval")
    assert len(search_acts) == 1
    assert search_acts[0]["chat_id"] == 111

def test_api_user_activities_endpoint(tmp_path, monkeypatch):
    test_file = tmp_path / "bot_users.json"
    monkeypatch.setattr(bot_users, "STATE_FILE", test_file)
    monkeypatch.setattr(bot_users, "_CACHE", {})
    monkeypatch.setattr(bot_users, "_LOADED", True)
    
    bot_users.record_user(12345, first_name="Haydovchi Otabek", username="otabek_h")
    bot_users._CACHE["12345"]["activity"] = [
        {"action": "grafik_confirm", "detail": "Reys tasdiqlandi", "at": "2026-11-11 11:30:00"},
    ]
    
    client = TestClient(app)
    # BM_TEST_MODE=1 allows bypass or viewer access
    res = client.get("/api/users/activities")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] >= 1
    item = [x for x in data["activities"] if x["chat_id"] == 12345][0]
    assert item["action"] == "grafik_confirm"
    assert item["detail"] == "Reys tasdiqlandi"
