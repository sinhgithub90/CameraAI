-- MultiCAMAI demo seed
-- Chay sau multicamai_postgresql_schema.sql

set search_path to multicamai, public;

insert into vai_tro (ma_vai_tro, ten_vai_tro, mo_ta, la_he_thong)
values
  ('ADMIN', 'Quan tri vien', 'Toan quyen he thong', true),
  ('SUPERVISOR', 'Giam sat', 'Giam sat camera va canh bao', true),
  ('STAFF', 'Nhan vien', 'Nguoi dung van hanh', true)
on conflict (ma_vai_tro) do nothing;

insert into quyen (ma_quyen, ten_quyen, nhom_chuc_nang, hanh_dong)
values
  ('live', 'Xem truc tiep', 'camera', 'read'),
  ('playback', 'Xem phat lai', 'recording', 'read'),
  ('cammgmt', 'Quan ly camera', 'camera', 'write'),
  ('usermgmt', 'Quan ly nguoi dung', 'user', 'write'),
  ('alertmgmt', 'Quan ly canh bao', 'alert', 'write'),
  ('reports', 'Xem bao cao', 'report', 'read'),
  ('sysconfig', 'Cau hinh he thong', 'system', 'write'),
  ('export', 'Xuat du lieu', 'data', 'export')
on conflict (ma_quyen) do nothing;

insert into vai_tro_quyen (vai_tro_id, quyen_id)
select vt.id, q.id
from vai_tro vt
cross join quyen q
where vt.ma_vai_tro = 'ADMIN'
on conflict (vai_tro_id, quyen_id) do nothing;

insert into vai_tro_quyen (vai_tro_id, quyen_id)
select vt.id, q.id
from vai_tro vt
join quyen q on q.ma_quyen in ('live', 'playback', 'cammgmt', 'alertmgmt', 'reports', 'sysconfig', 'export')
where vt.ma_vai_tro = 'SUPERVISOR'
on conflict (vai_tro_id, quyen_id) do nothing;

insert into vai_tro_quyen (vai_tro_id, quyen_id)
select vt.id, q.id
from vai_tro vt
join quyen q on q.ma_quyen in ('live', 'playback')
where vt.ma_vai_tro = 'STAFF'
on conflict (vai_tro_id, quyen_id) do nothing;

insert into cong_ty (ma_cong_ty, ten_cong_ty, ten_viet_tat)
values ('MULTICAMAI', 'MultiCAMAI Demo', 'MultiCAMAI')
on conflict (ma_cong_ty) do nothing;

insert into loai_khu_vuc (ma_loai_khu_vuc, ten_loai_khu_vuc)
values ('BUILDING', 'Toa nha'), ('FLOOR', 'Tang'), ('ZONE', 'Khu vuc')
on conflict (ma_loai_khu_vuc) do nothing;

insert into khu_vuc (cong_ty_id, loai_khu_vuc_id, ma_khu_vuc, ten_khu_vuc)
select ct.id, lkv.id, data.ma_khu_vuc, data.ten_khu_vuc
from (
  values
    ('TOA_NHA_A', 'Toa nha A', 'BUILDING'),
    ('KHU_NGOAI_VI', 'Khu ngoai vi', 'ZONE'),
    ('THE_GIOI_XANH', 'The gioi xanh', 'ZONE')
) as data(ma_khu_vuc, ten_khu_vuc, ma_loai_khu_vuc)
join cong_ty ct on ct.ma_cong_ty = 'MULTICAMAI'
left join loai_khu_vuc lkv on lkv.ma_loai_khu_vuc = data.ma_loai_khu_vuc
on conflict (ma_khu_vuc) do nothing;

insert into nguoi_dung (ma_nguoi_dung, ten_dang_nhap, ho_ten, email, so_dien_thoai)
values
  ('U_ADMIN', 'admin', 'Le Xuan Tuyen', 'utvanle113@gmail.com', '0909 123 456'),
  ('U_TANLE', 'tanle', 'Le Van Tan', 'tanle@gmail.com', '0999999992'),
  ('U_STAFF_01', 'trinhlo1', 'Nguyen Lam Trinh', 'nltrinh123@gmail.com', '0999999991')
on conflict (ten_dang_nhap) do nothing;

insert into xac_thuc_nguoi_dung (nguoi_dung_id, password_hash)
select id,
       case ten_dang_nhap
         when 'admin' then 'CHANGE_ME_HASH_ADMIN1'
         when 'tanle' then 'CHANGE_ME_HASH_123123'
         else 'CHANGE_ME_HASH_123456ABC'
       end
from nguoi_dung
where ten_dang_nhap in ('admin', 'tanle', 'trinhlo1')
on conflict (nguoi_dung_id) do nothing;

insert into vai_tro_nguoi_dung (nguoi_dung_id, vai_tro_id)
select nd.id, vt.id
from nguoi_dung nd
join vai_tro vt on vt.ma_vai_tro = 'ADMIN'
where nd.ten_dang_nhap = 'admin'
  and not exists (
    select 1 from vai_tro_nguoi_dung x
    where x.nguoi_dung_id = nd.id and x.vai_tro_id = vt.id and x.dang_hoat_dong = true
  );

