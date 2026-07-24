-- Alert Engine fields
-- Adds source/title/dedup metadata needed by centralized alert creation.

set search_path to multicamai, public;

alter table canh_bao
  add column if not exists nguon varchar(40) not null default 'SYSTEM',
  add column if not exists tieu_de varchar(220),
  add column if not exists duplicate_key varchar(260);

drop index if exists idx_canh_bao_open_duplicate;

create unique index if not exists idx_canh_bao_open_duplicate
  on canh_bao(duplicate_key)
  where deleted_at is null
    and duplicate_key is not null
    and trang_thai_hien_tai in ('NEW', 'PROCESSING', 'ACKNOWLEDGED');
