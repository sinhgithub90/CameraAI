# Tai lieu ERD Database MultiCAMAI

Tai lieu nay mo ta co so du lieu PostgreSQL cua he thong MultiCAMAI theo schema `multicamai`.
Muc tieu cua database la thay the cac file local nhu `users.json`, `cameras.json`, `recordings.db`, dong thoi luu metadata video, anh, su kien AI, canh bao va bang chung de truy van nhanh.

File SQL nguon: `database/multicamai_postgresql_schema.sql`

## 1. Tong quan thiet ke

Database duoc chia theo cac mien nghiep vu:

- User, role, permission: quan ly nguoi dung, vai tro, quyen, phien dang nhap va audit.
- Organization and area: cong ty, khu vuc, cay khu vuc.
- Camera management: thong tin camera, ket noi RTSP, thong so ky thuat, lap dat, tinh trang, lich su, bao tri, nhom camera.
- Recording and playback: doan video, anh chup, metadata phuc vu nut "Phat lai".
- AI, event, alert, evidence: mo hinh AI, luat AI, vung giam sat, ket qua suy luan, su kien, canh bao, bang chung.
- Notification, report and dashboard: thong bao, nguoi nhan, bao cao da luu, dashboard.

Nguyen tac chung:

- Bang chinh luu trang thai hien tai cua thuc the.
- Bang lich su/log luu cac thay doi theo thoi gian.
- File video/anh thuc te nam tren disk/NAS/object storage, database chi luu duong dan va metadata.
- Cac bang co `deleted_at` ap dung soft delete, khong xoa vat ly ngay.
- Cac bang co `created_at`, `updated_at` phuc vu audit va dong bo frontend/backend.
- Cac truong `jsonb` dung cho du lieu linh hoat nhu polygon, raw AI result, filter bao cao.

## 2. Quy uoc cot dung chung

| Cot | Y nghia |
| --- | --- |
| `id` | Khoa chinh noi bo, kieu `bigserial`. |
| `ma_*` | Ma nghiep vu, thuong unique, dung cho import/export/API. |
| `ten_*` | Ten hien thi cho nguoi dung. |
| `trang_thai` | Trang thai ban ghi, vi du `ACTIVE`, `INACTIVE`. |
| `trang_thai_hien_tai` | Trang thai hien tai cua entity co vong doi, vi du camera/canh bao. |
| `created_at` | Thoi diem tao ban ghi. |
| `updated_at` | Thoi diem cap nhat gan nhat, duoc trigger `set_updated_at()` cap nhat. |
| `deleted_at` | Thoi diem xoa mem. Neu khac null thi coi nhu da xoa. |
| `*_id` | Khoa ngoai toi bang lien quan. |

## 3. So do quan he tong quat

```text
cong_ty
  └── khu_vuc
        └── camera
              ├── ket_noi_camera
              ├── thong_so_ky_thuat_camera
              ├── lap_dat_camera
              ├── tinh_trang_camera
              ├── doan_video
              ├── anh_chup
              ├── luat_ai
              ├── su_kien_phat_hien
              └── canh_bao

nguoi_dung
  ├── vai_tro_nguoi_dung ── vai_tro ── vai_tro_quyen ── quyen
  ├── phien_dang_nhap
  ├── lich_su_dang_nhap
  ├── nhat_ky_he_thong
  └── bao_cao_da_luu / bo_cuc_dashboard / thong_bao

canh_bao
  ├── lich_su_trang_thai_canh_bao
  ├── tien_trinh_xu_ly_canh_bao
  ├── phan_cong_xu_ly_canh_bao
  ├── binh_luan_canh_bao
  ├── bang_chung
  │     ├── doan_video
  │     ├── anh_chup
  │     └── tep_bang_chung
  └── thong_bao
```

## 4. User, role, permission

### 4.1. `vai_tro`

Luu danh sach vai tro trong he thong.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID vai tro. |
| `ma_vai_tro` | varchar(80), unique, not null | Ma vai tro, vi du `ADMIN`, `SUPERVISOR`, `STAFF`. |
| `ten_vai_tro` | varchar(160), not null | Ten hien thi cua vai tro. |
| `mo_ta` | text | Mo ta vai tro. |
| `la_he_thong` | boolean, default false | Danh dau vai tro mac dinh cua he thong. |
| `trang_thai` | varchar(30), default `ACTIVE` | Trang thai vai tro. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Thoi diem xoa mem. |

### 4.2. `quyen`

Luu cac quyen/chuc nang co the gan cho vai tro.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID quyen. |
| `ma_quyen` | varchar(120), unique, not null | Ma quyen, vi du `live`, `playback`, `cammgmt`. |
| `ten_quyen` | varchar(160), not null | Ten quyen hien thi. |
| `nhom_chuc_nang` | varchar(80), not null | Nhom nghiep vu cua quyen: camera, recording, user, alert... |
| `hanh_dong` | varchar(80), not null | Hanh dong: read, write, export... |
| `trang_thai` | varchar(30), default `ACTIVE` | Trang thai quyen. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 4.3. `vai_tro_quyen`

Bang trung gian gan quyen cho vai tro.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID quan he. |
| `vai_tro_id` | FK `vai_tro(id)`, not null | Vai tro duoc gan quyen. |
| `quyen_id` | FK `quyen(id)`, not null | Quyen duoc gan. |
| `duoc_phep` | boolean, default true | Co duoc phep hay khong. |
| `ghi_chu` | text | Ghi chu bo sung. |
| Unique | `(vai_tro_id, quyen_id)` | Mot vai tro khong gan trung mot quyen. |

