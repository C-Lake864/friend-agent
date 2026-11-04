"""AI 친구 에이전트 서비스.

화면(/)과 API를 FastAPI 하나가 함께 제공한다.
- /health, /version: 서비스 상태와 배포 버전
- /api/config: 화면이 쓰는 설정(로그인 방식, 개발용 패널 표시 여부)
- /api/character, /api/settings: 캐릭터와 사용자 설정
- /api/chat: 메시지 한 턴. 결과를 이벤트(JSON 한 줄씩)로 스트리밍한다
- /api/greeting: 앱을 열 때 먼저 말 걸기
- /api/ocr: 사진 속 글자 읽기(OCR_ENABLED=true 일 때)
- /api/memories, /api/reminders, /api/data: 기억, 약속, 내 데이터 관리
- /api/credits: 크레딧 잔액과 최근 기록
- /api/payments/*: 크레딧 결제(토스페이먼츠 주문서형 결제, app/payments.py)
"""
import html
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import agent, ocr, payments, tracing, when
from app.auth import Auth, User
from app.config import ROOT, load_settings
from app.llm import make_llm
from app.logs import log
from app.persona import DEFAULT_CHARACTER, OPTIONS, build_persona, clean_character
from app.safety import is_crisis
from app.store.base import make_store
from app.tools import CITIES

settings = load_settings()  # 꼭 필요한 값이 없으면 여기서 종료한다
llm = make_llm(settings)
store = make_store(settings)
current_user = Auth(settings)
log("INFO", "Langfuse 기록", enabled=tracing.enabled())


@asynccontextmanager
async def lifespan(_app):
    yield
    tracing.flush()  # 종료 신호를 받으면 버퍼에 남은 기록을 보내고 끝낸다


app = FastAPI(title="AI 친구 에이전트", lifespan=lifespan)

# 시작 지연 스위치(배포 실습용). 포트는 바로 열지만 정한 시간 동안은 준비 중이라 모든 요청에 503을 돌려준다.
# AI 서비스가 시작할 때 모델 설정이나 색인을 불러오느라 늦게 준비되는 상황을 흉내 낸다. 기본값 0(끔).
SLOW_START_SECONDS = float(os.environ.get("SLOW_START_SECONDS", "0") or 0)
READY_AT = time.time() + SLOW_START_SECONDS
if SLOW_START_SECONDS:
    log("WARNING", f"시작 지연 스위치가 켜져 있습니다. {SLOW_START_SECONDS:.0f}초 동안 준비 중으로 응답합니다.")


@app.middleware("http")
async def not_ready_yet(request, call_next):
    if time.time() < READY_AT:
        return JSONResponse({"status": "starting"}, status_code=503)
    return await call_next(request)


def get_llm():
    """테스트에서는 이 함수를 가짜 LLM을 돌려주는 함수로 바꿔 끼운다."""
    return llm


class ChatRequest(BaseModel):
    message: str


class SettingsRequest(BaseModel):
    location: str | None = None
    timezone: str | None = None


class ReportRequest(BaseModel):
    message_id: int
    reason: str = ""


class FeedbackRequest(BaseModel):
    message_id: int
    liked: bool  # 좋아요 true, 별로예요 false


class OrderRequest(BaseModel):
    product: str
    customer_key: str  # 화면이 결제 UI를 초기화할 때 쓴 값. 이미 정해진 값이 있으면 그 값을 쓴다


class ConfirmRequest(BaseModel):
    payment_key: str = Field(min_length=1, max_length=200)  # 토스가 준 값 그대로. 형식은 검사하지 않는다
    order_id: str = Field(min_length=6, max_length=64)
    amount: int


@app.get("/", response_class=HTMLResponse)
def home():
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    banner = "" if settings.env in ("production", "local") else f'<div class="banner">테스트 환경 ({settings.env})</div>'
    if os.environ.get("BANNER"):  # 이미지를 새로 만들지 않고 환경변수만 바꿔 새 리비전을 만들 때 쓰는 화면 문구
        banner += f'<div class="banner">{html.escape(os.environ["BANNER"][:80])}</div>'
    return page.replace("<!--ENV_BANNER-->", banner)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"commit": settings.commit, "revision": settings.revision, "env": settings.env,
            "llm": settings.provider, "model": settings.model, "storage": settings.storage, "auth": settings.auth,
            "banner": os.environ.get("BANNER", "")}


