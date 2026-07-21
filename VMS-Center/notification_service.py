from db import db_cursor


NOTIFICATION_CHANNEL_IN_APP = "IN_APP"


class NotificationService:
    def create_notification(
        self,
        title,
        message,
        severity=None,
        alert_id=None,
        camera_id=None,
        recipient_user_ids=None,
        channel=NOTIFICATION_CHANNEL_IN_APP,
    ):
        title = (title or "Thong bao").strip()
        message = (message or "").strip()
        if not message:
            message = title
        with db_cursor(commit=True) as cur:
            recipients = recipient_user_ids or self._alert_recipients(cur)
            if not recipients:
                return {"status": "no_recipients", "notification_id": None, "recipient_count": 0}
            notification_id, notification_created = self._get_or_create_notification(
                cur, alert_id, title, message, channel
            )
            inserted_recipients = 0
            for user_id in recipients:
                cur.execute(
                    """
                    insert into nguoi_nhan_thong_bao (
                      thong_bao_id, nguoi_dung_id, trang_thai_nhan, da_doc
                    )
                    values (%s, %s, 'DELIVERED', false)
                    on conflict do nothing
                    """,
                    (notification_id, user_id),
                )
                inserted_recipients += cur.rowcount
            if notification_created:
                cur.execute(
                    """
                    insert into nhat_ky_thong_bao (
                      thong_bao_id, kenh, trang_thai_gui
                    )
                    values (%s, %s, 'CREATED')
                    """,
                    (notification_id, channel),
                )
        return {
            "status": "created" if notification_created else "existing",
            "notification_id": notification_id,
            "recipient_count": inserted_recipients,
        }

    def create_alert_notification(self, alert_id):
        with db_cursor() as cur:
            cur.execute(
                """
                select
                  cb.id,
                  cb.tieu_de,
                  cb.mo_ta,
                  cb.muc_do,
                  c.stream_key as camera_id,
                  c.ten_camera as camera_name
                from canh_bao cb
                left join camera c on c.id = cb.camera_id
                where cb.id = %s and cb.deleted_at is null
                limit 1
                """,
                (alert_id,),
            )
            alert = cur.fetchone()
        if not alert:
            return {"status": "alert_not_found", "notification_id": None, "recipient_count": 0}
        severity = str(alert.get("muc_do") or "MEDIUM").upper()
        title = alert.get("tieu_de") or f"Canh bao #{alert_id}"
        camera_name = alert.get("camera_name") or alert.get("camera_id") or "khong ro camera"
        message = alert.get("mo_ta") or f"{title} - {camera_name}"
        return self.create_notification(
            title=title,
            message=message,
            severity=severity,
            alert_id=alert_id,
            camera_id=alert.get("camera_id"),
        )

    def mark_read(self, username, notification_id):
        user_id = self._user_id(username)
        if not user_id:
            return {"status": "user_not_found"}
        with db_cursor(commit=True) as cur:
            cur.execute(
                """
                update nguoi_nhan_thong_bao
                set da_doc = true,
                    doc_luc = coalesce(doc_luc, now()),
                    trang_thai_nhan = 'READ'
                where id = %s
                  and nguoi_dung_id = %s
                returning id
                """,
                (notification_id, user_id),
            )
            row = cur.fetchone()
        return {"status": "success" if row else "not_found"}

    def mark_all_read(self, username):
        user_id = self._user_id(username)
        if not user_id:
            return {"status": "user_not_found", "updated": 0}
        with db_cursor(commit=True) as cur:
            cur.execute(
                """
                update nguoi_nhan_thong_bao
                set da_doc = true,
                    doc_luc = coalesce(doc_luc, now()),
                    trang_thai_nhan = 'READ'
                where nguoi_dung_id = %s
                  and da_doc = false
                """,
                (user_id,),
            )
            updated = cur.rowcount
        return {"status": "success", "updated": updated}

    def get_unread_count(self, username):
        user_id = self._user_id(username)
        if not user_id:
            return {"unread": 0}
        with db_cursor() as cur:
            cur.execute(
                """
                select count(*) as total
                from nguoi_nhan_thong_bao nntb
                join thong_bao tb on tb.id = nntb.thong_bao_id
                where nntb.nguoi_dung_id = %s
                  and nntb.da_doc = false
                """,
                (user_id,),
            )
            return {"unread": cur.fetchone()["total"]}

    def list_notifications(self, username, limit=50):
        user_id = self._user_id(username)
        if not user_id:
            return {"items": [], "unread": 0}
        limit = max(1, min(int(limit or 50), 100))
        with db_cursor() as cur:
            cur.execute(
                """
                select
                  nntb.id,
                  tb.tieu_de as title,
                  tb.noi_dung as message,
                  coalesce(cb.muc_do, 'MEDIUM') as severity,
                  tb.tao_luc as created_at,
                  nntb.da_doc as read,
                  tb.canh_bao_id as alert_id,
                  c.stream_key as camera_id
                from nguoi_nhan_thong_bao nntb
                join thong_bao tb on tb.id = nntb.thong_bao_id
                left join canh_bao cb on cb.id = tb.canh_bao_id
                left join camera c on c.id = cb.camera_id
                where nntb.nguoi_dung_id = %s
                order by tb.tao_luc desc, nntb.id desc
                limit %s
                """,
                (user_id, limit),
            )
            items = [self._normalize_notification(row) for row in cur.fetchall()]
            cur.execute(
                """
                select count(*) as total
                from nguoi_nhan_thong_bao
                where nguoi_dung_id = %s
                  and da_doc = false
                """,
                (user_id,),
            )
            unread = cur.fetchone()["total"]
        return {"items": items, "unread": unread}

    def _normalize_notification(self, row):
        data = dict(row)
        created_at = data.get("created_at")
        return {
            "id": data["id"],
            "title": data.get("title") or "Thong bao",
            "message": data.get("message") or "",
            "severity": str(data.get("severity") or "MEDIUM").upper(),
            "created_at": created_at.isoformat() if created_at else None,
            "read": bool(data.get("read")),
            "alert_id": data.get("alert_id"),
            "camera_id": data.get("camera_id"),
        }

    def _get_or_create_notification(self, cur, alert_id, title, message, channel):
        if alert_id is not None:
            cur.execute(
                """
                insert into thong_bao (
                  canh_bao_id, tieu_de, noi_dung, kenh, trang_thai
                )
                values (%s, %s, %s, %s, 'READY')
                on conflict (canh_bao_id, kenh)
                where canh_bao_id is not null
                do nothing
                returning id
                """,
                (alert_id, title, message, channel),
            )
            row = cur.fetchone()
            if row:
                return row["id"], True
            cur.execute(
                """
                select id
                from thong_bao
                where canh_bao_id = %s
                  and kenh = %s
                limit 1
                """,
                (alert_id, channel),
            )
            return cur.fetchone()["id"], False
        cur.execute(
            """
            insert into thong_bao (
              canh_bao_id, tieu_de, noi_dung, kenh, trang_thai
            )
            values (null, %s, %s, %s, 'READY')
            returning id
            """,
            (title, message, channel),
        )
        return cur.fetchone()["id"], True

    def _user_id(self, username):
        if not username:
            return None
        with db_cursor() as cur:
            cur.execute(
                """
                select id
                from nguoi_dung
                where ten_dang_nhap = %s
                  and trang_thai = 'ACTIVE'
                  and deleted_at is null
                limit 1
                """,
                (username,),
            )
            row = cur.fetchone()
        return row["id"] if row else None

    def _alert_recipients(self, cur):
        cur.execute(
            """
            select distinct nd.id
            from nguoi_dung nd
            join vai_tro_nguoi_dung vtnd
              on vtnd.nguoi_dung_id = nd.id
             and vtnd.dang_hoat_dong = true
             and (vtnd.ngay_ket_thuc is null or vtnd.ngay_ket_thuc > now())
            join vai_tro vt
              on vt.id = vtnd.vai_tro_id
             and vt.trang_thai = 'ACTIVE'
             and vt.deleted_at is null
            left join vai_tro_quyen vtq
              on vtq.vai_tro_id = vt.id
             and vtq.duoc_phep = true
            left join quyen q
              on q.id = vtq.quyen_id
             and q.trang_thai = 'ACTIVE'
            where nd.trang_thai = 'ACTIVE'
              and nd.deleted_at is null
              and (
                vt.ma_vai_tro in ('ADMIN', 'SUPERVISOR')
                or q.ma_quyen = 'alertmgmt'
              )
            order by nd.id
            """
        )
        return [row["id"] for row in cur.fetchall()]


notification_service = NotificationService()
