-- MultiCAMAI system settings migration
-- Stores settings that were previously in VMS-Center/settings.json.

set search_path to multicamai, public;

create table if not exists cau_hinh_he_thong (
  section varchar(80) primary key,
  cau_hinh jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

drop trigger if exists trg_cau_hinh_he_thong_updated_at on cau_hinh_he_thong;
create trigger trg_cau_hinh_he_thong_updated_at
before update on cau_hinh_he_thong
for each row execute function set_updated_at();