### 4.4. `nguoi_dung`

Bang chinh luu thong tin tai khoan nguoi dung.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID nguoi dung. |
| `ma_nguoi_dung` | varchar(80), unique, not null | Ma nguoi dung/noi bo. |
| `ten_dang_nhap` | varchar(120), unique, not null | Username dang nhap. |
| `ho_ten` | varchar(180), not null | Ho ten nguoi dung. |
| `email` | varchar(180), unique | Email. |
| `so_dien_thoai` | varchar(40) | So dien thoai. |
| `trang_thai` | varchar(30), default `ACTIVE` | Trang thai tai khoan. |
| `lan_dang_nhap_cuoi` | timestamptz | Lan dang nhap gan nhat. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem tai khoan. |

### 4.5. `ho_so_nguoi_dung`

Thong tin mo rong cua nguoi dung, tach rieng khoi bang login.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID ho so. |
| `nguoi_dung_id` | FK `nguoi_dung(id)`, unique, not null | Nguoi dung so huu ho so. |
| `ma_nhan_vien` | varchar(80) | Ma nhan vien. |
| `anh_dai_dien` | text | Duong dan anh dai dien. |
| `ngay_sinh` | date | Ngay sinh. |
| `gioi_tinh` | varchar(20) | Gioi tinh. |
| `dia_chi` | text | Dia chi. |
| `chuc_vu` | varchar(120) | Chuc vu. |
| `phong_ban` | varchar(120) | Phong ban. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 4.6. `xac_thuc_nguoi_dung`

Thong tin xac thuc, password hash va trang thai khoa.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID xac thuc. |
| `nguoi_dung_id` | FK `nguoi_dung(id)`, unique, not null | Tai khoan tuong ung. |
| `password_hash` | text, not null | Mat khau da hash, khong luu plain text. |
| `so_lan_dang_nhap_sai` | int, default 0 | So lan dang nhap sai lien tiep. |
| `khoa_den` | timestamptz | Thoi diem khoa den. |
| `mfa_enabled` | boolean, default false | Bat/tat xac thuc da yeu to. |
| `lan_doi_mat_khau_cuoi` | timestamptz | Lan doi mat khau gan nhat. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 4.7. `vai_tro_nguoi_dung`

Gan vai tro cho nguoi dung theo khoang thoi gian.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID quan he. |
| `nguoi_dung_id` | FK `nguoi_dung(id)`, not null | Nguoi dung. |
| `vai_tro_id` | FK `vai_tro(id)`, not null | Vai tro. |
| `ngay_bat_dau` | timestamptz, default now | Thoi diem bat dau co vai tro. |
| `ngay_ket_thuc` | timestamptz | Thoi diem ket thuc vai tro. |
| `dang_hoat_dong` | boolean, default true | Vai tro hien con hieu luc. |
| Unique | `(nguoi_dung_id, vai_tro_id, ngay_bat_dau)` | Tranh trung dot gan vai tro. |

### 4.8. `phien_dang_nhap`

Luu phien dang nhap/refresh token.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID phien. |
| `nguoi_dung_id` | FK `nguoi_dung(id)` | Nguoi dung dang nhap. |
| `refresh_token_hash` | text | Refresh token da hash. |
| `thiet_bi` | text | Thong tin thiet bi/trinh duyet. |
| `dia_chi_ip` | inet | IP dang nhap. |
| `bat_dau_luc` | timestamptz, default now | Bat dau phien. |
| `het_han_luc` | timestamptz | Thoi diem het han. |
| `dang_hoat_dong` | boolean, default true | Phien con hieu luc. |

### 4.9. `lich_su_dang_nhap`

Lich su thanh cong/that bai khi dang nhap.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID lich su. |
| `nguoi_dung_id` | FK `nguoi_dung(id)` | Nguoi dung neu xac dinh duoc. |
| `ten_dang_nhap_nhap_vao` | varchar(120) | Username ma user nhap. |
| `trang_thai` | varchar(40), not null | Thanh cong/that bai. |
| `ly_do_that_bai` | text | Ly do dang nhap that bai. |
| `dia_chi_ip` | inet | Dia chi IP. |
| `thoi_diem` | timestamptz, default now | Thoi diem dang nhap. |

### 4.10. `lich_su_mat_khau_nguoi_dung`

Luu lich su doi mat khau de kiem soat trung lap/chinh sach.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID lich su. |
| `nguoi_dung_id` | FK `nguoi_dung(id)`, not null | Tai khoan doi mat khau. |
| `password_hash` | text, not null | Hash mat khau cu/moi tuy logic backend. |
| `ly_do_thay_doi` | text | Ly do doi mat khau. |
| `thoi_diem_thay_doi` | timestamptz, default now | Thoi diem doi. |

### 4.11. `nhat_ky_he_thong`

Audit log chung cho he thong.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID log. |
| `nguoi_dung_id` | FK `nguoi_dung(id)` | Nguoi thuc hien. |
| `hanh_dong` | varchar(120), not null | Hanh dong: CREATE, UPDATE, DELETE, LOGIN... |
| `ten_bang` | varchar(120) | Bang bi tac dong. |
| `ban_ghi_id` | text | ID ban ghi bi tac dong. |
| `gia_tri_cu` | jsonb | Gia tri truoc khi sua. |
| `gia_tri_moi` | jsonb | Gia tri sau khi sua. |
| `dia_chi_ip` | inet | IP thuc hien. |
| `thoi_diem` | timestamptz, default now | Thoi diem ghi log. |

