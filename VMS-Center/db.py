import os
import re
import json
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:trinh123@localhost:5432/camera_system_db",
)
GO2RTC_BASE_URL = os.getenv("GO2RTC_BASE_URL", "http://127.0.0.1:1984").rstrip("/")


@contextmanager
def db_cursor(commit=False):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("set search_path to multicamai, public")
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _deepcopy_json(value):
    return json.loads(json.dumps(value))


def _merge_settings(default_settings, db_settings):
    merged = _deepcopy_json(default_settings)
    for section, values in (db_settings or {}).items():
        if section in merged and isinstance(merged[section], dict) and isinstance(values, dict):
            merged[section].update(values)
        else:
            merged[section] = values
    return merged


def get_system_settings(default_settings):
    with db_cursor() as cur:
        cur.execute(
            """
            select section, cau_hinh
            from cau_hinh_he_thong
            order by section
            """
        )
        rows = cur.fetchall()

    db_settings = {row["section"]: row["cau_hinh"] for row in rows}
    return _merge_settings(default_settings, db_settings)


def update_system_settings_section(section, values, default_settings):
    if section not in default_settings:
        raise ValueError(f"Unknown settings section: {section}")
    if not isinstance(values, dict):
        raise ValueError("Settings section value must be an object")

    section_settings = _deepcopy_json(default_settings[section])
    if isinstance(section_settings, dict):
        section_settings.update(values)
    else:
        section_settings = values

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            insert into cau_hinh_he_thong (section, cau_hinh)
            values (%s, %s)
            on conflict (section) do update
              set cau_hinh = excluded.cau_hinh
            """,
            (section, psycopg2.extras.Json(section_settings)),
        )
    return get_system_settings(default_settings)


def replace_system_settings(settings, default_settings):
    if not isinstance(settings, dict):
        raise ValueError("Settings payload must be an object")

    normalized = _merge_settings(default_settings, settings)
    with db_cursor(commit=True) as cur:
        for section, values in normalized.items():
            cur.execute(
                """
                insert into cau_hinh_he_thong (section, cau_hinh)
                values (%s, %s)
                on conflict (section) do update
                  set cau_hinh = excluded.cau_hinh
                """,
                (section, psycopg2.extras.Json(values)),
            )
    return get_system_settings(default_settings)


def list_cameras_for_ui():
    with db_cursor() as cur:
        cur.execute(
            """
            select
              c.id as db_id,
              c.ma_camera,
              c.ten_camera,
              c.hang_san_xuat,
              c.model,
              c.stream_key,
              c.thu_tu_hien_thi,
              c.trang_thai_hien_tai,
              kv.ten_khu_vuc,
              host(knc.dia_chi_ip) as dia_chi_ip,
              tsk.do_phan_giai,
              tsk.fps,
              tsk.bitrate_kbps,
              tsk.codec,
              ldc.vi_tri_lap_dat,
              ldc.toa_nha,
              ldc.tang,
              ldc.vi_do,
              ldc.kinh_do
            from camera c
            left join khu_vuc kv on kv.id = c.khu_vuc_id
            left join ket_noi_camera knc on knc.camera_id = c.id
            left join thong_so_ky_thuat_camera tsk on tsk.camera_id = c.id
            left join lap_dat_camera ldc on ldc.camera_id = c.id
            where c.deleted_at is null
            order by c.thu_tu_hien_thi, c.id
            """
        )
        rows = cur.fetchall()

    cameras = []
    for idx, row in enumerate(rows, start=1):
        stream_key = row["stream_key"] or row["ma_camera"]
        status = "online" if row["trang_thai_hien_tai"] == "ONLINE" else "offline"
        model_text = " ".join(
            part for part in [row.get("hang_san_xuat"), row.get("model")] if part
        )
        zone_parts = [row.get("ten_khu_vuc"), row.get("toa_nha")]
        zone = " / ".join(dict.fromkeys(part for part in zone_parts if part))
        camera = {
            "id": stream_key,
            "db_id": row["db_id"],
            "index": row["thu_tu_hien_thi"] or idx,
            "name": row["ten_camera"],
            "ip": row.get("dia_chi_ip") or "-",
            "model": model_text or row.get("model") or "-",
            "zone": zone or "-",
            "loc": row.get("vi_tri_lap_dat") or "-",
            "type": "video",
            "src": f"{GO2RTC_BASE_URL}/stream.html?src={stream_key}&mode=webrtc",
            "tag": "Live",
            "status": status,
            "resolution": row.get("do_phan_giai") or "1920x1080",
            "fps": row.get("fps") or 25,
            "bitrate": row.get("bitrate_kbps") or 4096,
            "codec": row.get("codec") or "H264",
        }
        if row.get("vi_do") is not None:
            camera["lat"] = float(row["vi_do"])
        if row.get("kinh_do") is not None:
            camera["lng"] = float(row["kinh_do"])
        cameras.append(camera)
    return cameras


def list_users_for_ui():
    role_label_map = {
        "ADMIN": "Quản trị viên",
        "SUPERVISOR": "Giám sát",
        "STAFF": "Nhân viên",
    }
    status_label_map = {
        "ACTIVE": "Hoạt động",
        "LOCKED": "Tạm khóa",
        "INACTIVE": "Tạm khóa",
        "DISABLED": "Tạm khóa",
    }

    with db_cursor() as cur:
        cur.execute(
            """
            select
              nd.id,
              nd.ten_dang_nhap,
              nd.ho_ten,
              nd.email,
              nd.so_dien_thoai,
              nd.trang_thai,
              hsn.phong_ban,
              vt.ma_vai_tro,
              vt.ten_vai_tro,
              array_remove(array_agg(distinct q.ma_quyen order by q.ma_quyen), null) as permissions
            from nguoi_dung nd
            left join ho_so_nguoi_dung hsn
              on hsn.nguoi_dung_id = nd.id
            left join vai_tro_nguoi_dung vtnd
              on vtnd.nguoi_dung_id = nd.id
             and vtnd.dang_hoat_dong = true
             and (vtnd.ngay_ket_thuc is null or vtnd.ngay_ket_thuc > now())
            left join vai_tro vt
              on vt.id = vtnd.vai_tro_id
             and vt.deleted_at is null
             and vt.trang_thai = 'ACTIVE'
            left join vai_tro_quyen vtq
              on vtq.vai_tro_id = vt.id
             and vtq.duoc_phep = true
            left join quyen q
              on q.id = vtq.quyen_id
             and q.trang_thai = 'ACTIVE'
            where nd.deleted_at is null
            group by nd.id, hsn.id, vt.id
            order by nd.id
            """
        )
        rows = cur.fetchall()

    users = []
    for row in rows:
        role_code = row.get("ma_vai_tro")
        status_code = row.get("trang_thai")
        users.append(
            {
                "username": row.get("ten_dang_nhap"),
                "name": row.get("ho_ten"),
                "role": role_label_map.get(role_code, row.get("ten_vai_tro") or role_code or "Nhân viên"),
                "unit": row.get("phong_ban") or "",
                "email": row.get("email") or "",
                "status": status_label_map.get(status_code, "Tạm khóa"),
                "phone": row.get("so_dien_thoai") or "",
                "permissions": row.get("permissions") or [],
            }
        )
    return users


def get_user_by_email_for_ui(email):
    role_label_map = {
        "ADMIN": "Quản trị viên",
        "SUPERVISOR": "Giám sát",
        "STAFF": "Nhân viên",
    }
    status_label_map = {
        "ACTIVE": "Hoạt động",
        "LOCKED": "Tạm khóa",
        "INACTIVE": "Tạm khóa",
        "DISABLED": "Tạm khóa",
    }
    email = (email or "").strip()
    if not email:
        return {"auth_status": "not_found"}

    with db_cursor() as cur:
        cur.execute(
            """
            select
              nd.id,
              nd.ten_dang_nhap,
              nd.ho_ten,
              nd.email,
              nd.so_dien_thoai,
              nd.trang_thai,
              hsn.phong_ban,
              vt.ma_vai_tro,
              vt.ten_vai_tro,
              array_remove(array_agg(distinct q.ma_quyen order by q.ma_quyen), null) as permissions
            from nguoi_dung nd
            left join ho_so_nguoi_dung hsn
              on hsn.nguoi_dung_id = nd.id
            left join vai_tro_nguoi_dung vtnd
              on vtnd.nguoi_dung_id = nd.id
             and vtnd.dang_hoat_dong = true
             and (vtnd.ngay_ket_thuc is null or vtnd.ngay_ket_thuc > now())
            left join vai_tro vt
              on vt.id = vtnd.vai_tro_id
             and vt.deleted_at is null
             and vt.trang_thai = 'ACTIVE'
            left join vai_tro_quyen vtq
              on vtq.vai_tro_id = vt.id
             and vtq.duoc_phep = true
            left join quyen q
              on q.id = vtq.quyen_id
             and q.trang_thai = 'ACTIVE'
            where nd.deleted_at is null
              and lower(nd.email) = lower(%s)
            group by nd.id, hsn.id, vt.id
            limit 1
            """,
            (email,),
        )
        row = cur.fetchone()

    if not row:
        return {"auth_status": "not_found"}
    if row.get("trang_thai") != "ACTIVE":
        return {"auth_status": "inactive"}

    role_code = row.get("ma_vai_tro")
    status_code = row.get("trang_thai")
    return {
        "auth_status": "ok",
        "username": row.get("ten_dang_nhap"),
        "name": row.get("ho_ten"),
        "role": role_label_map.get(role_code, row.get("ten_vai_tro") or role_code or "Nhân viên"),
        "unit": row.get("phong_ban") or "",
        "email": row.get("email") or "",
        "status": status_label_map.get(status_code, "Tạm khóa"),
        "phone": row.get("so_dien_thoai") or "",
        "permissions": row.get("permissions") or [],
    }


def _role_code_from_ui(role):
    normalized = (role or "").strip().lower()
    role_map = {
        "admin": "ADMIN",
        "administrator": "ADMIN",
        "quan tri vien": "ADMIN",
        "quản trị viên": "ADMIN",
        "supervisor": "SUPERVISOR",
        "giam sat": "SUPERVISOR",
        "giám sát": "SUPERVISOR",
        "staff": "STAFF",
        "nhan vien": "STAFF",
        "nhân viên": "STAFF",
    }
    upper_role = (role or "").strip().upper()
    if upper_role in {"ADMIN", "SUPERVISOR", "STAFF"}:
        return upper_role
    return role_map.get(normalized, "STAFF")


def _status_code_from_ui(status):
    normalized = (status or "").strip().lower()
    status_map = {
        "active": "ACTIVE",
        "hoat dong": "ACTIVE",
        "hoạt động": "ACTIVE",
        "locked": "LOCKED",
        "tam khoa": "LOCKED",
        "tạm khóa": "LOCKED",
        "inactive": "INACTIVE",
        "disabled": "DISABLED",
        "deleted": "DELETED",
    }
    upper_status = (status or "").strip().upper()
    if upper_status in {"ACTIVE", "LOCKED", "INACTIVE", "DISABLED", "DELETED"}:
        return upper_status
    return status_map.get(normalized, "ACTIVE")


def _next_user_code(cur, username):
    base = re.sub(r"[^A-Za-z0-9]+", "_", username or "").strip("_").upper() or "USER"
    base = f"U_{base}"[:70]
    candidate = base
    suffix = 2
    while True:
        cur.execute(
            "select 1 from nguoi_dung where ma_nguoi_dung = %s limit 1",
            (candidate,),
        )
        if not cur.fetchone():
            return candidate
        candidate = f"{base[:70 - len(str(suffix)) - 1]}_{suffix}"
        suffix += 1


def create_user_for_ui(user):
    username = (user.username or "").strip()
    email = (user.email or "").strip() or None
    name = (user.name or "").strip()
    phone = (user.phone or "").strip() or None
    unit = (user.unit or "").strip() or None
    role_code = _role_code_from_ui(user.role)

    if not username:
        return {"status": "invalid_username"}
    if not name:
        return {"status": "invalid_name"}

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            select 1
            from nguoi_dung
            where deleted_at is null
              and lower(ten_dang_nhap) = lower(%s)
            limit 1
            """,
            (username,),
        )
        if cur.fetchone():
            return {"status": "duplicate_username"}

        if email:
            cur.execute(
                """
                select 1
                from nguoi_dung
                where deleted_at is null
                  and lower(email) = lower(%s)
                limit 1
                """,
                (email,),
            )
            if cur.fetchone():
                return {"status": "duplicate_email"}

        cur.execute(
            """
            select id
            from vai_tro
            where ma_vai_tro = %s
              and trang_thai = 'ACTIVE'
              and deleted_at is null
            limit 1
            """,
            (role_code,),
        )
        role = cur.fetchone()
        if not role:
            return {"status": "role_not_found"}

        user_code = _next_user_code(cur, username)
        cur.execute(
            """
            insert into nguoi_dung (
              ma_nguoi_dung, ten_dang_nhap, ho_ten,
              email, so_dien_thoai, trang_thai
            )
            values (%s, %s, %s, %s, %s, 'ACTIVE')
            returning id
            """,
            (user_code, username, name, email, phone),
        )
        user_id = cur.fetchone()["id"]

        cur.execute(
            """
            insert into ho_so_nguoi_dung (nguoi_dung_id, phong_ban)
            values (%s, %s)
            """,
            (user_id, unit),
        )
        cur.execute(
            """
            insert into xac_thuc_nguoi_dung (
              nguoi_dung_id, password_hash, lan_doi_mat_khau_cuoi
            )
            values (%s, crypt(%s, gen_salt('bf', 12)), now())
            """,
            (user_id, user.password),
        )
        cur.execute(
            """
            insert into vai_tro_nguoi_dung (nguoi_dung_id, vai_tro_id)
            values (%s, %s)
            """,
            (user_id, role["id"]),
        )

    return {"status": "success"}


