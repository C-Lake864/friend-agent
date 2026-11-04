"""결과를 모르는 승인 실패는 주문을 ready 로 남기고, 같은 멱등키로 다시 승인한다.

결과를 모르는 실패: 네트워크 오류·시간 초과, 토스 5xx, 409 IDEMPOTENT_REQUEST_PROCESSING(이전 멱등 요청 처리 중).
서버는 같은 요청 안에서 두 번 더 시도하고, 그래도 안 되면 503 을 돌려준다. 화면은 '다시 시도'로 같은 승인을 다시 보낸다.
근거: https://docs.tosspayments.com/reference/error-codes (500: 잠시 후 다시 시도),
      https://docs.tosspayments.com/reference/using-api/authorization (멱등키 헤더, 409 IDEMPOTENT_REQUEST_PROCESSING)
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, payments
from app.auth import User
from app.main import app, current_user, store
from tests.test_payment_confirm import INTERNAL, REJECTED, payment_object

client = TestClient(app)
MEMBER = User("member-retry", anonymous=False)
PROCESSING = (409, {"code": "IDEMPOTENT_REQUEST_PROCESSING", "message": "이전 멱등 요청이 처리중입니다."})
NETWORK = "network"  # 응답을 받지 못함(연결 실패, 시간 초과)


@pytest.fixture
def toss(monkeypatch):
    """승인 API를 가짜로 바꾼다. toss.replies 에 차례대로 돌려줄 결과를 넣는다(비면 성공)."""
    monkeypatch.setattr(main.settings, "toss_client_key", "test_gck_dummy")
    monkeypatch.setattr(main.settings, "toss_secret_key", "test_gsk_dummy")
    monkeypatch.setattr(payments, "RETRY_WAITS", (0, 0))  # 테스트에서는 기다리지 않는다

    class Toss:
        calls = []
        replies = []

    def fake_post(url, headers, json, timeout):
        Toss.calls.append({"key": headers["Idempotency-Key"], "json": json})
        reply = Toss.replies.pop(0) if Toss.replies else None
        if reply == NETWORK:
            raise httpx.ReadTimeout("timed out")
        if reply:
            return httpx.Response(reply[0], json=reply[1])
        return httpx.Response(200, json=payment_object(json["paymentKey"], json["orderId"], "크레딧", json["amount"]))

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    app.dependency_overrides[current_user] = lambda: MEMBER
    yield Toss
    app.dependency_overrides.pop(current_user, None)


def new_order():
    key = client.get("/api/payments/checkout").json()["customer_key"]
    return client.post("/api/payments/orders", json={"product": "credits_100", "customer_key": key}).json()


def confirm(order):
    return client.post("/api/payments/confirm", json={"payment_key": "pk-retry-0001", "order_id": order["order_id"],
                                                     "amount": order["amount"]})


def stored_key(order):
    return store.get_payment_order(MEMBER.id, order["order_id"])["idempotency_key"]


@pytest.mark.parametrize("reply", [INTERNAL, PROCESSING, NETWORK], ids=["toss-500", "processing-409", "network"])
def test_unknown_result_keeps_order_ready_and_retries_with_same_key(toss, reply):
    order = new_order()
    before = store.credit_balance(MEMBER.id)
    toss.replies = [reply, reply, reply]
    r = confirm(order)
    assert r.status_code == 503 and r.json()["detail"]["retry"] is True
    assert len(toss.calls) == 3 and {c["key"] for c in toss.calls} == {stored_key(order)}
    assert store.get_payment_order(MEMBER.id, order["order_id"])["status"] == "ready"
    assert store.credit_balance(MEMBER.id) == before
    again = confirm(order)  # 화면의 '다시 시도'. 이번에는 토스가 성공을 돌려준다
    assert again.status_code == 200
    assert toss.calls[-1]["key"] == stored_key(order)
    assert store.credit_balance(MEMBER.id) == before + 100


def test_server_retries_within_one_request(toss):
    order = new_order()
    before = store.credit_balance(MEMBER.id)
    toss.replies = [NETWORK, INTERNAL]  # 세 번째 시도에서 성공
    r = confirm(order)
    assert r.status_code == 200 and len(toss.calls) == 3
    assert store.credit_balance(MEMBER.id) == before + 100


def test_definite_rejection_is_not_retried(toss):
    order = new_order()
    toss.replies = [REJECTED]
    r = confirm(order)
    assert r.status_code == 400 and len(toss.calls) == 1
    assert store.get_payment_order(MEMBER.id, order["order_id"])["status"] == "failed"
