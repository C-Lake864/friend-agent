import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app, store

client = TestClient(app)


def chat(message):
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200
    return [json.loads(line) for line in r.text.splitlines()]


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_weather_question_picks_weather_tool():
    turn = next(e for e in chat("내일 서울 날씨 어때?") if e["type"] == "turn")
    assert [t["name"] for t in turn["tools"]] == ["get_weather"]


def test_crisis_message_gets_fixed_safety_reply():
    events = chat("요즘 너무 죽고 싶어")
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "109" in text


def test_reminder_is_saved_with_timezone():
    chat("내일 아침 8시에 물 마시라고 알려 줘")
    pending = store.list_reminders("local-user", status="pending")
    assert pending and pending[-1]["due_at"].tzinfo is not None


def test_due_reminder_is_delivered_only_once():
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.add_reminder("local-user", "테스트 약속", due)
    now = datetime.now(timezone.utc)
    first = store.claim_due_reminders("local-user", now)
    second = store.claim_due_reminders("local-user", now)
    assert [r["text"] for r in first].count("테스트 약속") == 1
    assert "테스트 약속" not in [r["text"] for r in second]


def test_ocr_is_off_by_default():
    r = client.post("/api/ocr", files={"photo": ("a.png", b"x", "image/png")})
    assert r.status_code == 404


def answer_id():
    return next(e for e in chat("오늘 좀 피곤해") if e["type"] == "done")["message_id"]


def test_feedback_and_report_on_my_answer_work_without_langfuse_keys():
    message_id = answer_id()
    r = client.post("/api/feedback", json={"message_id": message_id, "liked": False})
    assert r.status_code == 200 and r.json() == {"ok": True, "scored": False}  # 키가 없으면 점수는 건너뛴다
    r = client.post("/api/report", json={"message_id": message_id, "reason": "엉뚱한 답"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_cannot_react_to_someone_elses_or_own_user_message():
    others = store.add_message("someone-else", "assistant", "남의 답", {"trace_id": "t-other"})
    assert client.post("/api/feedback", json={"message_id": others["id"], "liked": True}).status_code == 404
    assert client.post("/api/report", json={"message_id": others["id"]}).status_code == 404
    mine = store.add_message("local-user", "user", "내가 보낸 말")
    assert client.post("/api/feedback", json={"message_id": mine["id"], "liked": True}).status_code == 404