def _sent_fields(data):
    return set(
        getattr(data, "model_fields_set", None)
        or getattr(data, "__fields_set__", set())
    )


def _role_code_from_permissions(cur, permissions):
    requested = sorted(set(permissions or []))
    if not requested:
        return None

    cur.execute(
        """
        select
          vt.ma_vai_tro,
          array_remove(array_agg(distinct q.ma_quyen order by q.ma_quyen), null) as permissions
        from vai_tro vt
        left join vai_tro_quyen vtq
          on vtq.vai_tro_id = vt.id
         and vtq.duoc_phep = true
        left join quyen q
          on q.id = vtq.quyen_id
         and q.trang_thai = 'ACTIVE'
        where vt.deleted_at is null
          and vt.trang_thai = 'ACTIVE'
        group by vt.id
        """
    )
    best_role = None
    best_extra_count = None
    requested_set = set(requested)
    for row in cur.fetchall():
        role_permissions = set(row.get("permissions") or [])
        if requested_set == role_permissions:
            return row.get("ma_vai_tro")
        if requested_set.issubset(role_permissions):
            extra_count = len(role_permissions - requested_set)
            if best_extra_count is None or extra_count < best_extra_count:
                best_role = row.get("ma_vai_tro")
                best_extra_count = extra_count
    return best_role


