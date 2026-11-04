"""로그 한 줄을 JSON으로 남긴다.

Cloud Run은 JSON 로그의 severity 값을 읽어 로그 화면의 심각도(정보, 경고, 오류)로 보여 준다.
키나 문의 원문 같은 값은 로그에 남기지 않는다.
"""
import json
import logging
import re
import sys

PAYMENT_KEY = re.compile(r"(paymentKey=)[^&\s\"]+")


def log(severity, message, **fields):
    record = {"severity": severity, "message": message, **fields}
    print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)


class HidePaymentKey(logging.Filter):
    """서버 접속 로그(uvicorn.access)에서 결제 복귀 주소의 paymentKey 값을 가린다.
    토스가 /?pay=success&...&paymentKey=... 로 돌려보내서 주소째 로그에 남기 때문이다.
    Cloud Run 이 Google 쪽에서 남기는 요청 로그는 여기서 막을 수 없다(docs/decisions.md)."""

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(PAYMENT_KEY.sub(r"\1[가림]", a) if isinstance(a, str) else a for a in record.args)
        return True


def hide_payment_key_in_access_log():
    logging.getLogger("uvicorn.access").addFilter(HidePaymentKey())
