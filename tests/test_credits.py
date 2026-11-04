"""크레딧 원장. 가짜 LLM과 메모리 저장소로, 메시지 한 번에 1이 빠지고 0이면 거절되는지 본다.

다른 테스트와 같은 사용자(local-user)를 쓰므로, 잔액을 바꾼 테스트는 끝나면 원래대로 돌려 둔다.
"""
import json
import uuid
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import app, store

client = TestClient(app)
UID = "local-user"


def balance():
    return client.get("/api/credits").json()["balance"]


@contextmanager
def balance_set_to(target):
    """잔액을 target 으로 맞추고, 끝나면 맞추기 전으로 돌린다."""
    start = balance()
    store.add_credit(UID, target - start, "test", f"test:{uuid.uuid4()}")
    try:
        yield
    finally:
        store.add_credit(UID, start - target, "test", f"test:{uuid.uuid4()}")


def test_first_visit_gets_free_credits_once():
    first = client.get("/api/credits").json()
    again = client.get("/api/credits").json()
    signups = [row for row in again["history"] if row["ref"] == f"signup:{UID}"]
    assert first["balance"] == again["balance"] > 0
    assert len(signups) == 1 and signups[0]["delta"] == 50


def test_message_costs_one_credit():
    before = balance()
    r = client.post("/api/chat", json={"message": "오늘 좀 피곤해"})
    events = [json.loads(line) for line in r.text.splitlines()]
    assert r.status_code == 200 and events[-1]["type"] == "done"
    assert balance() == before - 1
    latest = client.get("/api/credits").json()["history"][0]
    assert latest["delta"] == -1 and latest["ref"] == f"chat:{events[-1]['message_id']}"


def test_zero_balance_is_rejected_with_402():
    with balance_set_to(0):
        r = client.post("/api/chat", json={"message": "오늘 좀 피곤해"})
        assert r.status_code == 402
        assert r.json() == {"error": "insufficient_credits",
                            "message": "크레딧이 부족합니다. 충전 후 이용해 주세요.", "balance": 0}
        assert balance() == 0


def test_crisis_message_is_answered_even_with_zero_balance():
    with balance_set_to(0):
        r = client.post("/api/chat", json={"message": "요즘 너무 죽고 싶어"})
        text = "".join(json.loads(line).get("text", "") for line in r.text.splitlines())
        assert r.status_code == 200 and "109" in text
        assert balance() == 0


def test_same_ref_is_recorded_only_once():
    ref = f"test:{uuid.uuid4()}"
    assert store.add_credit("someone-else", 5, "test", ref) is True
    assert store.add_credit("someone-else", 5, "test", ref) is False
    assert store.credit_balance("someone-else") == 5