def update_user_for_ui(username, data):
    sent_fields = _sent_fields(data)
    username = (username or "").strip()
    if not username:
        return {"status": "not_found"}

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            select id
            from nguoi_dung
            where deleted_at is null
              and ten_dang_nhap = %s
            limit 1
            """,
            (username,),
        )
        user_row = cur.fetchone()
        if not user_row:
            return {"status": "not_found"}
        user_id = user_row["id"]

        if "email" in sent_fields:
            email = (data.email or "").strip() or None
            if email:
                cur.execute(
                    """
                    select 1
                    from nguoi_dung
                    where deleted_at is null
                      and lower(email) = lower(%s)
                      and id <> %s
                    limit 1
                    """,
                    (email, user_id),
                )
                if cur.fetchone():
                    return {"status": "duplicate_email"}

        user_updates = []
        user_params = []
        if "name" in sent_fields:
            name = (data.name or "").strip()
            if not name:
                return {"status": "invalid_name"}
            user_updates.append("ho_ten = %s")
            user_params.append(name)
        if "email" in sent_fields:
            user_updates.append("email = %s")
            user_params.append((data.email or "").strip() or None)
        if "phone" in sent_fields:
            user_updates.append("so_dien_thoai = %s")
            user_params.append((data.phone or "").strip() or None)
        if user_updates:
            user_params.append(user_id)
            cur.execute(
                f"""
                update nguoi_dung
                set {", ".join(user_updates)}
                where id = %s
                """,
                tuple(user_params),
            )

        if "unit" in sent_fields:
            cur.execute(
                """
                insert into ho_so_nguoi_dung (nguoi_dung_id, phong_ban)
                values (%s, %s)
                on conflict (nguoi_dung_id)
                do update set phong_ban = excluded.phong_ban
                """,
                (user_id, (data.unit or "").strip() or None),
            )

        role_code = None
        if "role" in sent_fields:
            role_code = _role_code_from_ui(data.role)
        elif "permissions" in sent_fields:
            role_code = _role_code_from_permissions(cur, data.permissions)

        if role_code:
            cur.execute(
                """
                select id
                from vai_tro
                where ma_vai_tro = %s
                  and trang_thai = 'ACTIVE'
                  and deleted_at is null
                limit 1
                """,
                (role_code,),
            )
            role = cur.fetchone()
            if not role:
                return {"status": "role_not_found"}

            cur.execute(
                """
                update vai_tro_nguoi_dung
                set dang_hoat_dong = false,
                    ngay_ket_thuc = now()
                where nguoi_dung_id = %s
                  and dang_hoat_dong = true
                """,
                (user_id,),
            )
            cur.execute(
                """
                insert into vai_tro_nguoi_dung (nguoi_dung_id, vai_tro_id)
                values (%s, %s)
                """,
                (user_id, role["id"]),
            )

    return {"status": "success"}


def _get_user_for_admin_action(cur, username):
    cur.execute(
        """
        select
          nd.id,
          nd.ten_dang_nhap,
          nd.ho_ten,
          nd.trang_thai,
          nd.deleted_at,
          bool_or(vt.ma_vai_tro = 'ADMIN') as is_admin
        from nguoi_dung nd
        left join vai_tro_nguoi_dung vtnd
          on vtnd.nguoi_dung_id = nd.id
         and vtnd.dang_hoat_dong = true
         and (vtnd.ngay_ket_thuc is null or vtnd.ngay_ket_thuc > now())
        left join vai_tro vt
          on vt.id = vtnd.vai_tro_id
         and vt.deleted_at is null
         and vt.trang_thai = 'ACTIVE'
        where nd.deleted_at is null
          and nd.ten_dang_nhap = %s
        group by nd.id
        limit 1
        """,
        (username,),
    )
    return cur.fetchone()


def _active_admin_count(cur):
    cur.execute(
        """
        select count(*) as total
        from nguoi_dung nd
        join vai_tro_nguoi_dung vtnd
          on vtnd.nguoi_dung_id = nd.id
         and vtnd.dang_hoat_dong = true
         and (vtnd.ngay_ket_thuc is null or vtnd.ngay_ket_thuc > now())
        join vai_tro vt
          on vt.id = vtnd.vai_tro_id
         and vt.ma_vai_tro = 'ADMIN'
         and vt.trang_thai = 'ACTIVE'
         and vt.deleted_at is null
        where nd.deleted_at is null
          and nd.trang_thai = 'ACTIVE'
        """
    )
    return cur.fetchone()["total"]


def _admin_actor_id(cur, admin_username):
    if not admin_username:
        return None
    cur.execute(
        """
        select id
        from nguoi_dung
        where deleted_at is null
          and ten_dang_nhap = %s
        limit 1
        """,
        (admin_username,),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def reset_user_password_for_ui(username, new_password, admin_username=None):
    with db_cursor(commit=True) as cur:
        user = _get_user_for_admin_action(cur, username)
        if not user:
            return {"status": "not_found"}

        actor_id = _admin_actor_id(cur, admin_username)
        cur.execute(
            """
            select password_hash
            from xac_thuc_nguoi_dung
            where nguoi_dung_id = %s
            limit 1
            """,
            (user["id"],),
        )
        auth = cur.fetchone()
        if auth:
            cur.execute(
                """
                insert into lich_su_mat_khau_nguoi_dung (
                  nguoi_dung_id, password_hash, ly_do_thay_doi
                )
                values (%s, %s, 'ADMIN_RESET')
                """,
                (user["id"], auth["password_hash"]),
            )

        cur.execute(
            """
            update xac_thuc_nguoi_dung
            set password_hash = crypt(%s, gen_salt('bf', 12)),
                so_lan_dang_nhap_sai = 0,
                khoa_den = null,
                lan_doi_mat_khau_cuoi = now()
            where nguoi_dung_id = %s
            """,
            (new_password, user["id"]),
        )
        if cur.rowcount == 0:
            cur.execute(
                """
                insert into xac_thuc_nguoi_dung (
                  nguoi_dung_id, password_hash, so_lan_dang_nhap_sai,
                  khoa_den, lan_doi_mat_khau_cuoi
                )
                values (%s, crypt(%s, gen_salt('bf', 12)), 0, null, now())
                """,
                (user["id"], new_password),
            )

        cur.execute(
            """
            insert into nhat_ky_he_thong (
              nguoi_dung_id, hanh_dong, ten_bang, ban_ghi_id, gia_tri_moi
            )
            values (%s, 'RESET_USER_PASSWORD', 'nguoi_dung', %s, %s::jsonb)
            """,
            (actor_id, str(user["id"]), json.dumps({"username": username})),
        )

    return {"status": "success"}


def toggle_user_lock_for_ui(username, admin_username=None):
    with db_cursor(commit=True) as cur:
        user = _get_user_for_admin_action(cur, username)
        if not user:
            return {"status": "not_found"}
        if user.get("is_admin") and user.get("trang_thai") == "ACTIVE" and _active_admin_count(cur) <= 1:
            return {"status": "last_admin"}

        new_status = "LOCKED" if user.get("trang_thai") == "ACTIVE" else "ACTIVE"
        actor_id = _admin_actor_id(cur, admin_username)
        cur.execute(
            """
            update nguoi_dung
            set trang_thai = %s
            where id = %s
            """,
            (new_status, user["id"]),
        )
        cur.execute(
            """
            update xac_thuc_nguoi_dung
            set khoa_den = case when %s = 'LOCKED' then now() else null end
            where nguoi_dung_id = %s
            """,
            (new_status, user["id"]),
        )
        cur.execute(
            """
            update phien_dang_nhap
            set dang_hoat_dong = false,
                het_han_luc = coalesce(het_han_luc, now())
            where nguoi_dung_id = %s
              and %s = 'LOCKED'
              and dang_hoat_dong = true
            """,
            (user["id"], new_status),
        )
        cur.execute(
            """
            insert into nhat_ky_he_thong (
              nguoi_dung_id, hanh_dong, ten_bang, ban_ghi_id, gia_tri_cu, gia_tri_moi
            )
            values (%s, 'TOGGLE_USER_LOCK', 'nguoi_dung', %s, %s::jsonb, %s::jsonb)
            """,
            (
                actor_id,
                str(user["id"]),
                json.dumps({"trang_thai": user.get("trang_thai")}),
                json.dumps({"trang_thai": new_status}),
            ),
        )

    status_label = "Tạm khóa" if new_status == "LOCKED" else "Hoạt động"
    return {"status": "success", "new_status": status_label}


def soft_delete_user_for_ui(username, admin_username=None):
    with db_cursor(commit=True) as cur:
        user = _get_user_for_admin_action(cur, username)
        if not user:
            return {"status": "not_found"}
        if user.get("is_admin") and user.get("trang_thai") == "ACTIVE" and _active_admin_count(cur) <= 1:
            return {"status": "last_admin"}

        actor_id = _admin_actor_id(cur, admin_username)
        cur.execute(
            """
            update nguoi_dung
            set trang_thai = 'DELETED',
                deleted_at = now()
            where id = %s
            """,
            (user["id"],),
        )
        cur.execute(
            """
            update xac_thuc_nguoi_dung
            set khoa_den = now()
            where nguoi_dung_id = %s
            """,
            (user["id"],),
        )
        cur.execute(
            """
            update phien_dang_nhap
            set dang_hoat_dong = false,
                het_han_luc = coalesce(het_han_luc, now())
            where nguoi_dung_id = %s
              and dang_hoat_dong = true
            """,
            (user["id"],),
        )
        cur.execute(
            """
            update vai_tro_nguoi_dung
            set dang_hoat_dong = false,
                ngay_ket_thuc = coalesce(ngay_ket_thuc, now())
            where nguoi_dung_id = %s
              and dang_hoat_dong = true
            """,
            (user["id"],),
        )
        cur.execute(
            """
            insert into nhat_ky_he_thong (
              nguoi_dung_id, hanh_dong, ten_bang, ban_ghi_id, gia_tri_cu, gia_tri_moi
            )
            values (%s, 'SOFT_DELETE_USER', 'nguoi_dung', %s, %s::jsonb, %s::jsonb)
            """,
            (
                actor_id,
                str(user["id"]),
                json.dumps({"trang_thai": user.get("trang_thai"), "deleted_at": None}),
                json.dumps({"trang_thai": "DELETED", "deleted_at": "now"}),
            ),
        )

    return {"status": "success"}


def export_backup_from_db():
    return {
        "cameras": list_cameras_for_ui(),
        "users": list_users_for_ui(),
    }


def _require_dict_list(value, field_name):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} phai la danh sach")
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{field_name}[{idx}] phai la object")
    return value


def _area_id_from_backup(cur, zone):
    zone = (zone or "").strip()
    area_id = _find_area_id(cur, zone)
    if area_id:
        return area_id
    if "/" in zone:
        area_id = _find_area_id(cur, zone.split("/", 1)[0].strip())
    return area_id


def _build_rtsp_url(camera_item):
    explicit_url = (camera_item.get("rtsp_url") or camera_item.get("rtsp") or "").strip()
    if explicit_url:
        return explicit_url
    ip = (camera_item.get("ip") or "").strip()
    user = (camera_item.get("user") or camera_item.get("username") or "").strip()
    password = (camera_item.get("password") or "").strip()
    if not ip or ip == "-" or not user or not password:
        return None
    port = camera_item.get("rtsp_port") or 2004
    channel = camera_item.get("rtsp_channel") or "102"
    return f"rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}"


def _restore_user_item(cur, user_item):
    username = (user_item.get("username") or "").strip()
    if not username:
        raise ValueError("User backup thieu username")
    name = (user_item.get("name") or "").strip()
    if not name:
        raise ValueError(f"User {username} thieu name")
    email = (user_item.get("email") or "").strip() or None
    phone = (user_item.get("phone") or "").strip() or None
    unit = (user_item.get("unit") or "").strip() or None
    role_code = _role_code_from_ui(user_item.get("role"))
    status_code = _status_code_from_ui(user_item.get("status"))

    cur.execute(
        """
        select id
        from vai_tro
        where ma_vai_tro = %s
          and trang_thai = 'ACTIVE'
          and deleted_at is null
        limit 1
        """,
        (role_code,),
    )
    role = cur.fetchone()
    if not role:
        raise ValueError(f"User {username} co role khong hop le")

    if email:
        cur.execute(
            """
            select ten_dang_nhap
            from nguoi_dung
            where lower(email) = lower(%s)
              and ten_dang_nhap <> %s
              and deleted_at is null
            limit 1
            """,
            (email, username),
        )
        if cur.fetchone():
            raise ValueError(f"Email {email} da ton tai o user khac")

    cur.execute(
        "select id from nguoi_dung where ten_dang_nhap = %s limit 1",
        (username,),
    )
    existing = cur.fetchone()
    if existing:
        user_id = existing["id"]
        cur.execute(
            """
            update nguoi_dung
            set ho_ten = %s,
                email = %s,
                so_dien_thoai = %s,
                trang_thai = %s,
                deleted_at = null
            where id = %s
            """,
            (name, email, phone, status_code, user_id),
        )
    else:
        password = user_item.get("password")
        if not password:
            raise ValueError(f"User moi {username} thieu password")
        user_code = _next_user_code(cur, username)
        cur.execute(
            """
            insert into nguoi_dung (
              ma_nguoi_dung, ten_dang_nhap, ho_ten,
              email, so_dien_thoai, trang_thai
            )
            values (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (user_code, username, name, email, phone, status_code),
        )
        user_id = cur.fetchone()["id"]

    cur.execute(
        """
        insert into ho_so_nguoi_dung (nguoi_dung_id, phong_ban)
        values (%s, %s)
        on conflict (nguoi_dung_id)
        do update set phong_ban = excluded.phong_ban
        """,
        (user_id, unit),
    )
    password = user_item.get("password")
    if password:
        cur.execute(
            """
            insert into xac_thuc_nguoi_dung (
              nguoi_dung_id, password_hash, so_lan_dang_nhap_sai, khoa_den,
              lan_doi_mat_khau_cuoi
            )
            values (
              %s, crypt(%s, gen_salt('bf', 12)), 0,
              case when %s = 'ACTIVE' then null else now() end,
              now()
            )
            on conflict (nguoi_dung_id)
            do update set
              password_hash = excluded.password_hash,
              so_lan_dang_nhap_sai = 0,
              khoa_den = excluded.khoa_den,
              lan_doi_mat_khau_cuoi = now()
            """,
            (user_id, password, status_code),
        )
    else:
        cur.execute(
            "select 1 from xac_thuc_nguoi_dung where nguoi_dung_id = %s limit 1",
            (user_id,),
        )
        if not cur.fetchone():
            raise ValueError(f"User {username} thieu password de tao xac thuc")

    cur.execute(
        """
        update vai_tro_nguoi_dung
        set dang_hoat_dong = false,
            ngay_ket_thuc = coalesce(ngay_ket_thuc, now())
        where nguoi_dung_id = %s
          and dang_hoat_dong = true
        """,
        (user_id,),
    )
    cur.execute(
        "insert into vai_tro_nguoi_dung (nguoi_dung_id, vai_tro_id) values (%s, %s)",
        (user_id, role["id"]),
    )


