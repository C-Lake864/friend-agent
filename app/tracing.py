"""Langfuse 기록(트레이스)을 한곳에 모아 둔다.

- LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY 가 없으면 아무것도 보내지 않는 OFF 객체를 돌려준다. 앱은 그대로 돈다.
- 부모 기록을 직접 넘겨 자식을 만든다. /api/chat 은 run_turn 제너레이터를 스트리밍으로 흘려보내는데,
  조각마다 다른 스레드에서 실행될 수 있어 "지금 열려 있는 기록"을 자동으로 찾는 방식(@observe)은 부모를 잃기 쉽다.
- 원문은 보내되(docs/decisions.md), 보내기 전에 전화번호, 이메일, 카드 번호, API 키 모양은 가린다.
  서버 로그(app/logs.py)에는 여전히 원문을 남기지 않는다.

근거 문서: https://langfuse.com/docs/observability/sdk/python/instrumentation
         https://langfuse.com/docs/observability/features/masking (Langfuse(mask=...))
"""
import os
import re
from contextlib import contextmanager

MASKED = "[가림]"
PATTERNS = [
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}"),                      # API 키 (sk-…, sk-ant-…, pk-lf-…)
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),                              # Google API 키
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                          # 이메일
    re.compile(r"(?:\+82[- ]?|\b0)\d{1,2}[- .]?\d{3,4}[- .]?\d{4}\b"),    # 전화번호
    re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{1,7}\b"),              # 카드 번호(13~19자리)
]


def mask_text(text):
    for pattern in PATTERNS:
        text = pattern.sub(MASKED, text)
    return text


def mask(*, data, **kwargs):
    """Langfuse 가 입력, 출력, metadata 를 보내기 전에 부른다. 안쪽 문자열까지 모두 가린다."""
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        return {key: mask(data=value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask(data=value) for value in data]
    return data


class _Off:
    """키가 없을 때 쓰는 빈 기록. 무엇을 불러도 아무 일도 하지 않는다."""

    def update(self, **kwargs):
        return self

    def end(self, **kwargs):
        return self


OFF = _Off()
_client = None


def client():
    global _client
    if _client is None:
        if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
            from langfuse import Langfuse
            from langfuse.span_filter import is_langfuse_span

            # 키와 주소(LANGFUSE_BASE_URL)는 환경변수에서 읽는다.
            # anthropic SDK 가 스스로 남기는 자동 계측 기록은 보내지 않는다. 모델 호출은 llm.py 의 record 로
            # 제공사와 상관없이 직접 남기므로, 자동 기록까지 보내면 같은 호출의 비용이 두 번 잡힌다.
            _client = Langfuse(mask=mask, should_export_span=is_langfuse_span)
        else:
            _client = False
    return _client or None


def enabled():
    return client() is not None


@contextmanager
def step(name, parent=None, as_type="span", **fields):
    """관찰 단위 하나를 열고 닫는다. parent 가 None 이면 새 트레이스의 맨 위가 되고, OFF 면 기록하지 않는다."""
    c = client()
    if c is None or parent is OFF:
        yield OFF
        return
    opener = c if parent is None else parent
    obs = opener.start_observation(name=name, as_type=as_type, **fields)
    try:
        yield obs
    except Exception as error:
        obs.update(level="ERROR", status_message=type(error).__name__)
        raise
    finally:
        obs.end()


def model_fields(record):
    """llm.py 가 채운 record 에서 generation 에 남길 모델 이름, 입력, 토큰 수를 꺼낸다."""
    if not record:
        return {}
    return {"model": record["model"], "input": record["input"], "usage_details": record["usage"]}


def flush():
    c = client()
    if c is not None:
        c.flush()