insert into vai_tro_nguoi_dung (nguoi_dung_id, vai_tro_id)
select nd.id, vt.id
from nguoi_dung nd
join vai_tro vt on vt.ma_vai_tro = 'SUPERVISOR'
where nd.ten_dang_nhap = 'tanle'
  and not exists (
    select 1 from vai_tro_nguoi_dung x
    where x.nguoi_dung_id = nd.id and x.vai_tro_id = vt.id and x.dang_hoat_dong = true
  );

insert into vai_tro_nguoi_dung (nguoi_dung_id, vai_tro_id)
select nd.id, vt.id
from nguoi_dung nd
join vai_tro vt on vt.ma_vai_tro = 'STAFF'
where nd.ten_dang_nhap = 'trinhlo1'
  and not exists (
    select 1 from vai_tro_nguoi_dung x
    where x.nguoi_dung_id = nd.id and x.vai_tro_id = vt.id and x.dang_hoat_dong = true
  );

insert into camera (
  khu_vuc_id, ma_camera, ten_camera, hang_san_xuat, model,
  stream_key, thu_tu_hien_thi, trang_thai_hien_tai, bat_ghi_hinh
)
select kv.id, data.ma_camera, data.ten_camera, data.hang_san_xuat, data.model,
       data.stream_key, data.thu_tu_hien_thi, data.trang_thai_hien_tai, true
from (
  values
    ('TOA_NHA_A', 'cam_huyen_01', 'Hanh lang tang 2', 'Hikvision', 'DS-2CD2143G2', 'cam_huyen_01', 1, 'ONLINE'),
    ('KHU_NGOAI_VI', 'cam_huyen_02', 'Cong chinh co quan', 'Hikvision', 'DS-2CD1123G0', 'cam_huyen_02', 2, 'ONLINE'),
    ('THE_GIOI_XANH', 'cam_huyen_03', 'Tang 3', 'HikVision', 'HikVision', 'cam_huyen_03', 3, 'OFFLINE'),
    ('THE_GIOI_XANH', 'cam_huyen_04', 'Tang 2', 'HikVision', 'HikVision', 'cam_huyen_04', 4, 'OFFLINE')
) as data(ma_khu_vuc, ma_camera, ten_camera, hang_san_xuat, model, stream_key, thu_tu_hien_thi, trang_thai_hien_tai)
join khu_vuc kv on kv.ma_khu_vuc = data.ma_khu_vuc
on conflict (ma_camera) do nothing;

insert into ket_noi_camera (
  camera_id, dia_chi_ip, cong_rtsp, duong_dan_rtsp,
  ten_dang_nhap, mat_khau_ma_hoa, go2rtc_stream_name
)
select c.id, data.dia_chi_ip::inet, data.cong_rtsp, data.duong_dan_rtsp,
       data.ten_dang_nhap, 'CHANGE_ME_ENCRYPTED', data.go2rtc_stream_name
from (
  values
    ('cam_huyen_01', '192.168.1.4', 2004, 'rtsp://admin:Abc123456@192.168.1.4:2004/Streaming/Channels/102', 'admin', 'cam_huyen_01'),
    ('cam_huyen_02', '192.168.1.5', 2005, 'rtsp://admin:Abc123456@192.168.1.5:2005/Streaming/Channels/102', 'admin', 'cam_huyen_02'),
    ('cam_huyen_03', '10.10.10.15', 2004, 'rtsp://admin:45646@10.10.10.15:2004/Streaming/Channels/102', 'admin', 'cam_huyen_03'),
    ('cam_huyen_04', '10.10.10.16', 2004, 'rtsp://admin:1we2@10.10.10.16:2004/Streaming/Channels/102', 'admin', 'cam_huyen_04')
) as data(ma_camera, dia_chi_ip, cong_rtsp, duong_dan_rtsp, ten_dang_nhap, go2rtc_stream_name)
join camera c on c.ma_camera = data.ma_camera
on conflict (camera_id) do nothing;

insert into thong_so_ky_thuat_camera (camera_id, do_phan_giai, fps, bitrate_kbps, codec)
select id, '1920x1080', 25, 4096, 'H264'
from camera
on conflict (camera_id) do nothing;

insert into lap_dat_camera (camera_id, khu_vuc_id, vi_tri_lap_dat, toa_nha, tang, vi_do, kinh_do)
select c.id, c.khu_vuc_id, data.vi_tri_lap_dat, data.toa_nha, data.tang,
       data.vi_do::numeric, data.kinh_do::numeric
from (
  values
    ('cam_huyen_01', 'Hanh lang T2', 'Toa nha A', 'Tang 2', 13.7614262, 109.2076460),
    ('cam_huyen_02', 'Cong kiem soat', 'Khu ngoai vi', null, 13.7614614, 109.2076810),
    ('cam_huyen_03', 'Tang 3 huong xuong cong vien', 'The gioi xanh', 'Tang 3', null, null),
    ('cam_huyen_04', 'Tang 2', 'The gioi xanh', 'Tang 2', null, null)
) as data(ma_camera, vi_tri_lap_dat, toa_nha, tang, vi_do, kinh_do)
join camera c on c.ma_camera = data.ma_camera
on conflict (camera_id) do nothing;