def _restore_camera_item(cur, camera_item):
    stream_key = (camera_item.get("id") or camera_item.get("stream_key") or "").strip()
    if not stream_key:
        raise ValueError("Camera backup thieu id")
    name = (camera_item.get("name") or "").strip()
    if not name:
        raise ValueError(f"Camera {stream_key} thieu name")
    ip = (camera_item.get("ip") or "").strip()
    if ip == "-":
        ip = ""
    manufacturer, model = _split_camera_model(camera_item.get("model"))
    area_id = _area_id_from_backup(cur, camera_item.get("zone"))
    rtsp_url = _build_rtsp_url(camera_item)
    status_code = "ONLINE" if camera_item.get("status") == "online" else "OFFLINE"
    order_index = camera_item.get("index") or 0

    cur.execute(
        """
        select c.id, knc.duong_dan_rtsp
        from camera c
        left join ket_noi_camera knc on knc.camera_id = c.id
        where c.stream_key = %s
           or c.ma_camera = %s
        limit 1
        """,
        (stream_key, stream_key),
    )
    existing = cur.fetchone()
    if existing:
        camera_id = existing["id"]
        rtsp_url = rtsp_url or existing.get("duong_dan_rtsp")
        cur.execute(
            """
            update camera
            set khu_vuc_id = %s,
                ma_camera = %s,
                ten_camera = %s,
                hang_san_xuat = %s,
                model = %s,
                stream_key = %s,
                thu_tu_hien_thi = %s,
                trang_thai_hien_tai = %s,
                deleted_at = null,
                bat_ghi_hinh = true
            where id = %s
            """,
            (
                area_id,
                stream_key,
                name,
                manufacturer,
                model,
                stream_key,
                order_index,
                status_code,
                camera_id,
            ),
        )
    else:
        if not ip or not rtsp_url:
            raise ValueError(f"Camera moi {stream_key} thieu ip hoac thong tin RTSP")
        cur.execute(
            """
            insert into camera (
              khu_vuc_id, ma_camera, ten_camera, hang_san_xuat, model,
              loai_nguon, stream_key, thu_tu_hien_thi, trang_thai_hien_tai,
              bat_ai, bat_ghi_hinh
            )
            values (%s, %s, %s, %s, %s, 'RTSP', %s, %s, %s, false, true)
            returning id
            """,
            (
                area_id,
                stream_key,
                name,
                manufacturer,
                model,
                stream_key,
                order_index,
                status_code,
            ),
        )
        camera_id = cur.fetchone()["id"]

    if not rtsp_url:
        raise ValueError(f"Camera {stream_key} thieu RTSP")

    cur.execute(
        """
        insert into ket_noi_camera (
          camera_id, dia_chi_ip, cong_rtsp, duong_dan_rtsp,
          ten_dang_nhap, mat_khau_ma_hoa, rtsp_transport,
          go2rtc_stream_name, trang_thai_ket_noi
        )
        values (%s, nullif(%s, '')::inet, %s, %s, %s, %s, 'tcp', %s, 'UNKNOWN')
        on conflict (camera_id)
        do update set
          dia_chi_ip = excluded.dia_chi_ip,
          cong_rtsp = excluded.cong_rtsp,
          duong_dan_rtsp = excluded.duong_dan_rtsp,
          ten_dang_nhap = excluded.ten_dang_nhap,
          mat_khau_ma_hoa = coalesce(excluded.mat_khau_ma_hoa, ket_noi_camera.mat_khau_ma_hoa),
          go2rtc_stream_name = excluded.go2rtc_stream_name,
          updated_at = now()
        """,
        (
            camera_id,
            ip,
            camera_item.get("rtsp_port") or 2004,
            rtsp_url,
            camera_item.get("user") or camera_item.get("username"),
            camera_item.get("password"),
            stream_key,
        ),
    )
    cur.execute(
        """
        insert into thong_so_ky_thuat_camera (
          camera_id, do_phan_giai, fps, bitrate_kbps, codec
        )
        values (%s, %s, %s, %s, %s)
        on conflict (camera_id)
        do update set
          do_phan_giai = excluded.do_phan_giai,
          fps = excluded.fps,
          bitrate_kbps = excluded.bitrate_kbps,
          codec = excluded.codec,
          updated_at = now()
        """,
        (
            camera_id,
            camera_item.get("resolution") or "1920x1080",
            camera_item.get("fps") or 25,
            camera_item.get("bitrate") or 4096,
            camera_item.get("codec") or "H264",
        ),
    )
    cur.execute(
        """
        insert into lap_dat_camera (
          camera_id, khu_vuc_id, vi_tri_lap_dat, toa_nha, tang, vi_do, kinh_do
        )
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (camera_id)
        do update set
          khu_vuc_id = excluded.khu_vuc_id,
          vi_tri_lap_dat = excluded.vi_tri_lap_dat,
          toa_nha = excluded.toa_nha,
          tang = excluded.tang,
          vi_do = excluded.vi_do,
          kinh_do = excluded.kinh_do,
          updated_at = now()
        """,
        (
            camera_id,
            area_id,
            camera_item.get("loc"),
            camera_item.get("building"),
            camera_item.get("floor"),
            camera_item.get("lat"),
            camera_item.get("lng"),
        ),
    )
    return stream_key, rtsp_url


def restore_backup_to_db(cameras=None, users=None):
    camera_items = _require_dict_list(cameras, "cameras")
    user_items = _require_dict_list(users, "users")
    streams_to_sync = []

    with db_cursor(commit=True) as cur:
        for user_item in user_items:
            _restore_user_item(cur, user_item)
        for camera_item in camera_items:
            streams_to_sync.append(_restore_camera_item(cur, camera_item))

    return {"status": "success", "streams_to_sync": streams_to_sync}


