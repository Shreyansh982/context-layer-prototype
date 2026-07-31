from fastapi import HTTPException

from app import auth


def test_demo_key_returns_server_owned_identity(monkeypatch):
    monkeypatch.setattr(auth.settings, "DEMO_API_KEY", "demo-key")
    monkeypatch.setattr(auth.settings, "DEMO_USER_ID", "E0004")
    assert auth.get_principal("demo-key").user_id == "E0004"


def test_demo_key_rejects_missing_or_invalid_values(monkeypatch):
    monkeypatch.setattr(auth.settings, "DEMO_API_KEY", "demo-key")
    for value in (None, "wrong"):
        try:
            auth.get_principal(value)
        except HTTPException as error:
            assert error.status_code == 401
        else:
            raise AssertionError("Expected authentication failure")