## 5. Organization and area

### 5.1. `cong_ty`

Bang don vi/chu so huu he thong.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID cong ty. |
| `ma_cong_ty` | varchar(80), unique, not null | Ma cong ty. |
| `ten_cong_ty` | varchar(220), not null | Ten day du. |
| `ten_viet_tat` | varchar(80) | Ten viet tat. |
| `ma_so_thue` | varchar(80) | Ma so thue. |
| `email` | varchar(180) | Email lien he. |
| `so_dien_thoai` | varchar(40) | Dien thoai lien he. |
| `dia_chi` | text | Dia chi. |
| `trang_thai` | varchar(30), default `ACTIVE` | Trang thai. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem. |

### 5.2. `loai_khu_vuc`

Danh muc loai khu vuc.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID loai. |
| `ma_loai_khu_vuc` | varchar(80), unique, not null | Ma loai: BUILDING, FLOOR, ZONE... |
| `ten_loai_khu_vuc` | varchar(160), not null | Ten loai khu vuc. |
| `mo_ta` | text | Mo ta. |
| `trang_thai` | varchar(30), default `ACTIVE` | Trang thai. |

### 5.3. `khu_vuc`

Khu vuc lap dat camera, co the tao cay cha-con.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID khu vuc. |
| `cong_ty_id` | FK `cong_ty(id)`, not null | Cong ty so huu. |
| `loai_khu_vuc_id` | FK `loai_khu_vuc(id)` | Loai khu vuc. |
| `khu_vuc_cha_id` | FK `khu_vuc(id)` | Khu vuc cha. |
| `ma_khu_vuc` | varchar(80), unique, not null | Ma khu vuc. |
| `ten_khu_vuc` | varchar(180), not null | Ten khu vuc. |
| `mo_ta` | text | Mo ta. |
| `trang_thai` | varchar(30), default `ACTIVE` | Trang thai. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem. |

### 5.4. `lich_su_khu_vuc`

Lich su them/sua/xoa khu vuc.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID lich su. |
| `khu_vuc_id` | FK `khu_vuc(id)`, not null | Khu vuc bi thay doi. |
| `hanh_dong` | varchar(120), not null | Hanh dong. |
| `gia_tri_cu` | jsonb | Du lieu truoc. |
| `gia_tri_moi` | jsonb | Du lieu sau. |
| `nguoi_thuc_hien_id` | FK `nguoi_dung(id)` | Nguoi thuc hien. |
| `thoi_diem` | timestamptz, default now | Thoi diem thay doi. |

## 6. Camera management

### 6.1. `camera`

Bang chinh cua camera, phuc vu danh sach live, quan ly camera va lien ket recording.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID camera trong database. |
| `khu_vuc_id` | FK `khu_vuc(id)` | Khu vuc camera thuoc ve. |
| `ma_camera` | varchar(120), unique, not null | Ma camera nghiep vu. |
| `ten_camera` | varchar(220), not null | Ten hien thi tren giao dien. |
| `hang_san_xuat` | varchar(120) | Hang san xuat. |
| `model` | varchar(120) | Model thiet bi. |
| `serial_number` | varchar(160) | So serial. |
| `loai_nguon` | varchar(40), default `RTSP` | Loai nguon stream. |
| `stream_key` | varchar(120), unique, not null | Key map voi go2rtc/frontend, vi du `cam_huyen_01`. |
| `anh_dai_dien` | text | Duong dan anh thumbnail. |
| `thu_tu_hien_thi` | int, default 0 | Thu tu hien thi tren UI. |
| `trang_thai_hien_tai` | varchar(40), default `UNKNOWN` | ONLINE/OFFLINE/UNKNOWN. |
| `bat_ai` | boolean, default false | Camera co bat AI khong. |
| `bat_ghi_hinh` | boolean, default true | Camera co duoc ghi hinh khong. |
| `created_by` | FK `nguoi_dung(id)` | Nguoi tao camera. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem camera. |

### 6.2. `ket_noi_camera`

Thong tin ket noi RTSP/ONVIF cua camera.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID cau hinh ket noi. |
| `camera_id` | FK `camera(id)`, unique, not null | Camera tuong ung. |
| `dia_chi_ip` | inet | IP camera. |
| `cong_rtsp` | int | Port RTSP. |
| `duong_dan_rtsp` | text, not null | URL RTSP day du. |
| `ten_dang_nhap` | varchar(160) | Username camera. |
| `mat_khau_ma_hoa` | text | Mat khau da ma hoa. |
| `cong_onvif` | int | Port ONVIF neu co. |
| `rtsp_transport` | varchar(20), default `tcp` | Transport RTSP. |
| `go2rtc_stream_name` | varchar(120) | Ten stream trong go2rtc. |
| `lan_kiem_tra_cuoi` | timestamptz | Lan check ket noi gan nhat. |
| `trang_thai_ket_noi` | varchar(40), default `UNKNOWN` | Trang thai ket noi. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 6.3. `thong_so_ky_thuat_camera`

