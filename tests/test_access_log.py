"""서버 접속 로그(uvicorn.access)에 결제 복귀 주소의 paymentKey 가 그대로 남지 않는다.

토스는 결제 뒤 /?pay=success&orderId=..&paymentKey=..&amount=.. 로 돌려보낸다(주문서형 결제 JavaScript SDK 문서).
Cloud Run 이 Google 쪽에서 남기는 요청 로그는 앱이 막을 수 없다(docs/decisions.md).
"""
import logging

import app.main  # noqa: F401  접속 로그 필터를 붙인다

PATH = "/?pay=success&paymentType=NORMAL&orderId=credit-abc123&paymentKey=tgen_SECRET123&amount=1000"


def access_line(caplog, path):
    logger = logging.getLogger("uvicorn.access")
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", path, "1.1", 200)
    return caplog.records[-1].getMessage()


def test_payment_key_is_hidden_in_access_log(caplog):
    line = access_line(caplog, PATH)
    assert "tgen_SECRET123" not in line
    assert "paymentKey=[가림]" in line
    assert "orderId=credit-abc123" in line and "amount=1000" in line  # 나머지는 그대로 둔다


def test_other_requests_are_logged_as_is(caplog):
    assert "/api/credits?x=1" in access_line(caplog, "/api/credits?x=1")
