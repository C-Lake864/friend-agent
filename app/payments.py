"""크레딧 결제. 토스페이먼츠 주문서형 결제(v2, 예전 이름 결제위젯)로 크레딧 묶음을 판다.

흐름(근거 문서는 토스페이먼츠 연동 가이드 MCP 로 찾았다):
① 서버가 주문번호와 금액을 먼저 저장한다(/api/payments/orders).
② 화면이 주문서형 결제 UI로 결제를 요청하면, 인증 뒤 successUrl 로 paymentKey, orderId, amount 가 돌아온다.
③ 서버가 저장해 둔 금액과 같은지 확인한 뒤 결제 승인 API를 부른다. 승인되면 크레딧을 더한다.
- https://docs.tosspayments.com/guides/v2/payment-widget/integration (주문서형 결제 연동하기)
- https://docs.tosspayments.com/guides/v2/get-started/payment-flow (결제 흐름 이해하기, 결제 정보 검증하기)
- https://docs.tosspayments.com/guides/v2/get-started/llms-quick-reference (식별자 규칙, 승인 API, Idempotency-Key)

시크릿 키는 이 파일에서만 쓰고, 로그나 응답에 넣지 않는다. 인증 뒤 10분 안에 승인해야 한다.
"""
import base64
import uuid

import httpx

CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"

# 상품은 서버에서만 정한다. 화면이 보낸 금액은 믿지 않는다
PRODUCTS = {
    "credits_100": {"name": "크레딧 100개", "credits": 100, "amount": 1000},
    "credits_500": {"name": "크레딧 500개", "credits": 500, "amount": 4500},
    "credits_1000": {"name": "크레딧 1,000개", "credits": 1000, "amount": 8000},
}


class TossError(Exception):
    """토스페이먼츠가 돌려준 에러. code 와 message 는 토스 응답 그대로다."""

    def __init__(self, code, message):
        super().__init__(code)
        self.code, self.message = code, message


def new_order_id():
    """토스 orderId 규칙: 영문, 숫자, - _ = 로 6~64자. 결제마다 달라야 한다."""
    return f"credit-{uuid.uuid4().hex}"


def new_customer_key():
    """토스 customerKey 규칙: 22~50자, 특수문자 하나 이상, 회원 ID처럼 예측 가능한 값은 쓰지 않는다. UUID 권장."""
    return str(uuid.uuid4())


def confirm(secret_key, payment_key, order_id, amount):
    """결제 승인 API. 성공하면 Payment 객체(dict), 실패하면 TossError."""
    token = base64.b64encode(f"{secret_key}:".encode()).decode()  # 시크릿 키 뒤의 콜론을 빠뜨리지 않는다
    try:
        r = httpx.post(CONFIRM_URL, timeout=30,
                       headers={"Authorization": f"Basic {token}", "Content-Type": "application/json",
                                "Idempotency-Key": str(uuid.uuid4())},
                       json={"paymentKey": payment_key, "orderId": order_id, "amount": amount})
    except httpx.HTTPError as error:
        raise TossError("NETWORK_ERROR", "결제 승인 요청을 보내지 못했습니다.") from error
    data = r.json() if r.content else {}
    if r.status_code != 200:
        raise TossError(data.get("code", f"HTTP_{r.status_code}"), data.get("message", "결제를 승인하지 못했습니다."))
    return data