Thong so ky thuat hien thi tren panel camera.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID thong so. |
| `camera_id` | FK `camera(id)`, unique, not null | Camera tuong ung. |
| `do_phan_giai` | varchar(40) | Do phan giai, vi du `1920x1080`. |
| `fps` | int | Frame per second. |
| `bitrate_kbps` | int | Bitrate kbps. |
| `codec` | varchar(40) | Codec video, vi du H264/H265. |
| `ho_tro_ptz` | boolean, default false | Co ho tro PTZ. |
| `ho_tro_am_thanh` | boolean, default false | Co ho tro audio. |
| `tam_nhin_dem` | boolean, default false | Co tam nhin dem. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 6.4. `lap_dat_camera`

Thong tin vi tri lap dat vat ly cua camera.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID thong tin lap dat. |
| `camera_id` | FK `camera(id)`, unique, not null | Camera tuong ung. |
| `khu_vuc_id` | FK `khu_vuc(id)` | Khu vuc lap dat. |
| `vi_tri_lap_dat` | varchar(220) | Vi tri cu the. |
| `toa_nha` | varchar(120) | Toa nha. |
| `tang` | varchar(80) | Tang. |
| `vi_do` | numeric(10,7) | Latitude. |
| `kinh_do` | numeric(10,7) | Longitude. |
| `huong_nhin` | varchar(120) | Huong nhin camera. |
| `ngay_lap_dat` | date | Ngay lap dat. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 6.5. `lich_su_trang_thai_camera`

Lich su online/offline/thay doi trang thai camera.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID lich su. |
| `camera_id` | FK `camera(id)`, not null | Camera. |
| `trang_thai` | varchar(40), not null | Trang thai trong khoang thoi gian. |
| `bat_dau_luc` | timestamptz, not null | Bat dau trang thai. |
| `ket_thuc_luc` | timestamptz | Ket thuc trang thai. |
| `thoi_luong_giay` | int | Tong thoi gian giay. |
| `ly_do` | text | Ly do neu co. |

### 6.6. `tinh_trang_camera`

Metric suc khoe camera theo thoi gian.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID metric. |
| `camera_id` | FK `camera(id)`, not null | Camera. |
| `fps_hien_tai` | numeric(8,2) | FPS hien tai. |
| `do_tre_mang_ms` | int | Do tre mang ms. |
| `mat_goi_tin` | numeric(8,4) | Ti le mat goi. |
| `nhiet_do` | numeric(8,2) | Nhiet do neu lay duoc. |
| `online` | boolean, not null | Co online tai thoi diem ghi nhan. |
| `ghi_nhan_luc` | timestamptz, default now | Thoi diem ghi nhan. |

### 6.7. `nhat_ky_camera`

Log hanh dong/loi lien quan camera.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID log. |
| `camera_id` | FK `camera(id)` | Camera lien quan. |
| `hanh_dong` | varchar(120), not null | Hanh dong/loai log. |
| `noi_dung` | text | Noi dung log. |
| `muc_do` | varchar(40), default `INFO` | INFO/WARN/ERROR. |
| `thoi_diem` | timestamptz, default now | Thoi diem log. |

### 6.8. `bao_tri_camera`

Ke hoach va lich su bao tri camera.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID bao tri. |
| `camera_id` | FK `camera(id)`, not null | Camera bao tri. |
| `loai_bao_tri` | varchar(80), not null | Loai bao tri. |
| `noi_dung` | text | Noi dung cong viec. |
| `ngay_bat_dau` | timestamptz, not null | Thoi diem bat dau. |
| `ngay_ket_thuc` | timestamptz | Thoi diem ket thuc. |
| `nguoi_thuc_hien_id` | FK `nguoi_dung(id)` | Nguoi phu trach. |
| `trang_thai` | varchar(40), default `PLANNED` | Trang thai bao tri. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 6.9. `nhom_camera`

Nhom camera theo cong ty/khu vuc/nghiep vu.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID nhom. |
| `cong_ty_id` | FK `cong_ty(id)` | Cong ty so huu nhom. |
| `ten_nhom` | varchar(180), not null | Ten nhom. |
| `mo_ta` | text | Mo ta. |
| `trang_thai` | varchar(40), default `ACTIVE` | Trang thai. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem. |
| Unique | `(cong_ty_id, ten_nhom)` | Ten nhom khong trung trong mot cong ty. |

### 6.10. `thanh_vien_nhom_camera`

Bang trung gian gan camera vao nhom.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID quan he. |
| `nhom_camera_id` | FK `nhom_camera(id)`, not null | Nhom camera. |
| `camera_id` | FK `camera(id)`, not null | Camera thanh vien. |
| `ghi_chu` | text | Ghi chu. |
| `created_at` | timestamptz | Thoi diem tao. |
| Unique | `(nhom_camera_id, camera_id)` | Mot camera khong trung trong cung nhom. |

## 7. Recording and playback

### 7.1. `doan_video`

