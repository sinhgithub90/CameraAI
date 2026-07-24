-- MultiCAMAI PostgreSQL schema
-- Muc tieu:
-- 1. Thay the users.json, cameras.json, recordings.db bang PostgreSQL.
-- 2. Luu metadata video/anh/bang chung trong DB, file thuc te van nam tren disk/NAS/object storage.
-- 3. Toi uu truy van camera, playback, canh bao va audit.

create extension if not exists pgcrypto;

create schema if not exists multicamai;
set search_path to multicamai, public;

-- =========================================================
-- Common helpers
-- =========================================================

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- =========================================================
-- 01. User, role, permission
-- =========================================================

create table if not exists vai_tro (
  id bigserial primary key,
  ma_vai_tro varchar(80) not null unique,
  ten_vai_tro varchar(160) not null,
  mo_ta text,
  la_he_thong boolean not null default false,
  trang_thai varchar(30) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists quyen (
  id bigserial primary key,
  ma_quyen varchar(120) not null unique,
  ten_quyen varchar(160) not null,
  nhom_chuc_nang varchar(80) not null,
  hanh_dong varchar(80) not null,
  trang_thai varchar(30) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists vai_tro_quyen (
  id bigserial primary key,
  vai_tro_id bigint not null references vai_tro(id),
  quyen_id bigint not null references quyen(id),
  duoc_phep boolean not null default true,
  ghi_chu text,
  unique (vai_tro_id, quyen_id)
);

create table if not exists nguoi_dung (
  id bigserial primary key,
  ma_nguoi_dung varchar(80) not null unique,
  ten_dang_nhap varchar(120) not null unique,
  ho_ten varchar(180) not null,
  email varchar(180) unique,
  so_dien_thoai varchar(40),
  trang_thai varchar(30) not null default 'ACTIVE',
  lan_dang_nhap_cuoi timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists ho_so_nguoi_dung (
  id bigserial primary key,
  nguoi_dung_id bigint not null unique references nguoi_dung(id),
  ma_nhan_vien varchar(80),
  anh_dai_dien text,
  ngay_sinh date,
  gioi_tinh varchar(20),
  dia_chi text,
  chuc_vu varchar(120),
  phong_ban varchar(120),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists xac_thuc_nguoi_dung (
  id bigserial primary key,
  nguoi_dung_id bigint not null unique references nguoi_dung(id),
  password_hash text not null,
  so_lan_dang_nhap_sai int not null default 0,
  khoa_den timestamptz,
  mfa_enabled boolean not null default false,
  lan_doi_mat_khau_cuoi timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists vai_tro_nguoi_dung (
  id bigserial primary key,
  nguoi_dung_id bigint not null references nguoi_dung(id),
  vai_tro_id bigint not null references vai_tro(id),
  ngay_bat_dau timestamptz not null default now(),
  ngay_ket_thuc timestamptz,
  dang_hoat_dong boolean not null default true,
  unique (nguoi_dung_id, vai_tro_id, ngay_bat_dau)
);

create table if not exists phien_dang_nhap (
  id bigserial primary key,
  nguoi_dung_id bigint references nguoi_dung(id),
  refresh_token_hash text,
  thiet_bi text,
  dia_chi_ip inet,
  bat_dau_luc timestamptz not null default now(),
  het_han_luc timestamptz,
  dang_hoat_dong boolean not null default true
);

create table if not exists lich_su_dang_nhap (
  id bigserial primary key,
  nguoi_dung_id bigint references nguoi_dung(id),
  ten_dang_nhap_nhap_vao varchar(120),
  trang_thai varchar(40) not null,
  ly_do_that_bai text,
  dia_chi_ip inet,
  thoi_diem timestamptz not null default now()
);

create table if not exists lich_su_mat_khau_nguoi_dung (
  id bigserial primary key,
  nguoi_dung_id bigint not null references nguoi_dung(id),
  password_hash text not null,
  ly_do_thay_doi text,
  thoi_diem_thay_doi timestamptz not null default now()
);

create table if not exists nhat_ky_he_thong (
  id bigserial primary key,
  nguoi_dung_id bigint references nguoi_dung(id),
  hanh_dong varchar(120) not null,
  ten_bang varchar(120),
  ban_ghi_id text,
  gia_tri_cu jsonb,
  gia_tri_moi jsonb,
  dia_chi_ip inet,
  thoi_diem timestamptz not null default now()
);

-- =========================================================
-- 02. Organization and area
-- =========================================================

create table if not exists cong_ty (
  id bigserial primary key,
  ma_cong_ty varchar(80) not null unique,
  ten_cong_ty varchar(220) not null,
  ten_viet_tat varchar(80),
  ma_so_thue varchar(80),
  email varchar(180),
  so_dien_thoai varchar(40),
  dia_chi text,
  trang_thai varchar(30) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists loai_khu_vuc (
  id bigserial primary key,
  ma_loai_khu_vuc varchar(80) not null unique,
  ten_loai_khu_vuc varchar(160) not null,
  mo_ta text,
  trang_thai varchar(30) not null default 'ACTIVE'
);

create table if not exists khu_vuc (
  id bigserial primary key,
  cong_ty_id bigint not null references cong_ty(id),
  loai_khu_vuc_id bigint references loai_khu_vuc(id),
  khu_vuc_cha_id bigint references khu_vuc(id),
  ma_khu_vuc varchar(80) not null unique,
  ten_khu_vuc varchar(180) not null,
  mo_ta text,
  trang_thai varchar(30) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists lich_su_khu_vuc (
  id bigserial primary key,
  khu_vuc_id bigint not null references khu_vuc(id),
  hanh_dong varchar(120) not null,
  gia_tri_cu jsonb,
  gia_tri_moi jsonb,
  nguoi_thuc_hien_id bigint references nguoi_dung(id),
  thoi_diem timestamptz not null default now()
);

-- =========================================================
-- 03. Camera management
-- =========================================================

create table if not exists camera (
  id bigserial primary key,
  khu_vuc_id bigint references khu_vuc(id),
  ma_camera varchar(120) not null unique,
  ten_camera varchar(220) not null,
  hang_san_xuat varchar(120),
  model varchar(120),
  serial_number varchar(160),
  loai_nguon varchar(40) not null default 'RTSP',
  stream_key varchar(120) not null unique,
  anh_dai_dien text,
  thu_tu_hien_thi int not null default 0,
  trang_thai_hien_tai varchar(40) not null default 'UNKNOWN',
  bat_ai boolean not null default false,
  bat_ghi_hinh boolean not null default true,
  created_by bigint references nguoi_dung(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists ket_noi_camera (
  id bigserial primary key,
  camera_id bigint not null unique references camera(id),
  dia_chi_ip inet,
  cong_rtsp int,
  duong_dan_rtsp text not null,
  ten_dang_nhap varchar(160),
  mat_khau_ma_hoa text,
  cong_onvif int,
  rtsp_transport varchar(20) not null default 'tcp',
  go2rtc_stream_name varchar(120),
  lan_kiem_tra_cuoi timestamptz,
  trang_thai_ket_noi varchar(40) not null default 'UNKNOWN',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists thong_so_ky_thuat_camera (
  id bigserial primary key,
  camera_id bigint not null unique references camera(id),
  do_phan_giai varchar(40),
  fps int,
  bitrate_kbps int,
  codec varchar(40),
  ho_tro_ptz boolean not null default false,
  ho_tro_am_thanh boolean not null default false,
  tam_nhin_dem boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists lap_dat_camera (
  id bigserial primary key,
  camera_id bigint not null unique references camera(id),
  khu_vuc_id bigint references khu_vuc(id),
  vi_tri_lap_dat varchar(220),
  toa_nha varchar(120),
  tang varchar(80),
  vi_do numeric(10,7),
  kinh_do numeric(10,7),
  huong_nhin varchar(120),
  ngay_lap_dat date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists lich_su_trang_thai_camera (
  id bigserial primary key,
  camera_id bigint not null references camera(id),
  trang_thai varchar(40) not null,
  bat_dau_luc timestamptz not null,
  ket_thuc_luc timestamptz,
  thoi_luong_giay int,
  ly_do text
);

create table if not exists tinh_trang_camera (
  id bigserial primary key,
  camera_id bigint not null references camera(id),
  fps_hien_tai numeric(8,2),
  do_tre_mang_ms int,
  mat_goi_tin numeric(8,4),
  nhiet_do numeric(8,2),
  online boolean not null,
  ghi_nhan_luc timestamptz not null default now()
);

create table if not exists nhat_ky_camera (
  id bigserial primary key,
  camera_id bigint references camera(id),
  hanh_dong varchar(120) not null,
  noi_dung text,
  muc_do varchar(40) default 'INFO',
  thoi_diem timestamptz not null default now()
);

create table if not exists bao_tri_camera (
  id bigserial primary key,
  camera_id bigint not null references camera(id),
  loai_bao_tri varchar(80) not null,
  noi_dung text,
  ngay_bat_dau timestamptz not null,
  ngay_ket_thuc timestamptz,
  nguoi_thuc_hien_id bigint references nguoi_dung(id),
  trang_thai varchar(40) not null default 'PLANNED',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists nhom_camera (
  id bigserial primary key,
  cong_ty_id bigint references cong_ty(id),
  ten_nhom varchar(180) not null,
  mo_ta text,
  trang_thai varchar(40) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz,
  unique (cong_ty_id, ten_nhom)
);

create table if not exists thanh_vien_nhom_camera (
  id bigserial primary key,
  nhom_camera_id bigint not null references nhom_camera(id),
  camera_id bigint not null references camera(id),
  ghi_chu text,
  created_at timestamptz not null default now(),
  unique (nhom_camera_id, camera_id)
);

-- =========================================================
-- 04. Recording and playback
-- =========================================================

create table if not exists doan_video (
  id bigserial primary key,
  camera_id bigint not null references camera(id),
  bang_chung_id bigint,
  loai_doan varchar(40) not null default 'RAW_RECORDING',
  duong_dan_video text not null unique,
  bat_dau_luc timestamptz not null,
  ket_thuc_luc timestamptz not null,
  thoi_luong_giay int not null,
  dung_luong bigint not null default 0,
  mime_type varchar(120) not null default 'video/mp4',
  codec varchar(40),
  do_phan_giai varchar(40),
  fps numeric(8,2),
  checksum_sha256 varchar(80),
  trang_thai varchar(40) not null default 'READY',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists anh_chup (
  id bigserial primary key,
  camera_id bigint not null references camera(id),
  bang_chung_id bigint,
  duong_dan_anh text not null unique,
  thoi_diem_chup timestamptz not null,
  dung_luong bigint not null default 0,
  mime_type varchar(120) not null default 'image/jpeg',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  deleted_at timestamptz
);

-- =========================================================
-- 05. AI, event, alert, evidence
-- =========================================================

create table if not exists mo_hinh_ai (
  id bigserial primary key,
  ma_mo_hinh varchar(120) not null unique,
  ten_mo_hinh varchar(220) not null,
  loai_mo_hinh varchar(80),
  mo_ta text,
  trang_thai varchar(40) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists phien_ban_mo_hinh_ai (
  id bigserial primary key,
  mo_hinh_ai_id bigint not null references mo_hinh_ai(id),
  so_phien_ban varchar(80) not null,
  framework varchar(80),
  duong_dan_trong_so text,
  do_chinh_xac numeric(8,4),
  trang_thai varchar(40) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  unique (mo_hinh_ai_id, so_phien_ban)
);

create table if not exists loai_su_kien (
  id bigserial primary key,
  ma_loai_su_kien varchar(120) not null unique,
  ten_loai_su_kien varchar(220) not null,
  mo_ta text,
  muc_do_mac_dinh varchar(40) not null default 'MEDIUM',
  trang_thai varchar(40) not null default 'ACTIVE'
);

create table if not exists loai_doi_tuong (
  id bigserial primary key,
  ma_doi_tuong varchar(120) not null unique,
  ten_doi_tuong varchar(220) not null,
  mo_ta text,
  trang_thai varchar(40) not null default 'ACTIVE'
);

create table if not exists luat_ai (
  id bigserial primary key,
  camera_id bigint not null references camera(id),
  loai_su_kien_id bigint not null references loai_su_kien(id),
  phien_ban_mo_hinh_ai_id bigint references phien_ban_mo_hinh_ai(id),
  ten_luat varchar(220) not null,
  nguong_tin_cay numeric(8,4) not null default 0.7,
  muc_do varchar(40) not null default 'MEDIUM',
  bat boolean not null default true,
  created_by bigint references nguoi_dung(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (camera_id, loai_su_kien_id, ten_luat)
);

create table if not exists vung_giam_sat (
  id bigserial primary key,
  luat_ai_id bigint not null references luat_ai(id),
  ten_vung varchar(180) not null,
  toa_do_polygon jsonb not null,
  do_uu_tien int not null default 0,
  bat boolean not null default true
);

create table if not exists lich_hoat_dong_luat_ai (
  id bigserial primary key,
  luat_ai_id bigint not null references luat_ai(id),
  gio_bat_dau time not null,
  gio_ket_thuc time not null,
  cac_ngay_trong_tuan int[] not null default array[1,2,3,4,5,6,7],
  bat boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists lich_su_luat_ai (
  id bigserial primary key,
  luat_ai_id bigint not null references luat_ai(id),
  hanh_dong varchar(120) not null,
  gia_tri_cu jsonb,
  gia_tri_moi jsonb,
  nguoi_thuc_hien_id bigint references nguoi_dung(id),
  thoi_diem timestamptz not null default now()
);

create table if not exists khung_hinh_phan_tich (
  id bigserial primary key,
  camera_id bigint not null references camera(id),
  duong_dan_anh text,
  thoi_diem_chup timestamptz not null,
  chieu_rong int,
  chieu_cao int
);

create table if not exists ket_qua_suy_luan_ai (
  id bigserial primary key,
  khung_hinh_phan_tich_id bigint references khung_hinh_phan_tich(id),
  phien_ban_mo_hinh_ai_id bigint references phien_ban_mo_hinh_ai(id),
  thoi_gian_xu_ly_ms int,
  ket_qua_raw jsonb not null default '{}'::jsonb,
  xu_ly_luc timestamptz not null default now()
);

create table if not exists su_kien_phat_hien (
  id bigserial primary key,
  camera_id bigint not null references camera(id),
  luat_ai_id bigint references luat_ai(id),
  loai_su_kien_id bigint not null references loai_su_kien(id),
  ket_qua_suy_luan_ai_id bigint references ket_qua_suy_luan_ai(id),
  do_tin_cay numeric(8,4),
  mo_ta text,
  phat_hien_luc timestamptz not null
);

create table if not exists doi_tuong_phat_hien (
  id bigserial primary key,
  su_kien_phat_hien_id bigint not null references su_kien_phat_hien(id),
  loai_doi_tuong_id bigint references loai_doi_tuong(id),
  nhan varchar(160),
  do_tin_cay numeric(8,4),
  bounding_box jsonb
);

create table if not exists canh_bao (
  id bigserial primary key,
  su_kien_phat_hien_id bigint references su_kien_phat_hien(id),
  camera_id bigint not null references camera(id),
  khu_vuc_id bigint references khu_vuc(id),
  luat_ai_id bigint references luat_ai(id),
  muc_do varchar(40) not null default 'MEDIUM',
  trang_thai_hien_tai varchar(40) not null default 'NEW',
  mo_ta text,
  phat_sinh_luc timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists lich_su_trang_thai_canh_bao (
  id bigserial primary key,
  canh_bao_id bigint not null references canh_bao(id),
  trang_thai varchar(40) not null,
  bat_dau_luc timestamptz not null default now(),
  ket_thuc_luc timestamptz,
  nguoi_thuc_hien_id bigint references nguoi_dung(id),
  ghi_chu text
);

create table if not exists tien_trinh_xu_ly_canh_bao (
  id bigserial primary key,
  canh_bao_id bigint not null references canh_bao(id),
  hanh_dong varchar(120) not null,
  noi_dung text,
  nguoi_thuc_hien_id bigint references nguoi_dung(id),
  thoi_diem timestamptz not null default now()
);

create table if not exists phan_cong_xu_ly_canh_bao (
  id bigserial primary key,
  canh_bao_id bigint not null references canh_bao(id),
  nguoi_duoc_giao_id bigint not null references nguoi_dung(id),
  nguoi_giao_id bigint references nguoi_dung(id),
  han_xu_ly timestamptz,
  trang_thai varchar(40) not null default 'ASSIGNED',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists binh_luan_canh_bao (
  id bigserial primary key,
  canh_bao_id bigint not null references canh_bao(id),
  nguoi_dung_id bigint references nguoi_dung(id),
  noi_dung text not null,
  thoi_diem timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists bang_chung (
  id bigserial primary key,
  canh_bao_id bigint references canh_bao(id),
  su_kien_phat_hien_id bigint references su_kien_phat_hien(id),
  tieu_de varchar(220),
  mo_ta text,
  thoi_diem_ghi_nhan timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'fk_doan_video_bang_chung'
  ) then
    alter table doan_video
      add constraint fk_doan_video_bang_chung
      foreign key (bang_chung_id) references bang_chung(id);
  end if;

  if not exists (
    select 1 from pg_constraint where conname = 'fk_anh_chup_bang_chung'
  ) then
    alter table anh_chup
      add constraint fk_anh_chup_bang_chung
      foreign key (bang_chung_id) references bang_chung(id);
  end if;
end;
$$;

create table if not exists tep_bang_chung (
  id bigserial primary key,
  bang_chung_id bigint not null references bang_chung(id),
  loai_tep varchar(40) not null,
  ten_tep varchar(260) not null,
  duong_dan text not null,
  dung_luong bigint not null default 0,
  mime_type varchar(120),
  checksum_sha256 varchar(80),
  created_at timestamptz not null default now()
);

-- =========================================================
-- 06. Notification, report and dashboard
-- =========================================================

create table if not exists mau_thong_bao (
  id bigserial primary key,
  ma_mau varchar(120) not null unique,
  ten_mau varchar(220) not null,
  kenh varchar(40) not null,
  tieu_de_mau text,
  noi_dung_mau text not null,
  trang_thai varchar(40) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists thong_bao (
  id bigserial primary key,
  canh_bao_id bigint references canh_bao(id),
  mau_thong_bao_id bigint references mau_thong_bao(id),
  tieu_de text,
  noi_dung text not null,
  kenh varchar(40) not null,
  trang_thai varchar(40) not null default 'PENDING',
  tao_luc timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists nguoi_nhan_thong_bao (
  id bigserial primary key,
  thong_bao_id bigint not null references thong_bao(id),
  nguoi_dung_id bigint references nguoi_dung(id),
  dia_chi_nhan text,
  trang_thai_nhan varchar(40) not null default 'PENDING',
  da_doc boolean not null default false,
  doc_luc timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists nhat_ky_thong_bao (
  id bigserial primary key,
  thong_bao_id bigint not null references thong_bao(id),
  kenh varchar(40) not null,
  trang_thai_gui varchar(40) not null,
  thong_bao_loi text,
  gui_luc timestamptz not null default now()
);

create table if not exists bao_cao_da_luu (
  id bigserial primary key,
  nguoi_dung_id bigint references nguoi_dung(id),
  ten_bao_cao varchar(220) not null,
  loai_bao_cao varchar(80) not null,
  bo_loc jsonb not null default '{}'::jsonb,
  cau_hinh jsonb not null default '{}'::jsonb,
  trang_thai varchar(40) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists thanh_phan_dashboard (
  id bigserial primary key,
  ma_thanh_phan varchar(120) not null unique,
  ten_thanh_phan varchar(220) not null,
  loai_bieu_do varchar(80) not null,
  cau_hinh_mac_dinh jsonb not null default '{}'::jsonb,
  trang_thai varchar(40) not null default 'ACTIVE',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists bo_cuc_dashboard (
  id bigserial primary key,
  nguoi_dung_id bigint references nguoi_dung(id),
  ten_bo_cuc varchar(220) not null,
  cau_hinh_bo_cuc jsonb not null default '{}'::jsonb,
  la_mac_dinh boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

-- =========================================================
-- 07. Indexes for product queries
-- =========================================================

create index if not exists idx_nguoi_dung_trang_thai on nguoi_dung(trang_thai) where deleted_at is null;
create index if not exists idx_lich_su_dang_nhap_time on lich_su_dang_nhap(thoi_diem desc);
create index if not exists idx_nhat_ky_he_thong_time on nhat_ky_he_thong(thoi_diem desc);
create index if not exists idx_nhat_ky_he_thong_table_record on nhat_ky_he_thong(ten_bang, ban_ghi_id);

create index if not exists idx_khu_vuc_cong_ty on khu_vuc(cong_ty_id) where deleted_at is null;
create index if not exists idx_khu_vuc_parent on khu_vuc(khu_vuc_cha_id) where deleted_at is null;
create index if not exists idx_lich_su_khu_vuc_time on lich_su_khu_vuc(khu_vuc_id, thoi_diem desc);

create index if not exists idx_camera_khu_vuc on camera(khu_vuc_id) where deleted_at is null;
create index if not exists idx_camera_stream_key on camera(stream_key);
create index if not exists idx_camera_status on camera(trang_thai_hien_tai) where deleted_at is null;

create index if not exists idx_tinh_trang_camera_time on tinh_trang_camera(camera_id, ghi_nhan_luc desc);
create index if not exists idx_lich_su_camera_time on lich_su_trang_thai_camera(camera_id, bat_dau_luc desc);
create index if not exists idx_bao_tri_camera_time on bao_tri_camera(camera_id, ngay_bat_dau desc);
create index if not exists idx_nhat_ky_camera_time on nhat_ky_camera(camera_id, thoi_diem desc);
create index if not exists idx_thanh_vien_nhom_camera_camera on thanh_vien_nhom_camera(camera_id);

create index if not exists idx_doan_video_camera_time on doan_video(camera_id, bat_dau_luc, ket_thuc_luc) where deleted_at is null;
create index if not exists idx_doan_video_time on doan_video(bat_dau_luc, ket_thuc_luc) where deleted_at is null;
create index if not exists idx_doan_video_bang_chung on doan_video(bang_chung_id) where bang_chung_id is not null;

create index if not exists idx_anh_chup_camera_time on anh_chup(camera_id, thoi_diem_chup desc) where deleted_at is null;
create index if not exists idx_luat_ai_camera on luat_ai(camera_id, bat);
create index if not exists idx_lich_luat_ai_luat on lich_hoat_dong_luat_ai(luat_ai_id, bat);
create index if not exists idx_khung_hinh_camera_time on khung_hinh_phan_tich(camera_id, thoi_diem_chup desc);
create index if not exists idx_su_kien_camera_time on su_kien_phat_hien(camera_id, phat_hien_luc desc);
create index if not exists idx_canh_bao_camera_time on canh_bao(camera_id, phat_sinh_luc desc) where deleted_at is null;
create index if not exists idx_canh_bao_status on canh_bao(trang_thai_hien_tai, phat_sinh_luc desc) where deleted_at is null;
create index if not exists idx_canh_bao_khu_vuc_time on canh_bao(khu_vuc_id, phat_sinh_luc desc) where deleted_at is null;
create index if not exists idx_phan_cong_canh_bao_user on phan_cong_xu_ly_canh_bao(nguoi_duoc_giao_id, trang_thai);
create index if not exists idx_binh_luan_canh_bao_time on binh_luan_canh_bao(canh_bao_id, thoi_diem desc) where deleted_at is null;
create index if not exists idx_bang_chung_canh_bao on bang_chung(canh_bao_id);
create index if not exists idx_tep_bang_chung on tep_bang_chung(bang_chung_id);

create index if not exists idx_thong_bao_status on thong_bao(trang_thai, tao_luc desc);
create index if not exists idx_nguoi_nhan_thong_bao_user on nguoi_nhan_thong_bao(nguoi_dung_id, da_doc, created_at desc);
create index if not exists idx_nhat_ky_thong_bao_time on nhat_ky_thong_bao(thong_bao_id, gui_luc desc);
create index if not exists idx_bao_cao_user on bao_cao_da_luu(nguoi_dung_id, loai_bao_cao) where deleted_at is null;
create index if not exists idx_dashboard_user on bo_cuc_dashboard(nguoi_dung_id, la_mac_dinh) where deleted_at is null;

-- =========================================================
-- 08. Updated_at triggers
-- =========================================================

drop trigger if exists trg_vai_tro_updated_at on vai_tro;
create trigger trg_vai_tro_updated_at before update on vai_tro for each row execute function set_updated_at();

drop trigger if exists trg_nguoi_dung_updated_at on nguoi_dung;
create trigger trg_nguoi_dung_updated_at before update on nguoi_dung for each row execute function set_updated_at();

drop trigger if exists trg_cong_ty_updated_at on cong_ty;
create trigger trg_cong_ty_updated_at before update on cong_ty for each row execute function set_updated_at();

drop trigger if exists trg_khu_vuc_updated_at on khu_vuc;
create trigger trg_khu_vuc_updated_at before update on khu_vuc for each row execute function set_updated_at();

drop trigger if exists trg_camera_updated_at on camera;
create trigger trg_camera_updated_at before update on camera for each row execute function set_updated_at();

drop trigger if exists trg_ket_noi_camera_updated_at on ket_noi_camera;
create trigger trg_ket_noi_camera_updated_at before update on ket_noi_camera for each row execute function set_updated_at();

drop trigger if exists trg_lap_dat_camera_updated_at on lap_dat_camera;
create trigger trg_lap_dat_camera_updated_at before update on lap_dat_camera for each row execute function set_updated_at();

drop trigger if exists trg_bao_tri_camera_updated_at on bao_tri_camera;
create trigger trg_bao_tri_camera_updated_at before update on bao_tri_camera for each row execute function set_updated_at();

drop trigger if exists trg_nhom_camera_updated_at on nhom_camera;
create trigger trg_nhom_camera_updated_at before update on nhom_camera for each row execute function set_updated_at();

drop trigger if exists trg_doan_video_updated_at on doan_video;
create trigger trg_doan_video_updated_at before update on doan_video for each row execute function set_updated_at();

drop trigger if exists trg_mo_hinh_ai_updated_at on mo_hinh_ai;
create trigger trg_mo_hinh_ai_updated_at before update on mo_hinh_ai for each row execute function set_updated_at();

drop trigger if exists trg_luat_ai_updated_at on luat_ai;
create trigger trg_luat_ai_updated_at before update on luat_ai for each row execute function set_updated_at();

drop trigger if exists trg_canh_bao_updated_at on canh_bao;
create trigger trg_canh_bao_updated_at before update on canh_bao for each row execute function set_updated_at();

drop trigger if exists trg_phan_cong_canh_bao_updated_at on phan_cong_xu_ly_canh_bao;
create trigger trg_phan_cong_canh_bao_updated_at before update on phan_cong_xu_ly_canh_bao for each row execute function set_updated_at();

drop trigger if exists trg_mau_thong_bao_updated_at on mau_thong_bao;
create trigger trg_mau_thong_bao_updated_at before update on mau_thong_bao for each row execute function set_updated_at();

drop trigger if exists trg_thong_bao_updated_at on thong_bao;
create trigger trg_thong_bao_updated_at before update on thong_bao for each row execute function set_updated_at();

drop trigger if exists trg_bao_cao_da_luu_updated_at on bao_cao_da_luu;
create trigger trg_bao_cao_da_luu_updated_at before update on bao_cao_da_luu for each row execute function set_updated_at();

drop trigger if exists trg_thanh_phan_dashboard_updated_at on thanh_phan_dashboard;
create trigger trg_thanh_phan_dashboard_updated_at before update on thanh_phan_dashboard for each row execute function set_updated_at();

drop trigger if exists trg_bo_cuc_dashboard_updated_at on bo_cuc_dashboard;
create trigger trg_bo_cuc_dashboard_updated_at before update on bo_cuc_dashboard for each row execute function set_updated_at();

-- =========================================================
-- 09. Demo seed matching current project
-- Password hash below is placeholder. Replace by bcrypt/argon2 hash from backend.
-- =========================================================
/*
Deprecated inline seed.
Use database/multicamai_demo_seed.sql after this schema file instead.

insert into vai_tro (ma_vai_tro, ten_vai_tro, mo_ta, la_he_thong)
values
  ('ADMIN', 'Quản trị viên', 'Toàn quyền hệ thống', true),
  ('SUPERVISOR', 'Giám sát', 'Giám sát camera và cảnh báo', true),
  ('STAFF', 'Nhân viên', 'Người dùng vận hành', true)
on conflict (ma_vai_tro) do nothing;

insert into quyen (ma_quyen, ten_quyen, nhom_chuc_nang, hanh_dong)
values
  ('live', 'Xem trực tiếp', 'camera', 'read'),
  ('playback', 'Xem phát lại', 'recording', 'read'),
  ('cammgmt', 'Quản lý camera', 'camera', 'write'),
  ('usermgmt', 'Quản lý người dùng', 'user', 'write'),
  ('alertmgmt', 'Quản lý cảnh báo', 'alert', 'write'),
  ('reports', 'Xem báo cáo', 'report', 'read'),
  ('sysconfig', 'Cấu hình hệ thống', 'system', 'write'),
  ('export', 'Xuất dữ liệu', 'data', 'export')
on conflict (ma_quyen) do nothing;

insert into vai_tro_quyen (vai_tro_id, quyen_id)
select vt.id, q.id
from vai_tro vt
cross join quyen q
where vt.ma_vai_tro = 'ADMIN'
on conflict (vai_tro_id, quyen_id) do nothing;

insert into cong_ty (ma_cong_ty, ten_cong_ty, ten_viet_tat)
values ('MULTICAMAI', 'MultiCAMAI Demo', 'MultiCAMAI')
on conflict (ma_cong_ty) do nothing;

insert into loai_khu_vuc (ma_loai_khu_vuc, ten_loai_khu_vuc)
values ('BUILDING', 'Tòa nhà'), ('FLOOR', 'Tầng'), ('ZONE', 'Khu vực')
on conflict (ma_loai_khu_vuc) do nothing;

insert into khu_vuc (cong_ty_id, loai_khu_vuc_id, ma_khu_vuc, ten_khu_vuc)
select ct.id, lkv.id, 'TOA_NHA_A', 'Tòa nhà A'
from cong_ty ct
left join loai_khu_vuc lkv on lkv.ma_loai_khu_vuc = 'BUILDING'
where ct.ma_cong_ty = 'MULTICAMAI'
on conflict (ma_khu_vuc) do nothing;

insert into nguoi_dung (ma_nguoi_dung, ten_dang_nhap, ho_ten, email)
values ('U_ADMIN', 'admin', 'Lê Xuân Tuyên', 'utvanle113@gmail.com')
on conflict (ten_dang_nhap) do nothing;

insert into xac_thuc_nguoi_dung (nguoi_dung_id, password_hash)
select id, 'CHANGE_ME_HASH_ADMIN1'
from nguoi_dung
where ten_dang_nhap = 'admin'
on conflict (nguoi_dung_id) do nothing;

insert into vai_tro_nguoi_dung (nguoi_dung_id, vai_tro_id)
select nd.id, vt.id
from nguoi_dung nd
join vai_tro vt on vt.ma_vai_tro = 'ADMIN'
where nd.ten_dang_nhap = 'admin'
on conflict do nothing;

insert into camera (
  khu_vuc_id, ma_camera, ten_camera, hang_san_xuat, model,
  stream_key, thu_tu_hien_thi, trang_thai_hien_tai, bat_ghi_hinh
)
select kv.id, 'cam_huyen_01', 'Hành lang tầng 2', 'Hikvision', 'DS-2CD2143G2',
       'cam_huyen_01', 1, 'ONLINE', true
from khu_vuc kv
where kv.ma_khu_vuc = 'TOA_NHA_A'
on conflict (ma_camera) do nothing;

insert into ket_noi_camera (
  camera_id, dia_chi_ip, cong_rtsp, duong_dan_rtsp,
  ten_dang_nhap, mat_khau_ma_hoa, go2rtc_stream_name
)
select id, '192.168.1.4', 2004,
       'rtsp://admin:Abc123456@192.168.1.4:2004/Streaming/Channels/102',
       'admin', 'CHANGE_ME_ENCRYPTED', 'cam_huyen_01'
from camera
where ma_camera = 'cam_huyen_01'
on conflict (camera_id) do nothing;

insert into thong_so_ky_thuat_camera (camera_id, do_phan_giai, fps, bitrate_kbps, codec)
select id, '1920x1080', 25, 4096, 'H264'
from camera
where ma_camera = 'cam_huyen_01'
on conflict (camera_id) do nothing;

insert into lap_dat_camera (camera_id, khu_vuc_id, vi_tri_lap_dat, toa_nha, tang)
select c.id, c.khu_vuc_id, 'Hành lang T2', 'Tòa nhà A', 'Tầng 2'
from camera c
where c.ma_camera = 'cam_huyen_01'
on conflict (camera_id) do nothing;
*/