@app.get("/api/config")
def config():
    # publishable key 는 브라우저에 공개해도 되는 키다. secret key 는 절대 여기에 넣지 않는다.
    return {"env": settings.env, "auth": settings.auth, "storage": settings.storage,
            "supabase_url": settings.supabase_url if settings.auth == "supabase" else "",
            "supabase_publishable_key": settings.supabase_publishable_key if settings.auth == "supabase" else "",
            "google_login": settings.google_login, "show_turn_panel": settings.show_turn_panel,
            "ocr_enabled": settings.ocr_enabled, "payments_enabled": settings.payments_enabled,
            "options": OPTIONS, "cities": list(CITIES)}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "anonymous": user.anonymous, "email": user.email,
            "has_character": store.get_character(user.id) is not None}


# ── 캐릭터와 설정 ──────────────────────────────────────────────────

@app.get("/api/character")
def get_character(user: User = Depends(current_user)):
    c = store.get_character(user.id)
    return {"character": c or DEFAULT_CHARACTER, "saved": c is not None}


@app.put("/api/character")
def put_character(data: dict, user: User = Depends(current_user)):
    c = clean_character(data)
    store.save_character(user.id, c)
    log("INFO", "캐릭터 저장")
    return {"character": c}


@app.post("/api/character/preview")
def preview_character(data: dict):
    return {"prompt": build_persona(clean_character(data))}


@app.get("/api/settings")
def get_settings(user: User = Depends(current_user)):
    return store.get_settings(user.id)


@app.put("/api/settings")
def put_settings(req: SettingsRequest, user: User = Depends(current_user)):
    changes = {}
    if req.location in CITIES:
        changes["location"] = req.location
    if req.timezone:
        changes["timezone"] = req.timezone if req.timezone == "Asia/Seoul" else "Asia/Seoul"  # 지금은 한국 시간만
    store.save_settings(user.id, changes)
    return store.get_settings(user.id)


# ── 대화 ───────────────────────────────────────────────────────────

@app.get("/api/messages")
def messages(user: User = Depends(current_user)):
    show = settings.show_turn_panel
    return [{"id": m["id"], "role": m["role"], "content": m["content"],
             "meta": m["meta"] if show else {}, "created_at": m["created_at"].isoformat()}
            for m in store.recent_messages(user.id, 50)]


# ── 크레딧 ─────────────────────────────────────────────────────────
# 메시지 한 턴에 1. 답을 끝까지 만든 뒤(done)에만 빼므로, 중간에 실패하면 빠지지 않는다.
# 위기 표현에는 잔액과 상관없이 안전 응답을 보내고 빼지 않는다(docs/decisions.md).
FREE_CREDITS = 50
CHAT_COST = 1
NO_CREDITS_MESSAGE = "크레딧이 부족합니다. 충전 후 이용해 주세요."


def credit_balance(uid):
    # 처음 온 사용자에게 무료 크레딧을 준다. 두 번째부터는 ref 가 같아서 더해지지 않는다
    store.add_credit(uid, FREE_CREDITS, "signup", f"signup:{uid}")
    return store.credit_balance(uid)


@app.get("/api/credits")
def credits(user: User = Depends(current_user)):
    balance = credit_balance(user.id)
    return {"balance": balance,
            "history": [{"delta": c["delta"], "reason": c["reason"], "ref": c["ref"],
                         "created_at": c["created_at"].isoformat()} for c in store.recent_credits(user.id, 10)]}


# ── 크레딧 결제 ────────────────────────────────────────────────────
# 구글 로그인한 회원만 산다. 게스트는 브라우저 기록을 지우면 계정과 산 크레딧을 잃기 때문이다(docs/decisions.md).

def paying_member(user: User = Depends(current_user)):
    if not settings.payments_enabled:
        raise HTTPException(404, "결제가 꺼져 있습니다")
    if user.anonymous:
        raise HTTPException(403, "구글 로그인 후 충전할 수 있습니다")
    return user


def is_uuid(value):
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


