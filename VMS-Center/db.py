import os
import re
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


def create_camera_for_ui(camera_input, sync_stream_callback=None):
    with db_cursor(commit=True) as cur:
        stream_key = _next_camera_stream_key(cur)
        area_id = _find_area_id(cur, camera_input.zone)
        rtsp_url = (
            f"rtsp://{camera_input.user}:{camera_input.password}"
            f"@{camera_input.ip}:2004/Streaming/Channels/102"
        )
        model_text = camera_input.model.strip()
        manufacturer, model = (model_text.split(" ", 1) + [""])[:2] if " " in model_text else (model_text, model_text)

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