def authenticate_user_for_ui(username, password):
    role_label_map = {
        "ADMIN": "Quản trị viên",
        "SUPERVISOR": "Giám sát",
        "STAFF": "Nhân viên",
    }
    status_label_map = {
        "ACTIVE": "Hoạt động",
        "LOCKED": "Tạm khóa",
        "INACTIVE": "Tạm khóa",
        "DISABLED": "Tạm khóa",
    }

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            select
              nd.id,
              nd.ten_dang_nhap,
              nd.ho_ten,
              nd.email,
              nd.so_dien_thoai,
              nd.trang_thai,
              xt.khoa_den,
              xt.password_hash = crypt(%s, xt.password_hash) as password_ok,
              vt.ma_vai_tro,
              vt.ten_vai_tro,
              array_remove(array_agg(distinct q.ma_quyen order by q.ma_quyen), null) as permissions
            from nguoi_dung nd
            join xac_thuc_nguoi_dung xt on xt.nguoi_dung_id = nd.id
            left join vai_tro_nguoi_dung vtnd
              on vtnd.nguoi_dung_id = nd.id
             and vtnd.dang_hoat_dong = true
             and (vtnd.ngay_ket_thuc is null or vtnd.ngay_ket_thuc > now())
            left join vai_tro vt
              on vt.id = vtnd.vai_tro_id
             and vt.deleted_at is null
             and vt.trang_thai = 'ACTIVE'
            left join vai_tro_quyen vtq
              on vtq.vai_tro_id = vt.id
             and vtq.duoc_phep = true
            left join quyen q
              on q.id = vtq.quyen_id
             and q.trang_thai = 'ACTIVE'
            where nd.deleted_at is null
              and nd.ten_dang_nhap = %s
            group by nd.id, xt.id, vt.id
            limit 1
            """,
            (password, username),
        )
        row = cur.fetchone()
        if not row:
            return {"auth_status": "not_found"}

        if row.get("trang_thai") != "ACTIVE" or row.get("khoa_den") is not None:
            return {"auth_status": "inactive"}

        if not row.get("password_ok"):
            return {"auth_status": "bad_password"}

        cur.execute(
            """
            update nguoi_dung
            set lan_dang_nhap_cuoi = now(),
                updated_at = now()
            where id = %s
            """,
            (row["id"],),
        )
        cur.execute(
            """
            update xac_thuc_nguoi_dung
            set so_lan_dang_nhap_sai = 0,
                updated_at = now()
            where nguoi_dung_id = %s
            """,
            (row["id"],),
        )

    role_code = row.get("ma_vai_tro")
    status_code = row.get("trang_thai")
    return {
        "auth_status": "ok",
        "username": row.get("ten_dang_nhap"),
        "name": row.get("ho_ten"),
        "role": role_label_map.get(role_code, row.get("ten_vai_tro") or role_code or "Nhân viên"),
        "unit": "",
        "email": row.get("email") or "",
        "status": status_label_map.get(status_code, "Tạm khóa"),
        "phone": row.get("so_dien_thoai") or "",
        "permissions": row.get("permissions") or [],
    }


def verify_admin_user_from_db(username):
    if not username:
        return {"status": "not_found"}

    with db_cursor() as cur:
        cur.execute(
            """
            select
              nd.id,
              nd.ten_dang_nhap,
              nd.trang_thai,
              bool_or(vt.ma_vai_tro = 'ADMIN') as has_admin_role,
              bool_or(q.ma_quyen = 'usermgmt') as has_admin_permission
            from nguoi_dung nd
            left join vai_tro_nguoi_dung vtnd
              on vtnd.nguoi_dung_id = nd.id
             and vtnd.dang_hoat_dong = true
             and (vtnd.ngay_ket_thuc is null or vtnd.ngay_ket_thuc > now())
            left join vai_tro vt
              on vt.id = vtnd.vai_tro_id
             and vt.deleted_at is null
             and vt.trang_thai = 'ACTIVE'
            left join vai_tro_quyen vtq
              on vtq.vai_tro_id = vt.id
             and vtq.duoc_phep = true
            left join quyen q
              on q.id = vtq.quyen_id
             and q.trang_thai = 'ACTIVE'
            where nd.deleted_at is null
              and nd.ten_dang_nhap = %s
            group by nd.id
            limit 1
            """,
            (username,),
        )
        row = cur.fetchone()

    if not row:
        return {"status": "not_found"}
    if row.get("trang_thai") != "ACTIVE":
        return {"status": "inactive"}
    if row.get("has_admin_role") or row.get("has_admin_permission"):
        return {"status": "ok", "username": row.get("ten_dang_nhap")}
    return {"status": "forbidden"}


def _next_camera_stream_key(cur):
    cur.execute(
        """
        select stream_key
        from camera
        where stream_key ~ '^cam_huyen_[0-9]+$'
        """
    )
    max_number = 0
    for row in cur.fetchall():
        match = re.search(r"(\d+)$", row["stream_key"] or "")
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"cam_huyen_{max_number + 1:02d}"


def _find_area_id(cur, zone):
    if not zone:
        return None
    cur.execute(
        """
        select id
        from khu_vuc
        where deleted_at is null
          and (
            lower(ten_khu_vuc) = lower(%s)
            or lower(ma_khu_vuc) = lower(%s)
          )
        order by id
        limit 1
        """,
        (zone, zone),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def _split_camera_model(model_text):
    model_text = (model_text or "").strip()
    if not model_text:
        return None, None
    if " " in model_text:
        manufacturer, model = model_text.split(" ", 1)
        return manufacturer, model
    return model_text, model_text


def create_camera_for_ui(camera_input, sync_stream_callback=None):
    with db_cursor(commit=True) as cur:
        stream_key = _next_camera_stream_key(cur)
        area_id = _find_area_id(cur, camera_input.zone)
        rtsp_url = (
            f"rtsp://{camera_input.user}:{camera_input.password}"
            f"@{camera_input.ip}:2004/Streaming/Channels/102"
        )
        manufacturer, model = _split_camera_model(camera_input.model)

        cur.execute(
            """
            select coalesce(max(thu_tu_hien_thi), 0) + 1 as next_index
            from camera
            where deleted_at is null
            """
        )
        next_index = cur.fetchone()["next_index"] or 1

        cur.execute(
            """
            insert into camera (
              khu_vuc_id, ma_camera, ten_camera, hang_san_xuat, model,
              loai_nguon, stream_key, thu_tu_hien_thi, trang_thai_hien_tai,
              bat_ai, bat_ghi_hinh
            ) values (%s, %s, %s, %s, %s, 'RTSP', %s, %s, 'UNKNOWN', false, true)
            returning id
            """,
            (
                area_id,
                stream_key,
                camera_input.name,
                manufacturer,
                model,
                stream_key,
                next_index,
            ),
        )
        camera_db_id = cur.fetchone()["id"]

        cur.execute(
            """
            insert into ket_noi_camera (
              camera_id, dia_chi_ip, cong_rtsp, duong_dan_rtsp,
              ten_dang_nhap, mat_khau_ma_hoa, rtsp_transport,
              go2rtc_stream_name, trang_thai_ket_noi
            ) values (%s, %s::inet, 2004, %s, %s, %s, 'tcp', %s, 'UNKNOWN')
            """,
            (
                camera_db_id,
                camera_input.ip,
                rtsp_url,
                camera_input.user,
                camera_input.password,
                stream_key,
            ),
        )

        cur.execute(
            """
            insert into thong_so_ky_thuat_camera (
              camera_id, do_phan_giai, fps, bitrate_kbps, codec
            ) values (%s, '1920x1080', 25, 4096, 'H264')
            """,
            (camera_db_id,),
        )

        cur.execute(
            """
            insert into lap_dat_camera (
              camera_id, khu_vuc_id, vi_tri_lap_dat, toa_nha
            ) values (%s, %s, %s, %s)
            """,
            (camera_db_id, area_id, camera_input.loc, camera_input.zone),
        )

        cur.execute(
            """
            insert into nhat_ky_camera (camera_id, hanh_dong, noi_dung, muc_do)
            values (%s, 'CREATE', %s, 'INFO')
            """,
            (camera_db_id, f"Tao camera {camera_input.name} tu API"),
        )

        cur.execute(
            """
            insert into nhat_ky_he_thong (
              hanh_dong, ten_bang, ban_ghi_id, gia_tri_moi
            ) values ('CREATE', 'camera', %s, %s::jsonb)
            """,
            (
                str(camera_db_id),
                psycopg2.extras.Json(
                    {
                        "id": stream_key,
                        "name": camera_input.name,
                        "ip": camera_input.ip,
                        "model": camera_input.model,
                        "zone": camera_input.zone,
                        "loc": camera_input.loc,
                    }
                ),
            ),
        )

        new_camera = {
            "id": stream_key,
            "db_id": camera_db_id,
            "index": next_index,
            "name": camera_input.name,
            "ip": camera_input.ip,
            "model": camera_input.model,
            "zone": camera_input.zone,
            "loc": camera_input.loc,
            "type": "video",
            "src": f"{GO2RTC_BASE_URL}/stream.html?src={stream_key}&mode=webrtc",
            "tag": "Live",
            "status": "offline",
            "resolution": "1920x1080",
            "fps": 25,
            "bitrate": 4096,
            "codec": "H264",
        }

        if sync_stream_callback:
            sync_stream_callback(stream_key, rtsp_url)

        return new_camera


def update_camera_for_ui(cam_id, updates, sync_stream_callback=None):
    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            select
              c.id,
              c.stream_key,
              c.ma_camera,
              c.ten_camera,
              c.hang_san_xuat,
              c.model,
              c.khu_vuc_id,
              c.trang_thai_hien_tai,
              host(knc.dia_chi_ip) as dia_chi_ip,
              knc.cong_rtsp,
              knc.duong_dan_rtsp,
              knc.ten_dang_nhap,
              knc.mat_khau_ma_hoa,
              tsk.do_phan_giai,
              tsk.fps,
              tsk.bitrate_kbps,
              tsk.codec,
              ldc.vi_tri_lap_dat,
              ldc.toa_nha,
              ldc.tang,
              ldc.vi_do,
              ldc.kinh_do
            from camera c
            left join ket_noi_camera knc on knc.camera_id = c.id
            left join thong_so_ky_thuat_camera tsk on tsk.camera_id = c.id
            left join lap_dat_camera ldc on ldc.camera_id = c.id
            where c.deleted_at is null
              and (c.stream_key = %s or c.ma_camera = %s)
            limit 1
            """,
            (cam_id, cam_id),
        )
        camera = cur.fetchone()
        if not camera:
            return None

        camera_id = camera["id"]
        stream_key = camera["stream_key"]
        old_value = {
            "stream_key": stream_key,
            "name": camera.get("ten_camera"),
            "ip": camera.get("dia_chi_ip"),
            "model": " ".join(
                part for part in [camera.get("hang_san_xuat"), camera.get("model")] if part
            ),
            "zone": camera.get("toa_nha"),
            "loc": camera.get("vi_tri_lap_dat"),
            "user": camera.get("ten_dang_nhap"),
            "rtsp_url": camera.get("duong_dan_rtsp"),
            "resolution": camera.get("do_phan_giai"),
            "fps": camera.get("fps"),
            "bitrate": camera.get("bitrate_kbps"),
            "codec": camera.get("codec"),
            "lat": camera.get("vi_do"),
            "lng": camera.get("kinh_do"),
        }

        area_id = camera["khu_vuc_id"]
        if "zone" in updates:
            area_id = _find_area_id(cur, updates.get("zone")) or area_id

        camera_sets = []
        camera_params = []
        if "name" in updates:
            camera_sets.append("ten_camera = %s")
            camera_params.append(updates.get("name"))
        if "model" in updates:
            manufacturer, model = _split_camera_model(updates.get("model"))
            camera_sets.extend(["hang_san_xuat = %s", "model = %s"])
            camera_params.extend([manufacturer, model])
        if "zone" in updates:
            camera_sets.append("khu_vuc_id = %s")
            camera_params.append(area_id)
        if camera_sets:
            camera_sets.append("updated_at = now()")
            cur.execute(
                f"update camera set {', '.join(camera_sets)} where id = %s",
                camera_params + [camera_id],
            )

        ip = updates.get("ip", camera.get("dia_chi_ip"))
        user = updates.get("user", camera.get("ten_dang_nhap"))
        password = updates.get("password", camera.get("mat_khau_ma_hoa"))
        rtsp_changed = any(key in updates for key in ("ip", "user", "password"))
        rtsp_url = camera.get("duong_dan_rtsp")
        if rtsp_changed:
            port = camera.get("cong_rtsp") or 2004
            rtsp_url = f"rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/102"

        connection_sets = []
        connection_params = []
        if "ip" in updates:
            connection_sets.append("dia_chi_ip = %s::inet")
            connection_params.append(ip)
        if "user" in updates:
            connection_sets.append("ten_dang_nhap = %s")
            connection_params.append(user)
        if "password" in updates:
            connection_sets.append("mat_khau_ma_hoa = %s")
            connection_params.append(password)
        if rtsp_changed:
            connection_sets.append("duong_dan_rtsp = %s")
            connection_params.append(rtsp_url)
        if connection_sets:
            connection_sets.append("updated_at = now()")
            cur.execute(
                f"update ket_noi_camera set {', '.join(connection_sets)} where camera_id = %s",
                connection_params + [camera_id],
            )

        tech_sets = []
        tech_params = []
        if "resolution" in updates:
            tech_sets.append("do_phan_giai = %s")
            tech_params.append(updates.get("resolution"))
        if "fps" in updates:
            tech_sets.append("fps = %s")
            tech_params.append(updates.get("fps"))
        if "bitrate" in updates:
            tech_sets.append("bitrate_kbps = %s")
            tech_params.append(updates.get("bitrate"))
        if "codec" in updates:
            tech_sets.append("codec = %s")
            tech_params.append(updates.get("codec"))
        if tech_sets:
            tech_sets.append("updated_at = now()")
            cur.execute(
                f"update thong_so_ky_thuat_camera set {', '.join(tech_sets)} where camera_id = %s",
                tech_params + [camera_id],
            )

        location_sets = []
        location_params = []
        if "zone" in updates:
            location_sets.extend(["khu_vuc_id = %s", "toa_nha = %s"])
            location_params.extend([area_id, updates.get("zone")])
        if "loc" in updates:
            location_sets.append("vi_tri_lap_dat = %s")
            location_params.append(updates.get("loc"))
        if "lat" in updates:
            location_sets.append("vi_do = %s")
            location_params.append(updates.get("lat"))
        if "lng" in updates:
            location_sets.append("kinh_do = %s")
            location_params.append(updates.get("lng"))
        if location_sets:
            location_sets.append("updated_at = now()")
            cur.execute(
                f"update lap_dat_camera set {', '.join(location_sets)} where camera_id = %s",
                location_params + [camera_id],
            )
            if cur.rowcount == 0:
                cur.execute(
                    """
                    insert into lap_dat_camera (
                      camera_id, khu_vuc_id, vi_tri_lap_dat, toa_nha, vi_do, kinh_do
                    ) values (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        camera_id,
                        area_id,
                        updates.get("loc"),
                        updates.get("zone"),
                        updates.get("lat"),
                        updates.get("lng"),
                    ),
                )

        if rtsp_changed and sync_stream_callback:
            sync_stream_callback(stream_key, rtsp_url)

        cur.execute(
            """
            insert into nhat_ky_camera (camera_id, hanh_dong, noi_dung, muc_do)
            values (%s, 'UPDATE', %s, 'INFO')
            """,
            (camera_id, f"Cap nhat camera {stream_key} tu API"),
        )
        cur.execute(
            """
            insert into nhat_ky_he_thong (
              hanh_dong, ten_bang, ban_ghi_id, gia_tri_cu, gia_tri_moi
            ) values ('UPDATE', 'camera', %s, %s::jsonb, %s::jsonb)
            """,
            (
                str(camera_id),
                psycopg2.extras.Json(old_value, dumps=lambda obj: json.dumps(obj, default=str)),
                psycopg2.extras.Json(updates, dumps=lambda obj: json.dumps(obj, default=str)),
            ),
        )

    return next((cam for cam in list_cameras_for_ui() if cam["id"] == stream_key), None)


def update_camera_location_for_ui(cam_id, lat, lng):
    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            select id, khu_vuc_id
            from camera
            where deleted_at is null
              and (stream_key = %s or ma_camera = %s)
            limit 1
            """,
            (cam_id, cam_id),
        )
        camera = cur.fetchone()
        if not camera:
            return False

        cur.execute(
            """
            update lap_dat_camera
            set vi_do = %s,
                kinh_do = %s
            where camera_id = %s
            """,
            (lat, lng, camera["id"]),
        )
        if cur.rowcount == 0:
            cur.execute(
                """
                insert into lap_dat_camera (camera_id, khu_vuc_id, vi_do, kinh_do)
                values (%s, %s, %s, %s)
                """,
                (camera["id"], camera["khu_vuc_id"], lat, lng),
            )
        return True


