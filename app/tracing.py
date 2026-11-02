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
from contextlib import contextmanager, nullcontext
from datetime import datetime
from zoneinfo import ZoneInfo

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
            _client = Langfuse(mask=mask, should_export_span=is_langfuse_span,
                               environment=os.environ.get("ENV", "local").strip().lower() or "local")
        else:
            _client = False
    return _client or None


def enabled():
    return client() is not None


def turn_attrs(uid):
    """트레이스에 붙일 속성. 한 사람의 하루치 대화를 세션 하나로 본다(docs/decisions.md)."""
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    return {"user_id": str(uid), "session_id": f"{uid}-{today}",
            "version": os.environ.get("K_REVISION", "local"),  # Cloud Run 이 리비전 이름을 넣어 준다
            "tags": [], "metadata": {"commit": os.environ.get("COMMIT_SHA", "local")}}


def add_attrs(obs, tags=(), **metadata):
    """도구 선택처럼 턴 중간에 알게 된 값을, 이 뒤에 만드는 관찰 단위부터 함께 붙인다."""
    attrs = _attrs.get(id(obs))
    if attrs is not None:
        attrs["tags"] = sorted(set(attrs["tags"]) | set(tags))
        attrs["metadata"].update({key: value for key, value in metadata.items() if value is not None})


_attrs = {}  # 열려 있는 관찰 단위 → 속성. 자식을 만들 때마다 같은 속성을 다시 붙인다


@contextmanager
def step(name, parent=None, as_type="span", attrs=None, **fields):
    """관찰 단위 하나를 열고 닫는다. parent 가 None 이면 새 트레이스의 맨 위가 되고, OFF 면 기록하지 않는다.

    속성(사용자, 세션, 버전, 태그, metadata)은 맨 위에서 attrs 로 한 번 정하면 자식이 물려받는다.
    propagate_attributes 는 기록을 만드는 순간의 실행 맥락에만 걸리는데, 스트리밍 제너레이터는 조각마다
    맥락이 바뀔 수 있어서 자식을 만들 때마다 부모의 속성으로 다시 감싼다.
    """
    c = client()
    if c is None or parent is OFF:
        yield OFF
        return
    from langfuse import propagate_attributes

    opener = c if parent is None else parent
    attrs = attrs if parent is None else _attrs.get(id(parent))
    with propagate_attributes(**attrs) if attrs else nullcontext():
        obs = opener.start_observation(name=name, as_type=as_type, **fields)
    if attrs is not None:
        _attrs[id(obs)] = attrs
    try:
        yield obs
    except Exception as error:
        obs.update(level="ERROR", status_message=type(error).__name__)
        raise
    finally:
        obs.end()
        _attrs.pop(id(obs), None)


def model_fields(record):
    """llm.py 가 채운 record 에서 generation 에 남길 모델 이름, 입력, 토큰 수를 꺼낸다."""
    if not record:
        return {}
    return {"model": record["model"], "input": record["input"], "usage_details": record["usage"]}


def trace_id(obs):
    """메시지에 함께 저장해 둘 트레이스 ID. 기록이 꺼져 있으면 None."""
    return getattr(obs, "trace_id", None)


def score(trace, name, value, comment=None):
    """사용자 반응을 트레이스에 BOOLEAN 점수(1 또는 0)로 붙인다. 키나 트레이스 ID가 없으면 건너뛰고 False.
    근거 문서: https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk"""
    c = client()
    if c is None or not trace:
        return False
    c.create_score(name=name, value=float(value), trace_id=trace, data_type="BOOLEAN",
                   comment=mask_text(comment)[:500] if comment else None)  # 신고 사유도 보내기 전에 가린다
    c.flush()  # Cloud Run 에서 응답 뒤에 잃지 않게 바로 보낸다
    return True


def flush():
    c = client()
    if c is not None:
        c.flush()
