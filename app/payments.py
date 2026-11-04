"""크레딧 결제. 토스페이먼츠 주문서형 결제(v2, 예전 이름 결제위젯)로 크레딧 묶음을 판다.

흐름(근거 문서는 토스페이먼츠 연동 가이드 MCP 로 찾았다):
① 서버가 주문번호와 금액을 먼저 저장한다(/api/payments/orders).
② 화면이 주문서형 결제 UI로 결제를 요청하면, 인증 뒤 successUrl 로 paymentKey, orderId, amount 가 돌아온다.
③ 서버가 저장해 둔 금액과 같은지 확인한 뒤 결제 승인 API를 부른다. 승인되면 크레딧을 더한다.
- https://docs.tosspayments.com/guides/v2/payment-widget/integration (주문서형 결제 연동하기)
- https://docs.tosspayments.com/guides/v2/get-started/payment-flow (결제 흐름 이해하기, 결제 정보 검증하기)
- https://docs.tosspayments.com/guides/v2/get-started/llms-quick-reference (식별자 규칙, 승인 API, Idempotency-Key)
- https://docs.tosspayments.com/reference/using-api/authorization (멱등키 헤더)

시크릿 키는 이 파일에서만 쓰고, 로그나 응답에 넣지 않는다. 인증 뒤 10분 안에 승인해야 한다.
"""
import base64
import time
import uuid

import httpx

from app.logs import log

CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"
RETRY_WAITS = (1, 2)  # 결과를 모르는 실패 뒤 같은 멱등키로 다시 보내기 전에 기다리는 초. 인증 뒤 10분 안에 끝나야 한다

# 상품은 서버에서만 정한다. 화면이 보낸 금액은 믿지 않는다
PRODUCTS = {
    "credits_100": {"name": "크레딧 100개", "credits": 100, "amount": 1000},
    "credits_500": {"name": "크레딧 500개", "credits": 500, "amount": 4500},
    "credits_1000": {"name": "크레딧 1,000개", "credits": 1000, "amount": 8000},
}


class TossError(Exception):
    """토스페이먼츠가 돌려준 에러. code 와 message 는 토스 응답 그대로다.
    retriable 이면 승인됐는지 모르는 실패라서, 주문을 끝내지 않고 같은 멱등키로 다시 승인한다."""

    def __init__(self, code, message, retriable=False):
        super().__init__(code)
        self.code, self.message, self.retriable = code, message, retriable


def new_order_id():
    """토스 orderId 규칙: 영문, 숫자, - _ = 로 6~64자. 결제마다 달라야 한다."""
    return f"credit-{uuid.uuid4().hex}"


def new_customer_key():
    """토스 customerKey 규칙: 22~50자, 특수문자 하나 이상, 회원 ID처럼 예측 가능한 값은 쓰지 않는다. UUID 권장."""
    return str(uuid.uuid4())


def new_idempotency_key():
    """승인 요청 멱등키. 주문을 만들 때 한 번 만들어 저장하고, 그 주문의 승인 요청에는 늘 이 값을 보낸다.
    토스는 같은 멱등키(+ API 키, 주소, 메서드)의 요청에 첫 응답을 그대로 돌려준다. 최대 300자, 15일 유효."""
    return str(uuid.uuid4())


def confirm(secret_key, payment_key, order_id, amount, idempotency_key):
    """결제 승인 API. 성공하면 Payment 객체(dict), 실패하면 TossError.
    에러가 나도 멱등키를 바꿔 다시 보내지 않는다(토스 문서: 원인을 확인한 뒤 재요청)."""
    # 시크릿 키와 paymentKey 는 로그에 남기지 않는다
    log("INFO", "결제 승인 요청", order_id=order_id, idempotency_key=idempotency_key)
    token = base64.b64encode(f"{secret_key}:".encode()).decode()  # 시크릿 키 뒤의 콜론을 빠뜨리지 않는다
    try:
        r = httpx.post(CONFIRM_URL, timeout=30,
                       headers={"Authorization": f"Basic {token}", "Content-Type": "application/json",
                                "Idempotency-Key": idempotency_key},
                       json={"paymentKey": payment_key, "orderId": order_id, "amount": amount})
    except httpx.HTTPError as error:  # 연결 실패, 시간 초과. 토스가 승인했는지 모른다
        raise TossError("NETWORK_ERROR", "결제 승인 응답을 받지 못했습니다.", retriable=True) from error
    try:
        data = r.json() if r.content else {}
    except ValueError:  # 5xx 에서 JSON 이 아닌 본문이 올 수 있다
        data = {}
    if r.status_code != 200:
        code = data.get("code", f"HTTP_{r.status_code}")
        # 5xx 는 "잠시 후 다시 시도"(에러 코드 문서), 409 IDEMPOTENT_REQUEST_PROCESSING 은 이전 요청 처리 중(멱등키 문서)
        retriable = r.status_code >= 500 or code == "IDEMPOTENT_REQUEST_PROCESSING"
        raise TossError(code, data.get("message", "결제를 승인하지 못했습니다."), retriable=retriable)
    return data


def confirm_with_retry(secret_key, payment_key, order_id, amount, idempotency_key):
    """결과를 모르는 실패면 RETRY_WAITS 만큼 기다렸다가 같은 멱등키로 다시 보낸다. 마지막 에러는 그대로 올린다."""
    for wait in (*RETRY_WAITS, None):
        try:
            return confirm(secret_key, payment_key, order_id, amount, idempotency_key)
        except TossError as error:
            if not error.retriable or wait is None:
                raise
            log("WARNING", "결제 승인 재시도", order_id=order_id, code=error.code)
            time.sleep(wait)
