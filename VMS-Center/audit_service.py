import copy
import ipaddress
import queue
import threading
from datetime import datetime, timedelta

from psycopg2.extras import Json

from db import db_cursor


SENSITIVE_KEYS = (
    "api_key",
    "authorization",
    "mat_khau",
    "password",
    "password_hash",
    "pass",
    "refresh",
    "secret",
    "token",
)


class AuditService:
    def __init__(self, max_queue_size=1000):
        self.queue = queue.Queue(maxsize=max_queue_size)
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.RLock()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._worker, daemon=True)
            self.thread.start()

    def shutdown(self):
        self.flush(timeout=3)
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def flush(self, timeout=5):
        deadline = datetime.now() + timedelta(seconds=timeout)
        while not self.queue.empty() and datetime.now() < deadline:
            threading.Event().wait(0.05)
        return self.queue.empty()

    def record(
        self,
        action,
        entity_type,
        entity_id=None,
        actor_username=None,
        actor_user_id=None,
        before=None,
        after=None,
        ip=None,
        user_agent=None,
    ):
        payload = self._build_payload(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_username=actor_username,
            actor_user_id=actor_user_id,
            before=before,
            after=after,
            ip=ip,
            user_agent=user_agent,
        )
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            try:
                self.record_sync(**payload)
            except Exception as exc:
                print(f"Audit write error: {exc}")

    def record_sync(self, **kwargs):
        payload = self._build_payload(**kwargs)
        if payload.get("actor_user_id") is None and payload.get("actor_username"):
            payload["actor_user_id"] = self._lookup_user_id(payload["actor_username"])
        with db_cursor(commit=True) as cur:
            cur.execute(
                """
                insert into audit_log (
                  actor_user_id, actor_username, action, entity_type, entity_id,
                  before_data, after_data, ip, user_agent
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s::inet, %s)
                returning id
                """,
                (
                    payload.get("actor_user_id"),
                    payload.get("actor_username"),
                    payload["action"],
                    payload["entity_type"],
                    payload.get("entity_id"),
                    Json(payload.get("before") or {}),
                    Json(payload.get("after") or {}),
                    payload.get("ip"),
                    payload.get("user_agent"),
                ),
            )
            return cur.fetchone()["id"]

    def list_logs(self, user=None, action=None, entity=None, date=None, page=1, page_size=50):
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 50), 200))
        where = []
        params = []
        if user:
            where.append("(actor_username ilike %s or actor_user_id::text = %s)")
            params.extend([f"%{user}%", str(user)])
        if action:
            where.append("action ilike %s")
            params.append(f"%{action}%")
        if entity:
            where.append("(entity_type ilike %s or coalesce(entity_id, '') ilike %s)")
            params.extend([f"%{entity}%", f"%{entity}%"])
        if date:
            start = datetime.fromisoformat(str(date))
            end = start + timedelta(days=1)
            where.append("created_at >= %s and created_at < %s")
            params.extend([start, end])

        where_sql = " where " + " and ".join(where) if where else ""
        offset = (page - 1) * page_size
        with db_cursor() as cur:
            cur.execute(f"select count(*) as total from audit_log{where_sql}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"""
                select *
                from audit_log
                {where_sql}
                order by created_at desc, id desc
                limit %s offset %s
                """,
                params + [page_size, offset],
            )
            items = [self._normalize_row(row) for row in cur.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def get_log(self, audit_id):
        with db_cursor() as cur:
            cur.execute("select * from audit_log where id = %s", (audit_id,))
            row = cur.fetchone()
        return self._normalize_row(row) if row else None

    def request_meta(self, request):
        if not request:
            return {"ip": None, "user_agent": None}
        client = getattr(request, "client", None)
        headers = getattr(request, "headers", {}) or {}
        return {
            "ip": getattr(client, "host", None),
            "user_agent": headers.get("user-agent") if hasattr(headers, "get") else None,
        }

    def _worker(self):
        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                payload = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.record_sync(**payload)
            except Exception as exc:
                print(f"Audit write error: {exc}")
            finally:
                self.queue.task_done()

    def _build_payload(
        self,
        action,
        entity_type,
        entity_id=None,
        actor_username=None,
        actor_user_id=None,
        before=None,
        after=None,
        ip=None,
        user_agent=None,
    ):
        return {
            "actor_user_id": actor_user_id,
            "actor_username": actor_username,
            "action": str(action or "").upper(),
            "entity_type": str(entity_type or "").upper(),
            "entity_id": str(entity_id) if entity_id is not None else None,
            "before": self.sanitize(before or {}),
            "after": self.sanitize(after or {}),
            "ip": self._normalize_ip(ip),
            "user_agent": user_agent,
        }

    def sanitize(self, value):
        value = copy.deepcopy(value)
        if isinstance(value, dict):
            clean = {}
            for key, item in value.items():
                key_text = str(key).lower()
                if any(sensitive in key_text for sensitive in SENSITIVE_KEYS):
                    clean[key] = "[REDACTED]"
                else:
                    clean[key] = self.sanitize(item)
            return clean
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        return value

    def _lookup_user_id(self, username):
        with db_cursor() as cur:
            cur.execute(
                """
                select id
                from nguoi_dung
                where ten_dang_nhap = %s
                limit 1
                """,
                (username,),
            )
            row = cur.fetchone()
        return row["id"] if row else None

    def _normalize_ip(self, value):
        if not value:
            return None
        try:
            return str(ipaddress.ip_address(str(value)))
        except ValueError:
            return None

    def _normalize_row(self, row):
        if not row:
            return None
        created_at = row.get("created_at")
        return {
            "id": row["id"],
            "actor_user_id": row.get("actor_user_id"),
            "actor_username": row.get("actor_username"),
            "action": row.get("action"),
            "entity_type": row.get("entity_type"),
            "entity_id": row.get("entity_id"),
            "before": row.get("before_data") or {},
            "after": row.get("after_data") or {},
            "ip": str(row.get("ip")) if row.get("ip") else None,
            "user_agent": row.get("user_agent"),
            "created_at": created_at.isoformat() if created_at else None,
        }


audit_service = AuditService()