@app.get("/api/payments/checkout")
def checkout(user: User = Depends(paying_member)):
    # 첫 결제 전에는 새 값을 주고, 첫 주문을 만들 때 저장한다
    return {"client_key": settings.toss_client_key,
            "customer_key": store.payment_customer_key(user.id) or payments.new_customer_key(),
            "products": [{"id": pid, **p} for pid, p in payments.PRODUCTS.items()]}


@app.post("/api/payments/orders")
def create_order(req: OrderRequest, user: User = Depends(paying_member)):
    product = payments.PRODUCTS.get(req.product)
    if not product:
        raise HTTPException(400, "없는 상품입니다")
    customer_key = store.payment_customer_key(user.id) or (req.customer_key if is_uuid(req.customer_key) else None)
    if not customer_key:
        raise HTTPException(400, "결제 화면을 다시 열어 주세요")
    order = store.add_payment_order(user.id, {
        "order_id": payments.new_order_id(), "customer_key": customer_key, "product": req.product,
        "credits": product["credits"], "amount": product["amount"],
        "idempotency_key": payments.new_idempotency_key()})
    log("INFO", "결제 주문 생성", product=req.product)
    return {"order_id": order["order_id"], "order_name": product["name"], "amount": order["amount"]}


@app.post("/api/payments/confirm")
def confirm_payment(req: ConfirmRequest, user: User = Depends(paying_member)):
    order = store.get_payment_order(user.id, req.order_id)
    if not order:
        raise HTTPException(404, "주문을 찾지 못했습니다")
    if order["status"] != "ready":
        raise HTTPException(409, "이미 처리된 주문입니다")
    if req.amount != order["amount"]:  # 화면에서 금액을 바꿨다. 승인하지 않으면 결제되지 않는다
        log("WARNING", "결제 금액 불일치", product=order["product"])
        raise HTTPException(400, "결제 금액이 주문과 다릅니다")
    try:
        payments.confirm_with_retry(settings.toss_secret_key, req.payment_key, order["order_id"], order["amount"],
                                    order["idempotency_key"])
    except payments.TossError as error:
        log("WARNING", "결제 승인 실패", code=error.code, retriable=error.retriable)
        if error.retriable:  # 승인됐는지 모른다. 주문을 ready 로 두고 같은 멱등키로 다시 승인하게 한다
            raise HTTPException(503, {"code": error.code, "retry": True,
                                      "message": "결제 확인이 늦어지고 있어요. 잠시 뒤 다시 시도해 주세요."}) from error
        if error.code != "ALREADY_PROCESSED_PAYMENT":  # 같은 결제를 다른 요청이 먼저 승인한 경우는 실패로 바꾸지 않는다
            store.finish_payment_order(user.id, order["order_id"], {"status": "failed", "fail_code": error.code})
        raise HTTPException(400, {"code": error.code, "message": error.message}) from error
    # 돈이 빠져나갔으니 크레딧은 꼭 더한다. ref 가 주문번호라 두 번 더해지지 않는다
    store.add_credit(user.id, order["credits"], "purchase", f"payment:{order['order_id']}")
    store.finish_payment_order(user.id, order["order_id"], {
        "status": "paid", "payment_key": req.payment_key, "paid_at": datetime.now(timezone.utc)})
    log("INFO", "결제 승인", product=order["product"])
    return {"ok": True, "credits": order["credits"], "balance": store.credit_balance(user.id)}


