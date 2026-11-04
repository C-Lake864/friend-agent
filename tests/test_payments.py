"""크레딧 결제(토스페이먼츠 주문서형 결제). 토스 승인 API는 부르지 않고 가짜로 바꿔 끼운다.

로그인한 회원은 dependency_overrides 로 흉내 낸다. AUTH=none 의 기본 사용자(local-user)는 게스트다.
"""
import pytest
from fastapi.testclient import TestClient

from app import main, payments
from app.auth import User
from app.main import app, current_user, store

client = TestClient(app)
MEMBER = User("member-1", anonymous=False, email="member@example.com")


@pytest.fixture
def toss(monkeypatch):
    """결제를 켜고, 토스 승인 API 호출을 기록만 하는 가짜로 바꾼다."""
    monkeypatch.setattr(main.settings, "toss_client_key", "test_gck_dummy")
    monkeypatch.setattr(main.settings, "toss_secret_key", "test_gsk_dummy")
    calls = []

    def fake_confirm(secret_key, payment_key, order_id, amount):
        calls.append({"payment_key": payment_key, "order_id": order_id, "amount": amount})
        if payment_key == "pk-reject":
            raise payments.TossError("REJECT_CARD_COMPANY", "카드사에서 거절했습니다.")
        return {"paymentKey": payment_key, "orderId": order_id, "totalAmount": amount, "status": "DONE"}

    monkeypatch.setattr(payments, "confirm", fake_confirm)
    return calls


@pytest.fixture
def member():
    app.dependency_overrides[current_user] = lambda: MEMBER
    yield MEMBER
    app.dependency_overrides.pop(current_user, None)


def new_order(product="credits_500"):
    key = client.get("/api/payments/checkout").json()["customer_key"]
    r = client.post("/api/payments/orders", json={"product": product, "customer_key": key})
    assert r.status_code == 200, r.text
    return r.json()


def test_guest_cannot_order(toss):
    r = client.post("/api/payments/orders", json={"product": "credits_100", "customer_key": "x" * 36})
    assert r.status_code == 403


def test_payments_are_off_without_keys(member, monkeypatch):
    monkeypatch.setattr(main.settings, "toss_client_key", "")  # 노트북의 .env 에 키가 있어도 꺼진 상태로 본다
    monkeypatch.setattr(main.settings, "toss_secret_key", "")
    assert client.get("/api/payments/checkout").status_code == 404


def test_order_amount_is_decided_by_server(toss, member):
    checkout = client.get("/api/payments/checkout").json()
    assert checkout["client_key"] == "test_gck_dummy"
    assert [(p["credits"], p["amount"]) for p in checkout["products"]] == [(100, 1000), (500, 4500), (1000, 8000)]
    order = new_order("credits_500")
    assert order["amount"] == 4500
    assert 6 <= len(order["order_id"]) <= 64
    r = client.post("/api/payments/orders", json={"product": "credits_free", "customer_key": checkout["customer_key"]})
    assert r.status_code == 400


def test_customer_key_is_reused_after_first_order(toss, member):
    first = client.get("/api/payments/checkout").json()["customer_key"]
    client.post("/api/payments/orders", json={"product": "credits_100", "customer_key": first})
    assert client.get("/api/payments/checkout").json()["customer_key"] == first
    assert 22 <= len(first) <= 50 and "-" in first


def test_amount_mismatch_is_not_confirmed(toss, member):
    order = new_order("credits_500")
    before = store.credit_balance(MEMBER.id)
    r = client.post("/api/payments/confirm", json={"payment_key": "pk-1", "order_id": order["order_id"], "amount": 100})
    assert r.status_code == 400
    assert toss == [] and store.credit_balance(MEMBER.id) == before


def test_confirmed_payment_adds_credits_once(toss, member):
    order = new_order("credits_500")
    before = store.credit_balance(MEMBER.id)
    body = {"payment_key": "pk-2", "order_id": order["order_id"], "amount": 4500}
    r = client.post("/api/payments/confirm", json=body)
    assert r.status_code == 200 and r.json()["credits"] == 500
    assert store.credit_balance(MEMBER.id) == before + 500
    again = client.post("/api/payments/confirm", json=body)
    assert again.status_code == 409
    assert len(toss) == 1 and store.credit_balance(MEMBER.id) == before + 500
    assert toss[0] == {"payment_key": "pk-2", "order_id": order["order_id"], "amount": 4500}


def test_rejected_payment_adds_nothing(toss, member):
    order = new_order("credits_100")
    before = store.credit_balance(MEMBER.id)
    r = client.post("/api/payments/confirm", json={"payment_key": "pk-reject", "order_id": order["order_id"], "amount": 1000})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "REJECT_CARD_COMPANY"
    assert store.credit_balance(MEMBER.id) == before
    assert store.get_payment_order(MEMBER.id, order["order_id"])["status"] == "failed"


def test_cannot_confirm_someone_elses_order(toss, member):
    order = new_order("credits_100")
    app.dependency_overrides[current_user] = lambda: User("member-2", anonymous=False)
    r = client.post("/api/payments/confirm", json={"payment_key": "pk-3", "order_id": order["order_id"], "amount": 1000})
    assert r.status_code == 404 and toss == []


def test_secret_key_is_never_sent_to_browser(toss, member):
    assert "test_gsk_dummy" not in client.get("/api/config").text
    assert "test_gsk_dummy" not in client.get("/api/payments/checkout").text
