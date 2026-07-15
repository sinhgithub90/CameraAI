import os
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