@app.post("/api/chat")
def chat(req: ChatRequest, user: User = Depends(current_user), model=Depends(get_llm)):
    message = req.message.strip()[:1000]
    if not message:
        raise HTTPException(400, "메시지가 비어 있습니다")
    crisis = is_crisis(message)
    balance = credit_balance(user.id)
    if balance < CHAT_COST and not crisis:
        log("INFO", "크레딧 부족 거절")
        return JSONResponse({"error": "insufficient_credits", "message": NO_CREDITS_MESSAGE, "balance": balance},
                            status_code=402)

    def charge(message_id):
        try:
            store.add_credit(user.id, -CHAT_COST, "chat", f"chat:{message_id}")
        except Exception as error:  # 답은 이미 보냈으니 차감 실패로 턴을 실패시키지 않는다
            log("ERROR", "크레딧 차감 실패", error=type(error).__name__)

    def events():
        try:
            for event in agent.run_turn(message, user.id, model, store, settings.show_turn_panel):
                if event["type"] == "done" and not crisis:
                    charge(event["message_id"])  # done 을 보내기 전에 빼서, 화면이 바로 새 잔액을 읽게 한다
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"
        except Exception as error:
            log("ERROR", "대화 처리 실패", error=type(error).__name__)
            yield json.dumps({"type": "error", "message": "답을 만들지 못했어요. 잠시 뒤 다시 보내 주세요."}, ensure_ascii=False) + "\n"
        finally:
            # Cloud Run 은 응답이 끝나면 CPU를 거의 주지 않아, SDK가 모아 둔 기록을 못 보내고 잃을 수 있다.
            # 응답을 닫기 전에 보낸다. 대가는 응답이 아주 조금 늦게 닫히는 것이다.
            tracing.flush()

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/api/greeting")
def greeting(user: User = Depends(current_user), model=Depends(get_llm)):
    result = agent.greet(user.id, model, store)
    tracing.flush()
    return {"greeting": result}


@app.post("/api/ocr")
async def read_photo(photo: UploadFile, user: User = Depends(current_user)):
    if not settings.ocr_enabled:
        raise HTTPException(404, "사진 글자 읽기가 꺼져 있습니다")
    try:
        return {"text": ocr.read_text(await photo.read())}
    except ocr.OcrError as error:
        raise HTTPException(500, str(error)) from error


def my_answer(user, message_id):
    """로그인한 사용자의 캐릭터 답만 돌려준다. 남의 메시지나 내 메시지(user)에는 반응을 받지 않는다."""
    message = store.get_message(user.id, message_id)
    if not message or message["role"] != "assistant":
        raise HTTPException(404, "반응을 남길 수 있는 답을 찾지 못했습니다")
    return message


@app.post("/api/report")
def report(req: ReportRequest, user: User = Depends(current_user)):
    message = my_answer(user, req.message_id)
    log("WARNING", "부적절한 답변 신고", message_id=req.message_id, reason_length=len(req.reason))
    scored = tracing.score(message["meta"].get("trace_id"), "report", 1, comment=req.reason.strip() or None)
    return {"ok": True, "scored": scored}


@app.post("/api/feedback")
def feedback(req: FeedbackRequest, user: User = Depends(current_user)):
    """답 옆의 좋아요, 별로예요. Langfuse 점수 user-feedback(BOOLEAN)으로 보낸다."""
    message = my_answer(user, req.message_id)
    log("INFO", "답 반응", message_id=req.message_id, liked=req.liked)
    scored = tracing.score(message["meta"].get("trace_id"), "user-feedback", 1 if req.liked else 0)
    return {"ok": True, "scored": scored}


# ── 기억, 약속, 내 데이터 ───────────────────────────────────────────

@app.get("/api/memories")
def list_memories(user: User = Depends(current_user)):
    return [{"id": m["id"], "kind": m["kind"], "text": m["text"]} for m in store.list_memories(user.id)]


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int, user: User = Depends(current_user)):
    store.delete_memory(user.id, memory_id)
    return {"ok": True}


@app.delete("/api/memories")
def clear_memories(user: User = Depends(current_user)):
    store.clear_memories(user.id)
    return {"ok": True}


@app.get("/api/reminders")
def list_reminders(user: User = Depends(current_user)):
    tz = when.now_in(store.get_settings(user.id)["timezone"]).tzinfo
    return [{"id": r["id"], "text": r["text"], "status": r["status"],
             "due_label": when.label(r["due_at"].astimezone(tz))} for r in store.list_reminders(user.id)]


@app.delete("/api/reminders/{reminder_id}")
def cancel_reminder(reminder_id: int, user: User = Depends(current_user)):
    store.cancel_reminder(user.id, reminder_id)
    return {"ok": True}


@app.delete("/api/data")
def delete_data(user: User = Depends(current_user)):
    store.delete_all(user.id)
    log("INFO", "사용자 데이터 전체 삭제")
    return {"ok": True}
