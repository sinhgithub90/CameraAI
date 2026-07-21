set search_path to multicamai, public;

create unique index if not exists idx_thong_bao_alert_channel_unique
  on thong_bao(canh_bao_id, kenh)
  where canh_bao_id is not null;

create unique index if not exists idx_nguoi_nhan_thong_bao_unique
  on nguoi_nhan_thong_bao(thong_bao_id, nguoi_dung_id)
  where nguoi_dung_id is not null;