Metadata cua file video da ghi. Day la bang chinh phuc vu nut "Phat lai".

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID doan video. |
| `camera_id` | FK `camera(id)`, not null | Camera sinh ra video. |
| `bang_chung_id` | FK `bang_chung(id)` | Neu video duoc gan lam bang chung. |
| `loai_doan` | varchar(40), default `RAW_RECORDING` | Loai video: raw recording, event clip... |
| `duong_dan_video` | text, unique, not null | Duong dan file tren disk/NAS/object storage. |
| `bat_dau_luc` | timestamptz, not null | Thoi diem bat dau cua clip. |
| `ket_thuc_luc` | timestamptz, not null | Thoi diem ket thuc cua clip. |
| `thoi_luong_giay` | int, not null | Do dai clip. |
| `dung_luong` | bigint, default 0 | Kich thuoc file byte. |
| `mime_type` | varchar(120), default `video/mp4` | MIME type. |
| `codec` | varchar(40) | Codec video. |
| `do_phan_giai` | varchar(40) | Do phan giai. |
| `fps` | numeric(8,2) | FPS cua video. |
| `checksum_sha256` | varchar(80) | Checksum de xac thuc file. |
| `trang_thai` | varchar(40), default `READY` | Trang thai file: READY/PROCESSING/MISSING... |
| `created_at` | timestamptz | Thoi diem tao metadata. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem metadata. |

Truy van playback nen dung index `idx_doan_video_camera_time`:

```sql
select *
from multicamai.doan_video
where camera_id = :camera_id
  and bat_dau_luc <= :to_time
  and ket_thuc_luc >= :from_time
  and deleted_at is null
order by bat_dau_luc desc;
```

### 7.2. `anh_chup`

Metadata anh chup tu camera.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID anh. |
| `camera_id` | FK `camera(id)`, not null | Camera sinh anh. |
| `bang_chung_id` | FK `bang_chung(id)` | Bang chung neu co. |
| `duong_dan_anh` | text, unique, not null | Duong dan file anh. |
| `thoi_diem_chup` | timestamptz, not null | Thoi diem chup. |
| `dung_luong` | bigint, default 0 | Kich thuoc byte. |
| `mime_type` | varchar(120), default `image/jpeg` | MIME type. |
| `metadata` | jsonb, default `{}` | Metadata mo rong: width, height, source... |
| `created_at` | timestamptz | Thoi diem tao. |
| `deleted_at` | timestamptz | Xoa mem. |

## 8. AI, event, alert, evidence

### 8.1. `mo_hinh_ai`

Danh muc mo hinh AI.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID mo hinh. |
| `ma_mo_hinh` | varchar(120), unique, not null | Ma mo hinh. |
| `ten_mo_hinh` | varchar(220), not null | Ten mo hinh. |
| `loai_mo_hinh` | varchar(80) | Loai: detection, face, intrusion... |
| `mo_ta` | text | Mo ta. |
| `trang_thai` | varchar(40), default `ACTIVE` | Trang thai. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 8.2. `phien_ban_mo_hinh_ai`

Phien ban cu the cua mo hinh AI.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID phien ban. |
| `mo_hinh_ai_id` | FK `mo_hinh_ai(id)`, not null | Mo hinh cha. |
| `so_phien_ban` | varchar(80), not null | So phien ban. |
| `framework` | varchar(80) | Framework: YOLO, TensorRT, OpenVINO... |
| `duong_dan_trong_so` | text | File weights/model. |
| `do_chinh_xac` | numeric(8,4) | Do chinh xac danh gia. |
| `trang_thai` | varchar(40), default `ACTIVE` | Trang thai. |
| `created_at` | timestamptz | Thoi diem tao. |
| Unique | `(mo_hinh_ai_id, so_phien_ban)` | Khong trung version trong mot model. |

### 8.3. `loai_su_kien`

Danh muc loai su kien AI.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID loai su kien. |
| `ma_loai_su_kien` | varchar(120), unique, not null | Ma loai. |
| `ten_loai_su_kien` | varchar(220), not null | Ten loai su kien. |
| `mo_ta` | text | Mo ta. |
| `muc_do_mac_dinh` | varchar(40), default `MEDIUM` | Muc do mac dinh. |
| `trang_thai` | varchar(40), default `ACTIVE` | Trang thai. |

### 8.4. `loai_doi_tuong`

Danh muc doi tuong AI phat hien.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID loai doi tuong. |
| `ma_doi_tuong` | varchar(120), unique, not null | Ma doi tuong. |
| `ten_doi_tuong` | varchar(220), not null | Ten doi tuong: person, vehicle... |
| `mo_ta` | text | Mo ta. |
| `trang_thai` | varchar(40), default `ACTIVE` | Trang thai. |

### 8.5. `luat_ai`

Luat AI ap dung cho tung camera.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID luat. |
| `camera_id` | FK `camera(id)`, not null | Camera ap dung. |
| `loai_su_kien_id` | FK `loai_su_kien(id)`, not null | Loai su kien can phat hien. |
| `phien_ban_mo_hinh_ai_id` | FK `phien_ban_mo_hinh_ai(id)` | Model version duoc dung. |
| `ten_luat` | varchar(220), not null | Ten luat. |
| `nguong_tin_cay` | numeric(8,4), default 0.7 | Threshold confidence. |
| `muc_do` | varchar(40), default `MEDIUM` | Muc do canh bao. |
| `bat` | boolean, default true | Bat/tat luat. |
| `created_by` | FK `nguoi_dung(id)` | Nguoi tao. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| Unique | `(camera_id, loai_su_kien_id, ten_luat)` | Tranh trung luat tren camera. |

### 8.6. `vung_giam_sat`

Polygon vung giam sat cua luat AI.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID vung. |
| `luat_ai_id` | FK `luat_ai(id)`, not null | Luat AI so huu vung. |
| `ten_vung` | varchar(180), not null | Ten vung. |
| `toa_do_polygon` | jsonb, not null | Toa do polygon tren khung hinh. |
| `do_uu_tien` | int, default 0 | Thu tu/uu tien xu ly. |
| `bat` | boolean, default true | Bat/tat vung. |

