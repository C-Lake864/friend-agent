-- 크레딧 원장. 잔액은 따로 저장하지 않고 이 사용자의 delta 를 모두 더해서 구한다.
-- 0001_init.sql 을 실행한 뒤, staging 프로젝트와 production 프로젝트에 각각 한 번씩 실행한다.
--
-- RLS를 켜고 정책은 만들지 않는다. 서버(secret key)만 읽고 쓴다.
-- 잔액을 보여 주는 뷰나 함수도 만들지 않는다. public 스키마의 뷰와 함수는 브라우저 키로도 부를 수 있어서다.
-- 같은 일이 두 번 기록되지 않게 ref 를 유일하게 둔다(예: signup:<user_id>, chat:<답 메시지 id>).
-- "내 데이터 전체 삭제"에서는 지우지 않는다. 지우면 무료 크레딧을 다시 받을 수 있다(docs/decisions.md).

create table if not exists public.credit_ledger (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  delta integer not null,                    -- 더하면 양수, 빼면 음수
  reason text not null,                      -- signup, chat 등
  ref text not null unique,
  created_at timestamptz not null default now()
);
create index if not exists credit_ledger_user_id_idx on public.credit_ledger (user_id, id);

alter table public.credit_ledger enable row level security;
