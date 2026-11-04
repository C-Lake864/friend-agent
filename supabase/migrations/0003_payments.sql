-- 크레딧 결제 주문(토스페이먼츠 주문서형 결제).
-- 0002_credits.sql 을 실행한 뒤, staging 프로젝트와 production 프로젝트에 각각 한 번씩 실행한다.
--
-- 결제를 요청하기 전에 주문번호와 금액을 여기에 저장하고, 승인하기 전에 돌아온 금액과 비교한다.
-- 근거: https://docs.tosspayments.com/guides/v2/get-started/payment-flow (결제 정보 검증하기)
-- RLS를 켜고 정책은 만들지 않는다. 서버(secret key)만 읽고 쓴다.
-- "내 데이터 전체 삭제"에서는 지우지 않는다. 결제 기록이다(docs/decisions.md).

create table if not exists public.payment_orders (
  order_id text primary key check (order_id ~ '^[A-Za-z0-9_=-]{6,64}$'),   -- 토스 orderId 규칙
  user_id uuid not null references auth.users (id) on delete cascade,
  customer_key text not null,                -- 회원마다 무작위 UUID 하나. 토스 customerKey
  product text not null,
  credits integer not null check (credits > 0),
  amount integer not null check (amount > 0),
  status text not null default 'ready' check (status in ('ready', 'paid', 'failed')),
  payment_key text,                          -- 토스가 준 값 그대로(최대 200자)
  fail_code text,
  created_at timestamptz not null default now(),
  paid_at timestamptz
);
create index if not exists payment_orders_user_id_idx on public.payment_orders (user_id, created_at);

alter table public.payment_orders enable row level security;
