"""로그인 기능이 없는 로컬 개발(ENV=local, AUTH=none)에서는 개발용 사용자가 결제를 시험할 수 있다.
staging·production 처럼 다른 환경에서는 게스트 결제를 지금처럼 막는다.
"""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)


@pytest.fixture
def toss_keys(monkeypatch):
    monkeypatch.setattr(main.settings, "toss_client_key", "test_gck_dummy")
    monkeypatch.setattr(main.settings, "toss_secret_key", "test_gsk_dummy")
    return monkeypatch


def test_local_dev_user_can_open_checkout(toss_keys):
    toss_keys.setattr(main.settings, "env", "local")
    toss_keys.setattr(main.settings, "auth", "none")
    assert client.get("/api/payments/checkout").status_code == 200


@pytest.mark.parametrize("env", ["staging", "production", "test"])
def test_guest_is_still_blocked_outside_local(toss_keys, env):
    toss_keys.setattr(main.settings, "env", env)
    toss_keys.setattr(main.settings, "auth", "none")
    assert client.get("/api/payments/checkout").status_code == 403