### 8.7. `lich_hoat_dong_luat_ai`

Lich chay luat AI theo gio va ngay trong tuan.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID lich. |
| `luat_ai_id` | FK `luat_ai(id)`, not null | Luat AI. |
| `gio_bat_dau` | time, not null | Gio bat dau. |
| `gio_ket_thuc` | time, not null | Gio ket thuc. |
| `cac_ngay_trong_tuan` | int[], default `[1..7]` | Cac ngay ap dung. |
| `bat` | boolean, default true | Bat/tat lich. |
| `created_at` | timestamptz | Thoi diem tao. |

### 8.8. `lich_su_luat_ai`

Lich su thay doi cau hinh luat AI.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID lich su. |
| `luat_ai_id` | FK `luat_ai(id)`, not null | Luat bi thay doi. |
| `hanh_dong` | varchar(120), not null | Hanh dong. |
| `gia_tri_cu` | jsonb | Gia tri truoc. |
| `gia_tri_moi` | jsonb | Gia tri sau. |
| `nguoi_thuc_hien_id` | FK `nguoi_dung(id)` | Nguoi thuc hien. |
| `thoi_diem` | timestamptz, default now | Thoi diem thay doi. |

### 8.9. `khung_hinh_phan_tich`

Frame/anh dau vao de AI phan tich.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID khung hinh. |
| `camera_id` | FK `camera(id)`, not null | Camera sinh frame. |
| `duong_dan_anh` | text | Duong dan anh frame neu co luu. |
| `thoi_diem_chup` | timestamptz, not null | Thoi diem frame. |
| `chieu_rong` | int | Width. |
| `chieu_cao` | int | Height. |

### 8.10. `ket_qua_suy_luan_ai`

Ket qua inference tu AI engine.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID ket qua. |
| `khung_hinh_phan_tich_id` | FK `khung_hinh_phan_tich(id)` | Frame dau vao. |
| `phien_ban_mo_hinh_ai_id` | FK `phien_ban_mo_hinh_ai(id)` | Model version. |
| `thoi_gian_xu_ly_ms` | int | Thoi gian xu ly ms. |
| `ket_qua_raw` | jsonb, default `{}` | Raw output cua model. |
| `xu_ly_luc` | timestamptz, default now | Thoi diem xu ly. |

### 8.11. `su_kien_phat_hien`

Su kien da duoc AI phat hien.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID su kien. |
| `camera_id` | FK `camera(id)`, not null | Camera phat hien. |
| `luat_ai_id` | FK `luat_ai(id)` | Luat sinh su kien. |
| `loai_su_kien_id` | FK `loai_su_kien(id)`, not null | Loai su kien. |
| `ket_qua_suy_luan_ai_id` | FK `ket_qua_suy_luan_ai(id)` | Ket qua AI lien quan. |
| `do_tin_cay` | numeric(8,4) | Confidence. |
| `mo_ta` | text | Mo ta su kien. |
| `phat_hien_luc` | timestamptz, not null | Thoi diem phat hien. |

### 8.12. `doi_tuong_phat_hien`

Doi tuong nam trong mot su kien AI.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID doi tuong. |
| `su_kien_phat_hien_id` | FK `su_kien_phat_hien(id)`, not null | Su kien cha. |
| `loai_doi_tuong_id` | FK `loai_doi_tuong(id)` | Loai doi tuong. |
| `nhan` | varchar(160) | Label nhan dien. |
| `do_tin_cay` | numeric(8,4) | Confidence cua object. |
| `bounding_box` | jsonb | Toa do bounding box. |

### 8.13. `canh_bao`

Canh bao hien thi tren he thong.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID canh bao. |
| `su_kien_phat_hien_id` | FK `su_kien_phat_hien(id)` | Su kien nguon. |
| `camera_id` | FK `camera(id)`, not null | Camera lien quan. |
| `khu_vuc_id` | FK `khu_vuc(id)` | Khu vuc lien quan. |
| `luat_ai_id` | FK `luat_ai(id)` | Luat sinh canh bao. |
| `muc_do` | varchar(40), default `MEDIUM` | Muc do canh bao. |
| `trang_thai_hien_tai` | varchar(40), default `NEW` | NEW/ASSIGNED/RESOLVED... |
| `mo_ta` | text | Mo ta canh bao. |
| `phat_sinh_luc` | timestamptz, default now | Thoi diem phat sinh. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem. |

### 8.14. `lich_su_trang_thai_canh_bao`

Lich su trang thai cua canh bao.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID lich su. |
| `canh_bao_id` | FK `canh_bao(id)`, not null | Canh bao. |
| `trang_thai` | varchar(40), not null | Trang thai trong khoang thoi gian. |
| `bat_dau_luc` | timestamptz, default now | Bat dau trang thai. |
| `ket_thuc_luc` | timestamptz | Ket thuc trang thai. |
| `nguoi_thuc_hien_id` | FK `nguoi_dung(id)` | Nguoi thay doi. |
| `ghi_chu` | text | Ghi chu. |

### 8.15. `tien_trinh_xu_ly_canh_bao`

Timeline xu ly canh bao.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID tien trinh. |
| `canh_bao_id` | FK `canh_bao(id)`, not null | Canh bao. |
| `hanh_dong` | varchar(120), not null | Hanh dong xu ly. |
| `noi_dung` | text | Noi dung chi tiet. |
| `nguoi_thuc_hien_id` | FK `nguoi_dung(id)` | Nguoi thuc hien. |
| `thoi_diem` | timestamptz, default now | Thoi diem. |

