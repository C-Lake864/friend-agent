"""결제 승인 요청의 멱등키와 로그. 토스페이먼츠는 부르지 않고 HTTP 요청(httpx.post)을 가짜로 바꿔 끼운다.

근거: https://docs.tosspayments.com/reference/using-api/authorization (멱등키 헤더)
- 같은 멱등키의 요청에는 첫 응답을 그대로 돌려준다. UUID 처럼 무작위적인 값, 최대 300자.
"""
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, payments
from app.auth import User
from app.main import app, current_user, store

client = TestClient(app)
MEMBER = User("member-idem", anonymous=False)
SECRET = "test_gsk_dummy_secret"


@pytest.fixture
def sent(monkeypatch):
    """결제를 켜고, 승인 API로 나가는 요청을 기록한 뒤 200 을 돌려준다."""
    monkeypatch.setattr(main.settings, "toss_client_key", "test_gck_dummy")
    monkeypatch.setattr(main.settings, "toss_secret_key", SECRET)
    requests = []

    def fake_post(url, headers, json, timeout):
        requests.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(200, json={"paymentKey": json["paymentKey"], "orderId": json["orderId"],
                                         "totalAmount": json["amount"], "status": "DONE"})

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    app.dependency_overrides[current_user] = lambda: MEMBER
    yield requests
    app.dependency_overrides.pop(current_user, None)


def order(product="credits_100"):
    key = client.get("/api/payments/checkout").json()["customer_key"]
    return client.post("/api/payments/orders", json={"product": product, "customer_key": key}).json()


def confirm(o, payment_key="pk-idem-0123456789"):
    return client.post("/api/payments/confirm", json={"payment_key": payment_key, "order_id": o["order_id"],
                                                     "amount": o["amount"]})


def test_confirm_sends_the_orders_stored_idempotency_key(sent):
    o = order()
    stored = store.get_payment_order(MEMBER.id, o["order_id"])["idempotency_key"]
    assert confirm(o).status_code == 200
    assert sent[0]["url"] == "https://api.tosspayments.com/v1/payments/confirm"
    assert sent[0]["headers"]["Idempotency-Key"] == stored
    assert uuid.UUID(stored).version == 4 and len(stored) <= 300


def test_same_order_keeps_one_key_and_orders_do_not_share(sent):
    a, b = order(), order()
    key_a = store.get_payment_order(MEMBER.id, a["order_id"])["idempotency_key"]
    assert store.get_payment_order(MEMBER.id, a["order_id"])["idempotency_key"] == key_a  # 다시 읽어도 같다
    assert key_a != store.get_payment_order(MEMBER.id, b["order_id"])["idempotency_key"]
    payments.confirm(SECRET, "pk-x", a["order_id"], a["amount"], key_a)
    payments.confirm(SECRET, "pk-x", a["order_id"], a["amount"], key_a)
    assert sent[0]["headers"]["Idempotency-Key"] == sent[1]["headers"]["Idempotency-Key"] == key_a


def test_confirm_log_has_order_and_key_but_no_secrets(sent, capsys):
    o = order()
    stored = store.get_payment_order(MEMBER.id, o["order_id"])["idempotency_key"]
    capsys.readouterr()  # 주문을 만들 때 남은 로그는 버린다
    confirm(o, payment_key="pk-very-secret-payment-key-123")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    request_log = next(line for line in lines if line["message"] == "결제 승인 요청")
    assert request_log["order_id"] == o["order_id"] and request_log["idempotency_key"] == stored
    everything = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
    assert SECRET not in everything and "pk-very-secret-payment-key-123" not in everything