def soft_delete_camera_for_ui(cam_id, remove_stream_callback=None):
    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            select id, stream_key, ma_camera, ten_camera, trang_thai_hien_tai, deleted_at
            from camera
            where deleted_at is null
              and (stream_key = %s or ma_camera = %s)
            limit 1
            """,
            (cam_id, cam_id),
        )
        camera = cur.fetchone()
        if not camera:
            return None

        old_value = dict(camera)
        cur.execute(
            """
            update camera
            set deleted_at = now(),
                updated_at = now(),
                trang_thai_hien_tai = 'DELETED',
                bat_ghi_hinh = false
            where id = %s
            """,
            (camera["id"],),
        )
        cur.execute(
            """
            update ket_noi_camera
            set trang_thai_ket_noi = 'DISABLED',
                updated_at = now()
            where camera_id = %s
            """,
            (camera["id"],),
        )
        cur.execute(
            """
            insert into nhat_ky_camera (camera_id, hanh_dong, noi_dung, muc_do)
            values (%s, 'DELETE', %s, 'INFO')
            """,
            (camera["id"], f"Soft delete camera {camera['stream_key']} tu API"),
        )
        cur.execute(
            """
            insert into nhat_ky_he_thong (
              hanh_dong, ten_bang, ban_ghi_id, gia_tri_cu, gia_tri_moi
            ) values ('DELETE', 'camera', %s, %s::jsonb, %s::jsonb)
            """,
            (
                str(camera["id"]),
                psycopg2.extras.Json(old_value),
                psycopg2.extras.Json(
                    {
                        "stream_key": camera["stream_key"],
                        "deleted_at": "now()",
                        "trang_thai_hien_tai": "DELETED",
                        "bat_ghi_hinh": False,
                    }
                ),
            ),
        )
        if remove_stream_callback:
            remove_stream_callback(camera["stream_key"])
        return camera["stream_key"]


def upsert_recording_segment(camera, file_path, start_time, end_time, duration_seconds, file_size):
    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            insert into doan_video (
              camera_id, loai_doan, duong_dan_video, bat_dau_luc, ket_thuc_luc,
              thoi_luong_giay, dung_luong, mime_type, codec, do_phan_giai, fps,
              trang_thai
            ) values (%s, 'RAW_RECORDING', %s, %s, %s, %s, %s, 'video/mp4', %s, %s, %s, 'READY')
            on conflict (duong_dan_video) do update set
              ket_thuc_luc = excluded.ket_thuc_luc,
              thoi_luong_giay = excluded.thoi_luong_giay,
              dung_luong = excluded.dung_luong,
              trang_thai = 'READY',
              updated_at = now()
            returning id
            """,
            (
                camera["db_id"],
                os.path.abspath(file_path),
                start_time,
                end_time,
                duration_seconds,
                file_size,
                camera.get("codec"),
                camera.get("resolution"),
                camera.get("fps"),
            ),
        )
        return cur.fetchone()["id"]


def list_recording_enabled_cameras():
    with db_cursor() as cur:
        cur.execute(
            """
            select
              c.id as db_id,
              c.stream_key,
              c.ten_camera as name,
              c.bat_ghi_hinh,
              knc.duong_dan_rtsp as rtsp_url,
              tsk.codec,
              tsk.do_phan_giai as resolution,
              tsk.fps
            from camera c
            join ket_noi_camera knc on knc.camera_id = c.id
            left join thong_so_ky_thuat_camera tsk on tsk.camera_id = c.id
            where c.deleted_at is null
              and c.bat_ghi_hinh = true
              and knc.duong_dan_rtsp is not null
              and trim(knc.duong_dan_rtsp) <> ''
            order by c.stream_key
            """
        )
        return [dict(row) for row in cur.fetchall()]