### 8.16. `phan_cong_xu_ly_canh_bao`

Phan cong canh bao cho nguoi xu ly.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID phan cong. |
| `canh_bao_id` | FK `canh_bao(id)`, not null | Canh bao. |
| `nguoi_duoc_giao_id` | FK `nguoi_dung(id)`, not null | Nguoi duoc giao. |
| `nguoi_giao_id` | FK `nguoi_dung(id)` | Nguoi giao viec. |
| `han_xu_ly` | timestamptz | Han xu ly. |
| `trang_thai` | varchar(40), default `ASSIGNED` | Trang thai phan cong. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 8.17. `binh_luan_canh_bao`

Binh luan/noi dung trao doi trong canh bao.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID binh luan. |
| `canh_bao_id` | FK `canh_bao(id)`, not null | Canh bao. |
| `nguoi_dung_id` | FK `nguoi_dung(id)` | Nguoi binh luan. |
| `noi_dung` | text, not null | Noi dung. |
| `thoi_diem` | timestamptz, default now | Thoi diem binh luan. |
| `deleted_at` | timestamptz | Xoa mem binh luan. |

### 8.18. `bang_chung`

Ho so bang chung gom video, anh, tep lien quan.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID bang chung. |
| `canh_bao_id` | FK `canh_bao(id)` | Canh bao lien quan. |
| `su_kien_phat_hien_id` | FK `su_kien_phat_hien(id)` | Su kien lien quan. |
| `tieu_de` | varchar(220) | Tieu de bang chung. |
| `mo_ta` | text | Mo ta. |
| `thoi_diem_ghi_nhan` | timestamptz, default now | Thoi diem ghi nhan. |

### 8.19. `tep_bang_chung`

File dinh kem cua bang chung.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID tep. |
| `bang_chung_id` | FK `bang_chung(id)`, not null | Ho so bang chung. |
| `loai_tep` | varchar(40), not null | VIDEO/IMAGE/DOCUMENT... |
| `ten_tep` | varchar(260), not null | Ten file hien thi. |
| `duong_dan` | text, not null | Duong dan file. |
| `dung_luong` | bigint, default 0 | Kich thuoc byte. |
| `mime_type` | varchar(120) | MIME type. |
| `checksum_sha256` | varchar(80) | Checksum. |
| `created_at` | timestamptz | Thoi diem tao. |

## 9. Notification, report and dashboard

### 9.1. `mau_thong_bao`

Template thong bao theo kenh.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID mau. |
| `ma_mau` | varchar(120), unique, not null | Ma template. |
| `ten_mau` | varchar(220), not null | Ten template. |
| `kenh` | varchar(40), not null | Kenh: email, sms, app... |
| `tieu_de_mau` | text | Template tieu de. |
| `noi_dung_mau` | text, not null | Template noi dung. |
| `trang_thai` | varchar(40), default `ACTIVE` | Trang thai. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 9.2. `thong_bao`

Ban ghi thong bao can gui/da gui.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID thong bao. |
| `canh_bao_id` | FK `canh_bao(id)` | Canh bao lien quan. |
| `mau_thong_bao_id` | FK `mau_thong_bao(id)` | Template da dung. |
| `tieu_de` | text | Tieu de thuc te. |
| `noi_dung` | text, not null | Noi dung thuc te. |
| `kenh` | varchar(40), not null | Kenh gui. |
| `trang_thai` | varchar(40), default `PENDING` | Trang thai gui. |
| `tao_luc` | timestamptz, default now | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 9.3. `nguoi_nhan_thong_bao`

Danh sach nguoi nhan cua thong bao.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID nguoi nhan. |
| `thong_bao_id` | FK `thong_bao(id)`, not null | Thong bao. |
| `nguoi_dung_id` | FK `nguoi_dung(id)` | Nguoi nhan noi bo. |
| `dia_chi_nhan` | text | Dia chi nhan neu ngoai he thong. |
| `trang_thai_nhan` | varchar(40), default `PENDING` | Trang thai nhan. |
| `da_doc` | boolean, default false | Da doc hay chua. |
| `doc_luc` | timestamptz | Thoi diem doc. |
| `created_at` | timestamptz | Thoi diem tao. |

### 9.4. `nhat_ky_thong_bao`

Log gui thong bao.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID log. |
| `thong_bao_id` | FK `thong_bao(id)`, not null | Thong bao. |
| `kenh` | varchar(40), not null | Kenh gui. |
| `trang_thai_gui` | varchar(40), not null | Ket qua gui. |
| `thong_bao_loi` | text | Noi dung loi neu co. |
| `gui_luc` | timestamptz, default now | Thoi diem gui. |

### 9.5. `bao_cao_da_luu`

Bao cao/filter da luu cua nguoi dung.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID bao cao. |
| `nguoi_dung_id` | FK `nguoi_dung(id)` | Chu so huu bao cao. |
| `ten_bao_cao` | varchar(220), not null | Ten bao cao. |
| `loai_bao_cao` | varchar(80), not null | Loai bao cao. |
| `bo_loc` | jsonb, default `{}` | Bo loc truy van. |
| `cau_hinh` | jsonb, default `{}` | Cau hinh hien thi/export. |
| `trang_thai` | varchar(40), default `ACTIVE` | Trang thai. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem. |

