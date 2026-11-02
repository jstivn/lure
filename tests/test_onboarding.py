import json

import pytest

from core import onboarding


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "META_CONFIG", tmp_path / "meta.json")
    monkeypatch.setattr(onboarding, "WA_CONFIG", tmp_path / "wa.json")
    monkeypatch.setattr(onboarding, "STATE_PATH", tmp_path / "setup.json")
    monkeypatch.setattr(onboarding, "find_chrome", lambda: "C:/chrome.exe")
    return tmp_path


def test_status_never_returns_password(isolated):
    (isolated / "meta.json").write_text(json.dumps({"email": {"sender": "a@b.co", "app_password": "secret"}}))
    st = onboarding.status()
    assert st["email"]["ok"] and "secret" not in json.dumps(st)


def test_save_email_rejects_bad_input_without_network():
    assert not onboarding.save_email("not-an-email", "pw", "")["success"]
    assert not onboarding.save_email("a@b.co", "   ", "")["success"]


def test_login_flow_unblocks_channel():
    assert "Facebook" in onboarding.blocker("meta_fb")
    onboarding.set_login("fb", True)
    assert onboarding.blocker("meta_fb") is None
    assert not onboarding.set_login("tiktok", True)["success"]


def test_blocker_without_chrome(monkeypatch):
    monkeypatch.setattr(onboarding, "find_chrome", lambda: None)
    assert "Chrome" in onboarding.blocker("meta_scrape")


def test_whatsapp_needs_both_profiles(isolated):
    assert onboarding.blocker("wa_wp1")
    assert not onboarding.save_whatsapp("ftp://x", "1", "2")["success"]
    onboarding.save_whatsapp("", "id1", "id2")
    assert onboarding.blocker("wa_wp1") is None
    assert json.loads((isolated / "wa.json").read_text())["bitbrowser_api_url"] == "http://127.0.0.1:54345"
