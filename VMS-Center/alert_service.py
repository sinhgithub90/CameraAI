from datetime import datetime

from psycopg2.errors import UniqueViolation

from db import db_cursor


OPEN_STATUSES = {"NEW", "PROCESSING", "ACKNOWLEDGED"}
ALERT_STATUSES = {"NEW", "PROCESSING", "ACKNOWLEDGED", "RESOLVED", "CLOSED", "IGNORED"}
ALERT_SOURCES = {"HEALTH", "AI", "USER", "SYSTEM"}
ALERT_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
ALERT_TRANSITIONS = {
    "NEW": {"PROCESSING", "ACKNOWLEDGED", "IGNORED"},
    "PROCESSING": {"ACKNOWLEDGED", "RESOLVED", "CLOSED"},
    "ACKNOWLEDGED": {"RESOLVED", "CLOSED"},
    "RESOLVED": {"CLOSED"},
    "CLOSED": set(),
    "IGNORED": set(),
}


class AlertTransitionError(ValueError):
    def __init__(self, current_status, requested_status, allowed_transitions):
        self.current_status = current_status
        self.requested_status = requested_status
        self.allowed_transitions = sorted(allowed_transitions)
        super().__init__(f"Invalid alert transition: {current_status} -> {requested_status}")

    def to_dict(self):
        return {
            "message": str(self),
            "current_status": self.current_status,
            "requested_status": self.requested_status,
            "allowed_transitions": self.allowed_transitions,
        }


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

    def update_alert(
        self,
        alert_id,
        severity=None,
        title=None,
        description=None,
        status=None,
        note=None,
        username=None,
    ):
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
        requested_status = self._normalize_status(status) if status is not None else None

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
            current_status = self._normalize_status(alert["trang_thai_hien_tai"])
            actor_id = self._actor_id(cur, username)
            status_changed = False

            if requested_status is not None:
                if requested_status == current_status:
                    if not updates:
                        return {"status": "no_change", "alert_id": alert_id, "current_status": current_status}
                else:
                    self._validate_transition(current_status, requested_status)
                    updates.append("trang_thai_hien_tai = %s")
                    params.append(requested_status)
                    status_changed = True

            if updates:
                params.append(alert_id)
                cur.execute(
                    f"update canh_bao set {', '.join(updates)}, updated_at = now() where id = %s",
                    params,
                )

            if status_changed:
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
                    (alert_id, requested_status, actor_id, note),
                )

            if not updates:
                return {"status": "noop", "alert_id": alert_id}

            action = "UPDATE_STATUS" if status_changed else "UPDATE_ALERT"
            content = (
                f"{current_status} -> {requested_status}"
                if status_changed
                else note or "Alert updated"
            )
            cur.execute(
                """
                insert into tien_trinh_xu_ly_canh_bao (
                  canh_bao_id, hanh_dong, noi_dung, nguoi_thuc_hien_id
                )
                values (%s, %s, %s, %s)
                """,
                (alert_id, action, content, actor_id),
            )
        if requested_status is not None and requested_status == current_status and updates:
            return {"status": "no_change", "alert_id": alert_id, "current_status": current_status}
        return {"status": "updated", "alert_id": alert_id}

    def close_alert(self, alert_id, status="CLOSED", note=None, username=None):
        status = str(status or "CLOSED").upper()
        if status not in {"CLOSED", "RESOLVED"}:
            raise ValueError("close_alert chi nhan CLOSED hoac RESOLVED")
        result = self.update_alert(alert_id, status=status, note=note, username=username)
        if result.get("status") == "updated":
            return {"status": "closed", "alert_id": alert_id}
        return result

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

    def _normalize_status(self, status):
        value = str(status or "").upper()
        if value not in ALERT_STATUSES:
            raise ValueError("Trang thai canh bao khong hop le")
        return value

    def _validate_transition(self, current_status, requested_status):
        allowed = ALERT_TRANSITIONS.get(current_status, set())
        if requested_status not in allowed:
            raise AlertTransitionError(current_status, requested_status, allowed)

    def _actor_id(self, cur, username):
        if not username:
            return None
        cur.execute(
            """
            select id
            from nguoi_dung
            where ten_dang_nhap = %s and deleted_at is null
            limit 1
            """,
            (username,),
        )
        row = cur.fetchone()
        return row["id"] if row else None


alert_service = AlertService()