def search_recording_segments(camera_id=None, zone=None, loc=None, from_time=None, to_time=None):
    where = ["dv.deleted_at is null", "dv.dung_luong >= 1048576"]
    params = []
    if camera_id:
        where.append("c.stream_key = %s")
        params.append(camera_id)
    if zone:
        where.append("(kv.ten_khu_vuc = %s or ldc.toa_nha = %s)")
        params.extend([zone, zone])
    if loc:
        where.append("ldc.vi_tri_lap_dat = %s")
        params.append(loc)
    if from_time:
        where.append("dv.ket_thuc_luc >= %s")
        params.append(from_time)
    if to_time:
        where.append("dv.bat_dau_luc <= %s")
        params.append(to_time)

    sql = f"""
        select
          dv.id,
          c.stream_key as camera_id,
          c.ten_camera as camera_name,
          coalesce(kv.ten_khu_vuc, ldc.toa_nha, '-') as zone,
          coalesce(ldc.vi_tri_lap_dat, '-') as loc,
          dv.duong_dan_video as file_path,
          dv.bat_dau_luc as start_time,
          dv.ket_thuc_luc as end_time,
          dv.thoi_luong_giay as duration_seconds,
          dv.dung_luong as file_size,
          lower(dv.trang_thai) as status
        from doan_video dv
        join camera c on c.id = dv.camera_id
        left join khu_vuc kv on kv.id = c.khu_vuc_id
        left join lap_dat_camera ldc on ldc.camera_id = c.id
        where {' and '.join(where)}
        order by dv.bat_dau_luc asc
        limit 200
    """
    with db_cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(row) for row in cur.fetchall()]

    for row in rows:
        row["start_time"] = row["start_time"].isoformat()
        row["end_time"] = row["end_time"].isoformat()
        row["stream_url"] = f"http://127.0.0.1:8000/api/playback/file/{row['id']}"
        row["download_url"] = row["stream_url"]
    return rows


def get_recording_file_path(segment_id):
    with db_cursor() as cur:
        cur.execute(
            "select duong_dan_video from doan_video where id = %s and deleted_at is null",
            (segment_id,),
        )
        row = cur.fetchone()
    return row["duong_dan_video"] if row else None


def _alert_severity_label(value):
    labels = {
        "LOW": "Thap",
        "MEDIUM": "Trung binh",
        "HIGH": "Cao",
        "CRITICAL": "Khan cap",
    }
    return labels.get(str(value or "").upper(), value or "-")


def _alert_status_label(value):
    labels = {
        "NEW": "Moi",
        "PROCESSING": "Dang xu ly",
        "ACKNOWLEDGED": "Da tiep nhan",
        "CLOSED": "Da dong",
        "RESOLVED": "Da xu ly",
        "IGNORED": "Bo qua",
    }
    return labels.get(str(value or "").upper(), value or "-")


def _normalize_alert_row(row):
    if not row:
        return None
    data = dict(row)
    severity = str(data.get("severity") or "").upper()
    status = str(data.get("status") or "").upper()
    occurred_at = data.get("occurred_at")
    updated_at = data.get("updated_at")
    return {
        "id": data["id"],
        "code": f"AL-{int(data['id']):06d}",
        "title": data.get("title") or f"Canh bao #{data['id']}",
        "description": data.get("description") or "",
        "severity": severity,
        "severity_label": _alert_severity_label(severity),
        "status": status,
        "status_label": _alert_status_label(status),
        "camera_id": data.get("camera_id"),
        "camera_db_id": data.get("camera_db_id"),
        "camera_name": data.get("camera_name") or "-",
        "zone": data.get("zone") or "-",
        "location": data.get("location") or "-",
        "occurred_at": occurred_at.isoformat() if occurred_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def list_alerts_for_ui(status=None, severity=None, camera_id=None, from_time=None, to_time=None):
    where = ["cb.deleted_at is null"]
    params = []
    if status and status != "all":
        where.append("upper(cb.trang_thai_hien_tai) = %s")
        params.append(str(status).upper())
    if severity and severity != "all":
        where.append("upper(cb.muc_do) = %s")
        params.append(str(severity).upper())
    if camera_id and camera_id != "all":
        where.append("(c.stream_key = %s or c.ma_camera = %s)")
        params.extend([camera_id, camera_id])
    if from_time:
        where.append("cb.phat_sinh_luc >= %s")
        params.append(from_time)
    if to_time:
        where.append("cb.phat_sinh_luc <= %s")
        params.append(to_time)

    sql = f"""
        select
          cb.id,
          cb.muc_do as severity,
          cb.trang_thai_hien_tai as status,
          cb.mo_ta as description,
          coalesce(lsk.ten_loai_su_kien, cb.mo_ta, 'Canh bao') as title,
          cb.phat_sinh_luc as occurred_at,
          cb.updated_at,
          c.id as camera_db_id,
          c.stream_key as camera_id,
          c.ten_camera as camera_name,
          coalesce(kv.ten_khu_vuc, ldc.toa_nha, '-') as zone,
          coalesce(ldc.vi_tri_lap_dat, '-') as location
        from canh_bao cb
        join camera c on c.id = cb.camera_id
        left join khu_vuc kv on kv.id = cb.khu_vuc_id
        left join lap_dat_camera ldc on ldc.camera_id = c.id
        left join su_kien_phat_hien sk on sk.id = cb.su_kien_phat_hien_id
        left join loai_su_kien lsk on lsk.id = sk.loai_su_kien_id
        where {' and '.join(where)}
        order by cb.phat_sinh_luc desc
        limit 200
    """
    with db_cursor() as cur:
        cur.execute(sql, params)
        items = [_normalize_alert_row(row) for row in cur.fetchall()]
        cur.execute(
            """
            select
              count(*) as total,
              count(*) filter (where upper(trang_thai_hien_tai) = 'NEW') as new,
              count(*) filter (where upper(trang_thai_hien_tai) in ('PROCESSING', 'ACKNOWLEDGED')) as processing,
              count(*) filter (where upper(trang_thai_hien_tai) in ('CLOSED', 'RESOLVED')) as closed,
              count(*) filter (where upper(muc_do) in ('HIGH', 'CRITICAL')) as high
            from canh_bao
            where deleted_at is null
            """
        )
        summary = dict(cur.fetchone())
    return {"items": items, "summary": summary}


def get_alert_detail_for_ui(alert_id):
    with db_cursor() as cur:
        cur.execute(
            """
            select
              cb.id,
              cb.muc_do as severity,
              cb.trang_thai_hien_tai as status,
              cb.mo_ta as description,
              coalesce(lsk.ten_loai_su_kien, cb.mo_ta, 'Canh bao') as title,
              cb.phat_sinh_luc as occurred_at,
              cb.updated_at,
              c.id as camera_db_id,
              c.stream_key as camera_id,
              c.ten_camera as camera_name,
              coalesce(kv.ten_khu_vuc, ldc.toa_nha, '-') as zone,
              coalesce(ldc.vi_tri_lap_dat, '-') as location
            from canh_bao cb
            join camera c on c.id = cb.camera_id
            left join khu_vuc kv on kv.id = cb.khu_vuc_id
            left join lap_dat_camera ldc on ldc.camera_id = c.id
            left join su_kien_phat_hien sk on sk.id = cb.su_kien_phat_hien_id
            left join loai_su_kien lsk on lsk.id = sk.loai_su_kien_id
            where cb.id = %s and cb.deleted_at is null
            """,
            (alert_id,),
        )
        alert = _normalize_alert_row(cur.fetchone())
        if not alert:
            return None

        cur.execute(
            """
            select
              lstt.id,
              'status' as type,
              lstt.trang_thai as status,
              lstt.ghi_chu as note,
              nd.ten_dang_nhap as username,
              lstt.bat_dau_luc as occurred_at
            from lich_su_trang_thai_canh_bao lstt
            left join nguoi_dung nd on nd.id = lstt.nguoi_thuc_hien_id
            where lstt.canh_bao_id = %s
            union all
            select
              ttxl.id,
              'action' as type,
              ttxl.hanh_dong as status,
              ttxl.noi_dung as note,
              nd.ten_dang_nhap as username,
              ttxl.thoi_diem as occurred_at
            from tien_trinh_xu_ly_canh_bao ttxl
            left join nguoi_dung nd on nd.id = ttxl.nguoi_thuc_hien_id
            where ttxl.canh_bao_id = %s
            order by occurred_at asc
            """,
            (alert_id, alert_id),
        )
        timeline = []
        for row in cur.fetchall():
            item = dict(row)
            item["occurred_at"] = item["occurred_at"].isoformat() if item.get("occurred_at") else None
            item["status_label"] = _alert_status_label(item.get("status"))
            timeline.append(item)

        cur.execute(
            """
            select tbc.id, tbc.loai_tep as file_type, tbc.ten_tep as file_name,
                   tbc.duong_dan as path, tbc.mime_type, tbc.dung_luong as size
            from bang_chung bc
            join tep_bang_chung tbc on tbc.bang_chung_id = bc.id
            where bc.canh_bao_id = %s
            order by tbc.created_at desc
            limit 20
            """,
            (alert_id,),
        )
        evidence = [dict(row) for row in cur.fetchall()]

    alert["timeline"] = timeline
    alert["evidence"] = evidence
    return alert


def update_alert_status_for_ui(alert_id, new_status, note=None, username=None):
    allowed = {"NEW", "PROCESSING", "ACKNOWLEDGED", "CLOSED", "RESOLVED", "IGNORED"}
    status_value = str(new_status or "").upper()
    if status_value not in allowed:
        raise ValueError("Trang thai canh bao khong hop le")

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            select id, trang_thai_hien_tai
            from canh_bao
            where id = %s and deleted_at is null
            for update
            """,
            (alert_id,),
        )
        alert = cur.fetchone()
        if not alert:
            return None

        actor_id = _admin_actor_id(cur, username) if username else None
        old_status = alert["trang_thai_hien_tai"]
        cur.execute(
            "update canh_bao set trang_thai_hien_tai = %s, updated_at = now() where id = %s",
            (status_value, alert_id),
        )
        cur.execute(
            """
            update lich_su_trang_thai_canh_bao
            set ket_thuc_luc = now()
            where canh_bao_id = %s and ket_thuc_luc is null
            """,
            (alert_id,),
        )
        cur.execute(
            """
            insert into lich_su_trang_thai_canh_bao (
              canh_bao_id, trang_thai, bat_dau_luc, nguoi_thuc_hien_id, ghi_chu
            )
            values (%s, %s, now(), %s, %s)
            """,
            (alert_id, status_value, actor_id, note),
        )
        cur.execute(
            """
            insert into tien_trinh_xu_ly_canh_bao (
              canh_bao_id, hanh_dong, noi_dung, nguoi_thuc_hien_id
            )
            values (%s, %s, %s, %s)
            """,
            (alert_id, "UPDATE_STATUS", f"{old_status} -> {status_value}", actor_id),
        )
    return get_alert_detail_for_ui(alert_id)


def get_reports_summary_from_db(from_time=None, to_time=None):
    recording_where = ["dv.deleted_at is null"]
    recording_params = []
    alert_where = ["cb.deleted_at is null"]
    alert_params = []
    if from_time:
        recording_where.append("dv.bat_dau_luc >= %s")
        recording_params.append(from_time)
        alert_where.append("cb.phat_sinh_luc >= %s")
        alert_params.append(from_time)
    if to_time:
        recording_where.append("dv.bat_dau_luc <= %s")
        recording_params.append(to_time)
        alert_where.append("cb.phat_sinh_luc <= %s")
        alert_params.append(to_time)

    with db_cursor() as cur:
        cur.execute(
            """
            select
              count(*) as total,
              count(*) filter (where trang_thai_hien_tai = 'ONLINE') as online,
              count(*) filter (where trang_thai_hien_tai <> 'ONLINE') as offline,
              count(*) filter (where bat_ghi_hinh = true) as recording_enabled
            from camera
            where deleted_at is null
            """
        )
        camera = dict(cur.fetchone())

        cur.execute(
            """
            select
              count(*) as total,
              coalesce(sum(dung_luong), 0) as total_size,
              coalesce(avg(thoi_luong_giay), 0) as avg_duration_seconds
            from doan_video dv
            where {}
            """.format(" and ".join(recording_where)),
            recording_params,
        )
        recording = dict(cur.fetchone())

        cur.execute(
            """
            select
              dv.bat_dau_luc::date as day,
              count(*) as segments,
              coalesce(sum(dv.dung_luong), 0) as size
            from doan_video dv
            where {}
            group by dv.bat_dau_luc::date
            order by day asc
            """.format(" and ".join(recording_where)),
            recording_params,
        )
        recording_by_day = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            select
              upper(coalesce(cb.trang_thai_hien_tai, 'UNKNOWN')) as status,
              count(*) as total
            from canh_bao cb
            where {}
            group by upper(coalesce(cb.trang_thai_hien_tai, 'UNKNOWN'))
            order by status asc
            """.format(" and ".join(alert_where)),
            alert_params,
        )
        alerts_by_status = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            select
              count(*) as total,
              count(*) filter (where upper(trang_thai_hien_tai) = 'NEW') as new,
              count(*) filter (where upper(trang_thai_hien_tai) in ('PROCESSING', 'ACKNOWLEDGED')) as processing,
              count(*) filter (where upper(trang_thai_hien_tai) in ('CLOSED', 'RESOLVED')) as closed
            from canh_bao cb
            where {}
            """.format(" and ".join(alert_where)),
            alert_params,
        )
        alerts = dict(cur.fetchone())

        cur.execute(
            """
            select
              kv.id,
              kv.ma_khu_vuc as code,
              kv.ten_khu_vuc as name,
              count(distinct c.id) filter (where c.deleted_at is null) as cameras,
              count(distinct c.id) filter (where c.deleted_at is null and c.trang_thai_hien_tai = 'ONLINE') as online,
              count(distinct c.id) filter (where c.deleted_at is null and c.trang_thai_hien_tai <> 'ONLINE') as offline,
              count(distinct cb.id) filter (where cb.deleted_at is null) as alerts
            from khu_vuc kv
            left join camera c on c.khu_vuc_id = kv.id and c.deleted_at is null
            left join canh_bao cb on cb.khu_vuc_id = kv.id and {}
            where kv.deleted_at is null
            group by kv.id, kv.ma_khu_vuc, kv.ten_khu_vuc
            order by kv.ten_khu_vuc asc
            """.format(" and ".join(alert_where)),
            alert_params,
        )
        areas = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            select
              c.stream_key as camera_id,
              c.ten_camera as camera_name,
              count(dv.id) as segments,
              coalesce(sum(dv.dung_luong), 0) as size
            from camera c
            left join doan_video dv on dv.camera_id = c.id and {}
            where c.deleted_at is null
            group by c.stream_key, c.ten_camera
            order by size desc, segments desc, camera_name asc
            limit 10
            """.format(" and ".join(recording_where)),
            recording_params,
        )
        recording_by_camera = [dict(row) for row in cur.fetchall()]

    for row in recording_by_day:
        row["day"] = row["day"].isoformat() if row.get("day") else None

    return {
        "filters": {
            "from_time": from_time.isoformat() if hasattr(from_time, "isoformat") else from_time,
            "to_time": to_time.isoformat() if hasattr(to_time, "isoformat") else to_time,
        },
        "camera": camera,
        "recording": recording,
        "recording_by_day": recording_by_day,
        "recording_by_camera": recording_by_camera,
        "alerts": alerts,
        "alerts_by_status": alerts_by_status,
        "areas": areas,
    }


