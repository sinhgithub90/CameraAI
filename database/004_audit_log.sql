-- MultiCAMAI Audit System MVP
-- Chay sau cac migration hien co.

set search_path to multicamai, public;

create table if not exists audit_log (
  id bigserial primary key,
  actor_user_id bigint references nguoi_dung(id),
  actor_username varchar(120),
  action varchar(120) not null,
  entity_type varchar(120) not null,
  entity_id text,
  before_data jsonb not null default '{}'::jsonb,
  after_data jsonb not null default '{}'::jsonb,
  ip inet,
  user_agent text,
  created_at timestamptz not null default now()
);

create index if not exists idx_audit_log_created_at
  on audit_log(created_at desc);

create index if not exists idx_audit_log_actor
  on audit_log(actor_user_id, created_at desc);

create index if not exists idx_audit_log_actor_username
  on audit_log(actor_username, created_at desc);

create index if not exists idx_audit_log_action
  on audit_log(action, created_at desc);

create index if not exists idx_audit_log_entity
  on audit_log(entity_type, entity_id, created_at desc);