### 9.6. `thanh_phan_dashboard`

Danh muc widget/dashboard component.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID thanh phan. |
| `ma_thanh_phan` | varchar(120), unique, not null | Ma component. |
| `ten_thanh_phan` | varchar(220), not null | Ten component. |
| `loai_bieu_do` | varchar(80), not null | Loai chart/widget. |
| `cau_hinh_mac_dinh` | jsonb, default `{}` | Cau hinh mac dinh. |
| `trang_thai` | varchar(40), default `ACTIVE` | Trang thai. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |

### 9.7. `bo_cuc_dashboard`

Layout dashboard rieng cua nguoi dung.

| Cot | Kieu / rang buoc | Y nghia |
| --- | --- | --- |
| `id` | PK | ID bo cuc. |
| `nguoi_dung_id` | FK `nguoi_dung(id)` | Nguoi so huu. |
| `ten_bo_cuc` | varchar(220), not null | Ten bo cuc. |
| `cau_hinh_bo_cuc` | jsonb, default `{}` | Layout JSON. |
| `la_mac_dinh` | boolean, default false | Co phai dashboard mac dinh. |
| `created_at` | timestamptz | Thoi diem tao. |
| `updated_at` | timestamptz | Thoi diem cap nhat. |
| `deleted_at` | timestamptz | Xoa mem. |

## 10. Index chinh va muc dich

| Index | Bang | Muc dich |
| --- | --- | --- |
| `idx_camera_khu_vuc` | `camera` | Loc camera theo khu vuc. |
| `idx_camera_stream_key` | `camera` | Tim camera theo stream key frontend/go2rtc. |
| `idx_camera_status` | `camera` | Loc camera online/offline. |
| `idx_doan_video_camera_time` | `doan_video` | Truy van playback theo camera va khoang thoi gian. |
| `idx_doan_video_time` | `doan_video` | Truy van video theo khoang thoi gian toan he thong. |
| `idx_anh_chup_camera_time` | `anh_chup` | Truy van anh chup theo camera/thoi gian. |
| `idx_tinh_trang_camera_time` | `tinh_trang_camera` | Lay metric moi nhat cua camera. |
| `idx_su_kien_camera_time` | `su_kien_phat_hien` | Xem su kien AI theo camera. |
| `idx_canh_bao_camera_time` | `canh_bao` | Xem canh bao theo camera. |
| `idx_canh_bao_status` | `canh_bao` | Loc canh bao theo trang thai. |
| `idx_bang_chung_canh_bao` | `bang_chung` | Lay bang chung cua canh bao. |
| `idx_nguoi_nhan_thong_bao_user` | `nguoi_nhan_thong_bao` | Lay thong bao cua nguoi dung. |
| `idx_bao_cao_user` | `bao_cao_da_luu` | Lay bao cao da luu cua user. |
| `idx_dashboard_user` | `bo_cuc_dashboard` | Lay dashboard mac dinh cua user. |

## 11. Luong du lieu cho tinh nang "Phat lai"

1. Camera duoc khai bao trong `camera`.
2. Thong tin RTSP nam trong `ket_noi_camera`.
3. Backend/FFmpeg ghi file video vao thu muc luu tru.
4. Moi file hoan thanh se co metadata trong `doan_video`.
5. Frontend bam "Phat lai" goi API search theo camera/khu vuc/vi tri/thoi gian.
6. Backend query `doan_video`, join `camera`, `khu_vuc`, `lap_dat_camera`.
7. Khi user chon mot doan video, backend stream file theo `duong_dan_video`.

Bang toi thieu cho playback:

- `camera`
- `khu_vuc`
- `lap_dat_camera`
- `doan_video`

Bang mo rong cho bang chung/su kien:

- `su_kien_phat_hien`
- `canh_bao`
- `bang_chung`
- `tep_bang_chung`
- `anh_chup`

## 12. Luong du lieu cho lich su them/sua/xoa camera

Bang `camera` chi luu trang thai hien tai. De xem lich su thay doi can dung:

- `nhat_ky_he_thong`: audit chung, luu `ten_bang = 'camera'`, `ban_ghi_id = camera.id`.
- `nhat_ky_camera`: log nghiep vu cua camera.
- `lich_su_trang_thai_camera`: lich su online/offline.
- `bao_tri_camera`: lich su bao tri.

Vi du truy van audit cua mot camera:

```sql
select *
from multicamai.nhat_ky_he_thong
where ten_bang = 'camera'
  and ban_ghi_id = :camera_id::text
order by thoi_diem desc;
```

## 13. Ghi chu product-ready

- Khong luu mat khau camera plain text trong `ket_noi_camera.mat_khau_ma_hoa`; nen ma hoa bang key rieng.
- Khong luu password user plain text; `xac_thuc_nguoi_dung.password_hash` nen dung bcrypt/argon2.
- `doan_video.duong_dan_video` co the la local path, NAS path, hoac object storage key.
- Nen co job dinh ky kiem tra file trong `doan_video` con ton tai khong, neu mat thi cap nhat `trang_thai = 'MISSING'`.
- Nen partition bang lon theo thoi gian khi du lieu tang manh: `doan_video`, `tinh_trang_camera`, `su_kien_phat_hien`, `canh_bao`, `nhat_ky_he_thong`.
- Nen dung soft delete cho entity nghiep vu, hard delete chi dung cho data tam/cache.
