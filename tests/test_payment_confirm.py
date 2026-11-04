"""결제 승인 상황 네 가지: 같은 요청 두 번, 금액을 바꾼 요청, 다른 사용자의 주문, 승인 실패.

토스페이먼츠는 부르지 않는다. 승인 API로 나가는 HTTP 요청(httpx.post)을 가짜로 바꿔 끼우고,
가짜 응답은 토스페이먼츠 연동 가이드 MCP 의 문서 모양을 따른다.
- 성공: Payment 객체 (https://docs.tosspayments.com/reference#payment-객체, 결제 승인 > Response)
- 실패: 에러 객체 {"code", "message"} (https://docs.tosspayments.com/reference/using-api/req-res#에러-객체)
- 에러 코드와 HTTP 상태: https://docs.tosspayments.com/reference/error-codes
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, payments
from app.auth import User
from app.main import app, current_user, store

client = TestClient(app)
OWNER = User("member-confirm", anonymous=False)
OTHER = User("member-confirm-other", anonymous=False)
PAYMENT_KEY = "tgen_20261104abcdEFGH1234"


def payment_object(payment_key, order_id, order_name, amount):
    """결제 승인 성공 응답(Payment 객체, 카드 결제). 필드 이름과 값의 형식은 코어 API 문서를 따른다."""
    return {
        "version": "2022-06-08",  # 문서에 나온 날짜 기반 응답 버전
        "paymentKey": payment_key,
        "type": "NORMAL",
        "orderId": order_id,
        "orderName": order_name,
        "mId": "tosspayments",
        "currency": "KRW",
        "method": "카드",
        "totalAmount": amount,
        "balanceAmount": amount,
        "status": "DONE",
        "requestedAt": "2026-11-04T15:00:00+09:00",
        "approvedAt": "2026-11-04T15:00:10+09:00",
        "useEscrow": False,
        "lastTransactionKey": "9C62B18EEF0DE3EB7F4422EB6D14BC6E",
        "suppliedAmount": round(amount / 1.1),
        "vat": amount - round(amount / 1.1),
        "cultureExpense": False,
        "taxFreeAmount": 0,
        "taxExemptionAmount": 0,
        "cancels": None,
        "isPartialCancelable": True,
        "card": {"amount": amount, "issuerCode": "71", "acquirerCode": "71", "number": "12345678****000*",
                 "installmentPlanMonths": 0, "approveNo": "00000000", "useCardPoint": False, "cardType": "신용",
                 "ownerType": "개인", "acquireStatus": "READY", "isInterestFree": False, "interestPayer": None},
        "virtualAccount": None, "transfer": None, "mobilePhone": None, "giftCertificate": None,
        "cashReceipt": None, "cashReceipts": None, "discount": None, "easyPay": None, "failure": None,
        "secret": None, "country": "KR",
        "receipt": {"url": "https://dashboard.tosspayments.com/receipt/redirection?transactionId=example"},
        "checkout": {"url": "https://api.tosspayments.com/v1/payments/example/checkout"},
        "metadata": None,
    }


# 승인 API 에러: (HTTP 상태, 에러 객체). 코드와 메시지는 에러 코드 문서 그대로
REJECTED = (403, {"code": "REJECT_CARD_COMPANY", "message": "결제 승인이 거절되었습니다."})
EXPIRED = (404, {"code": "NOT_FOUND_PAYMENT_SESSION", "message": "결제 시간이 만료되어 결제 진행 데이터가 존재하지 않습니다."})
INTERNAL = (500, {"code": "FAILED_INTERNAL_SYSTEM_PROCESSING", "message": "내부 시스템 처리 작업이 실패했습니다. 잠시 후 다시 시도해주세요."})


@pytest.fixture
def toss(monkeypatch):
    """결제를 켜고 승인 API를 가짜로 바꾼다. toss.fail 에 (상태, 에러 객체)를 넣으면 그 에러를 돌려준다."""
    monkeypatch.setattr(main.settings, "toss_client_key", "test_gck_dummy")
    monkeypatch.setattr(main.settings, "toss_secret_key", "test_gsk_dummy")

    class Toss:
        calls = []
        fail = None

    def fake_post(url, headers, json, timeout):
        Toss.calls.append({"url": url, "json": json})
        if Toss.fail:
            status, error = Toss.fail
            return httpx.Response(status, json=error)
        order = next(o for o in store.orders.values() if o["order_id"] == json["orderId"])
        return httpx.Response(200, json=payment_object(json["paymentKey"], json["orderId"],
                                                       payments.PRODUCTS[order["product"]]["name"], json["amount"]))

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    yield Toss
    app.dependency_overrides.pop(current_user, None)


def login(user):
    app.dependency_overrides[current_user] = lambda: user


def new_order(user, product="credits_500"):
    login(user)
    key = client.get("/api/payments/checkout").json()["customer_key"]
    return client.post("/api/payments/orders", json={"product": product, "customer_key": key}).json()


def confirm(order, amount=None):
    return client.post("/api/payments/confirm", json={"payment_key": PAYMENT_KEY, "order_id": order["order_id"],
                                                     "amount": order["amount"] if amount is None else amount})


def test_same_confirm_twice_calls_toss_once_and_adds_credits_once(toss):
    order = new_order(OWNER)
    before = store.credit_balance(OWNER.id)
    first, second = confirm(order), confirm(order)
    assert first.status_code == 200 and first.json()["credits"] == 500
    assert second.status_code == 409
    assert len(toss.calls) == 1
    assert store.credit_balance(OWNER.id) == before + 500
    assert store.get_payment_order(OWNER.id, order["order_id"])["status"] == "paid"


def test_changed_amount_is_rejected_without_calling_toss(toss):
    order = new_order(OWNER, "credits_1000")
    before = store.credit_balance(OWNER.id)
    r = confirm(order, amount=1000)  # 8,000원 주문을 1,000원으로 바꿔 보냄
    assert r.status_code == 400
    assert toss.calls == []
    assert store.credit_balance(OWNER.id) == before
    assert store.get_payment_order(OWNER.id, order["order_id"])["status"] == "ready"


def test_someone_elses_order_is_not_found_without_calling_toss(toss):
    order = new_order(OWNER)
    other_before = store.credit_balance(OTHER.id)
    login(OTHER)
    r = confirm(order)
    assert r.status_code == 404
    assert toss.calls == []
    assert store.credit_balance(OTHER.id) == other_before
    assert store.get_payment_order(OWNER.id, order["order_id"])["status"] == "ready"


# 토스 500 처럼 결과를 모르는 실패는 다시 승인할 수 있게 남긴다(tests/test_payment_retry.py)
@pytest.mark.parametrize("failure", [REJECTED, EXPIRED], ids=["card-rejected", "session-expired"])
def test_failed_confirm_adds_no_credits_and_passes_toss_error(toss, failure):
    toss.fail = failure
    order = new_order(OWNER, "credits_100")
    before = store.credit_balance(OWNER.id)
    r = confirm(order)
    status, error = failure
    assert r.status_code == 400 and r.json()["detail"] == error
    assert len(toss.calls) == 1
    assert store.credit_balance(OWNER.id) == before
    saved = store.get_payment_order(OWNER.id, order["order_id"])
    assert saved["status"] == "failed" and saved["fail_code"] == error["code"]