def get_dashboard_summary_from_db():
    with db_cursor() as cur:
        cur.execute(
            """
            select
              count(*) as total,
              count(*) filter (where trang_thai_hien_tai = 'ONLINE') as online,
              count(*) filter (where trang_thai_hien_tai <> 'ONLINE') as offline,
              count(*) filter (where bat_ghi_hinh = true) as recording_enabled
            from camera
            where deleted_at is null
            """
        )
        camera = dict(cur.fetchone())

        cur.execute("select count(*) as total from khu_vuc where deleted_at is null")
        areas = dict(cur.fetchone())

        cur.execute("select count(*) as total from nguoi_dung where deleted_at is null")
        users = dict(cur.fetchone())

        cur.execute(
            """
            select count(*) as online
            from phien_dang_nhap
            where dang_hoat_dong = true
              and (het_han_luc is null or het_han_luc > now())
            """
        )
        users_online = dict(cur.fetchone())

        cur.execute(
            """
            select
              count(*) as total_segments,
              coalesce(sum(dung_luong), 0) as total_size,
              count(*) filter (where bat_dau_luc::date = current_date) as today_segments,
              coalesce(sum(dung_luong) filter (where bat_dau_luc::date = current_date), 0) as today_size
            from doan_video
            where deleted_at is null
            """
        )
        recording = dict(cur.fetchone())

        cur.execute(
            """
            select count(*) as today
            from canh_bao
            where deleted_at is null
              and phat_sinh_luc::date = current_date
            """
        )
        alerts = dict(cur.fetchone())

        cur.execute(
            """
            select count(*) as today
            from su_kien_phat_hien
            where phat_hien_luc::date = current_date
            """
        )
        ai_events = dict(cur.fetchone())

        cur.execute(
            """
            with days as (
              select generate_series(current_date - interval '6 day', current_date, interval '1 day')::date as day
            )
            select
              days.day,
              coalesce(count(cb.id), 0) as total
            from days
            left join canh_bao cb
              on cb.deleted_at is null
             and cb.phat_sinh_luc::date = days.day
            group by days.day
            order by days.day
            """
        )
        alert_chart = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            with days as (
              select generate_series(current_date - interval '6 day', current_date, interval '1 day')::date as day
            )
            select
              days.day,
              coalesce(count(sk.id), 0) as total
            from days
            left join su_kien_phat_hien sk
              on sk.phat_hien_luc::date = days.day
            group by days.day
            order by days.day
            """
        )
        ai_chart = [dict(row) for row in cur.fetchall()]

    return {
        "camera": camera,
        "areas": areas,
        "users": {"total": users["total"], "online": users_online["online"]},
        "recording": recording,
        "alerts": alerts,
        "ai_events": ai_events,
        "charts": {
            "alerts_7_days": alert_chart,
            "ai_events_7_days": ai_chart,
        },
    }


def get_dashboard_activity_from_db(limit=20):
    limit = max(1, min(int(limit or 20), 100))
    with db_cursor() as cur:
        cur.execute(
            """
            select *
            from (
              select
                'system' as source,
                nh.hanh_dong as action,
                coalesce(nh.ten_bang, 'he_thong') as target,
                nh.ban_ghi_id as record_id,
                nh.thoi_diem as occurred_at,
                'INFO' as level,
                concat(nh.hanh_dong, ' ', coalesce(nh.ten_bang, 'he_thong')) as title,
                coalesce(nh.ban_ghi_id, '') as subtitle
              from nhat_ky_he_thong nh
              union all
              select
                'camera' as source,
                nk.hanh_dong as action,
                coalesce(c.stream_key, 'camera') as target,
                c.stream_key as record_id,
                nk.thoi_diem as occurred_at,
                coalesce(nk.muc_do, 'INFO') as level,
                coalesce(nk.noi_dung, nk.hanh_dong) as title,
                coalesce(c.ten_camera, '') as subtitle
              from nhat_ky_camera nk
              left join camera c on c.id = nk.camera_id
            ) activity
            order by occurred_at desc
            limit %s
            """,
            (limit,),
        )
        rows = [dict(row) for row in cur.fetchall()]

    for row in rows:
        row["occurred_at"] = row["occurred_at"].isoformat()
    return rows
