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
        order by dv.bat_dau_luc desc
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
