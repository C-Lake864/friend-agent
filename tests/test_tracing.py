"""Langfuse 기록 도구 테스트. 실제로 보내지는 않는다(conftest 가 키를 비운다)."""
from app import tracing
from app.llm import FakeLLM


def test_mask_hides_personal_patterns():
    text = "내 번호는 010-1234-5678, 메일은 me@example.com, 카드 4111 1111 1111 1111, 키 sk-ant-abcdEFGH1234"
    masked = tracing.mask(data=text)
    for secret in ("010-1234-5678", "me@example.com", "4111 1111 1111 1111", "sk-ant-abcdEFGH1234"):
        assert secret not in masked
    assert masked.count(tracing.MASKED) == 4


def test_mask_keeps_ordinary_text_and_walks_nested_values():
    data = {"messages": [{"role": "user", "content": "내일 서울 비 와? 연락처 02-123-4567"}],
            "date": "2026-11-02T14:30:00+09:00", "count": 3}
    masked = tracing.mask(data=data)
    assert masked["messages"][0]["content"] == "내일 서울 비 와? 연락처 " + tracing.MASKED
    assert masked["date"] == data["date"]
    assert masked["count"] == 3


def test_tracing_is_off_without_keys():
    assert not tracing.enabled()
    with tracing.step("chat-turn", input="안녕") as trace:
        assert trace is tracing.OFF
        with tracing.step("reply", trace, as_type="generation") as obs:
            assert obs is tracing.OFF


def test_fake_llm_fills_record_with_fake_model_and_zero_tokens():
    record = {}
    text = "".join(FakeLLM(delay=0).chat_stream([{"role": "system", "content": "이름: 하루"},
                                                 {"role": "user", "content": "안녕"}], record=record))
    assert record["model"] == "fake"
    assert record["output"] == text
    assert record["usage"] == {"input": 0, "output": 0}
    assert tracing.model_fields(record)["usage_details"] == {"input": 0, "output": 0}
