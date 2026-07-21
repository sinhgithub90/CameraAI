from datetime import datetime

from psycopg2.errors import UniqueViolation

from db import db_cursor


OPEN_STATUSES = {"NEW", "PROCESSING", "ACKNOWLEDGED"}
ALERT_SOURCES = {"HEALTH", "AI", "USER", "SYSTEM"}
ALERT_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


class AlertService:
    def create_alert(
        self,
        source,
        severity,
        camera_id,
        khu_vuc_id=None,
        title=None,
        description=None,
        event_time=None,
        duplicate_key=None,
    ):
        source = self._normalize_source(source)
        severity = self._normalize_severity(severity)
        title = (title or "Canh bao").strip()
        description = (description or "").strip()
        event_time = event_time or datetime.now().astimezone()
        duplicate_key = duplicate_key or self._duplicate_key(source, camera_id, title)

        try:
            with db_cursor(commit=True) as cur:
                existing = self._find_open_duplicate(cur, duplicate_key)
                if existing:
                    self._ignore_duplicate_alert_with_cursor(cur, existing["id"], description)
                    return {"status": "duplicate_ignored", "alert_id": existing["id"], "created": False}

                cur.execute(
                    """
                    insert into canh_bao (
                      camera_id, khu_vuc_id, muc_do, trang_thai_hien_tai,
                      mo_ta, phat_sinh_luc, nguon, tieu_de, duplicate_key
                    )
                    values (%s, %s, %s, 'NEW', %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (camera_id, khu_vuc_id, severity, description, event_time, source, title, duplicate_key),
                )
                alert_id = cur.fetchone()["id"]
                cur.execute(
                    """
                    insert into lich_su_trang_thai_canh_bao (
                      canh_bao_id, trang_thai, bat_dau_luc, ghi_chu
                    )
                    values (%s, 'NEW', %s, %s)
                    """,
                    (alert_id, event_time, f"{source}: {title}"),
                )
                cur.execute(
                    """
                    insert into tien_trinh_xu_ly_canh_bao (
                      canh_bao_id, hanh_dong, noi_dung
                    )
                    values (%s, 'CREATE_ALERT', %s)
                    """,
                    (alert_id, f"{source}: {description or title}"),
                )
        except UniqueViolation:
            return self._handle_unique_duplicate(duplicate_key, description)
        return {"status": "created", "alert_id": alert_id, "created": True}

    def update_alert(self, alert_id, severity=None, title=None, description=None, status=None, note=None):
        updates = []
        params = []
        if severity is not None:
            updates.append("muc_do = %s")
            params.append(self._normalize_severity(severity))
        if title is not None:
            updates.append("tieu_de = %s")
            params.append(title)
        if description is not None:
            updates.append("mo_ta = %s")
            params.append(description)
        if status is not None:
            updates.append("trang_thai_hien_tai = %s")
            params.append(status)
        if not updates:
            return {"status": "noop", "alert_id": alert_id}

        with db_cursor(commit=True) as cur:
            cur.execute("select id from canh_bao where id = %s and deleted_at is null for update", (alert_id,))
            if not cur.fetchone():
                return {"status": "not_found"}
            params.append(alert_id)
            cur.execute(
                f"update canh_bao set {', '.join(updates)}, updated_at = now() where id = %s",
                params,
            )
            cur.execute(
                """
                insert into tien_trinh_xu_ly_canh_bao (
                  canh_bao_id, hanh_dong, noi_dung
                )
                values (%s, 'UPDATE_ALERT', %s)
                """,
                (alert_id, note or "Alert updated"),
            )
        return {"status": "updated", "alert_id": alert_id}

    def close_alert(self, alert_id, status="CLOSED", note=None):
        status = str(status or "CLOSED").upper()
        if status not in {"CLOSED", "RESOLVED"}:
            raise ValueError("close_alert chi nhan CLOSED hoac RESOLVED")
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
                return {"status": "not_found"}
            old_status = alert["trang_thai_hien_tai"]
            cur.execute(
                "update canh_bao set trang_thai_hien_tai = %s, updated_at = now() where id = %s",
                (status, alert_id),
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
                  canh_bao_id, trang_thai, bat_dau_luc, ghi_chu
                )
                values (%s, %s, now(), %s)
                """,
                (alert_id, status, note),
            )
            cur.execute(
                """
                insert into tien_trinh_xu_ly_canh_bao (
                  canh_bao_id, hanh_dong, noi_dung
                )
                values (%s, 'CLOSE_ALERT', %s)
                """,
                (alert_id, f"{old_status} -> {status}"),
            )
        return {"status": "closed", "alert_id": alert_id}

    def ignore_duplicate_alert(self, alert_id, note=None):
        with db_cursor(commit=True) as cur:
            self._ignore_duplicate_alert_with_cursor(cur, alert_id, note)
        return {"status": "duplicate_ignored", "alert_id": alert_id}

    def _ignore_duplicate_alert_with_cursor(self, cur, alert_id, note=None):
        cur.execute(
            """
            insert into tien_trinh_xu_ly_canh_bao (
              canh_bao_id, hanh_dong, noi_dung
            )
            values (%s, 'IGNORE_DUPLICATE_ALERT', %s)
            """,
            (alert_id, note or "Duplicate alert ignored"),
        )
        cur.execute("update canh_bao set updated_at = now() where id = %s", (alert_id,))

    def _find_open_duplicate(self, cur, duplicate_key):
        cur.execute(
            """
            select id
            from canh_bao
            where deleted_at is null
              and duplicate_key = %s
              and trang_thai_hien_tai = any(%s)
            order by phat_sinh_luc desc
            limit 1
            for update
            """,
            (duplicate_key, list(OPEN_STATUSES)),
        )
        return cur.fetchone()

    def _handle_unique_duplicate(self, duplicate_key, note=None):
        with db_cursor(commit=True) as cur:
            existing = self._find_open_duplicate(cur, duplicate_key)
            if not existing:
                raise
            self._ignore_duplicate_alert_with_cursor(cur, existing["id"], note)
            return {"status": "duplicate_ignored", "alert_id": existing["id"], "created": False}

    def _duplicate_key(self, source, camera_id, title):
        return f"{source}:camera:{camera_id}:{title}".lower()

    def _normalize_source(self, source):
        value = str(source or "SYSTEM").upper()
        if value not in ALERT_SOURCES:
            raise ValueError("Nguon alert khong hop le")
        return value

    def _normalize_severity(self, severity):
        value = str(severity or "MEDIUM").upper()
        if value not in ALERT_SEVERITIES:
            raise ValueError("Muc do alert khong hop le")
        return value


alert_service = AlertService()
