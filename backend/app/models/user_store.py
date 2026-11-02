"""账号与登录会话的 PostgreSQL 存储；数据库结构由迁移脚本管理。"""
import os
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row


class UserStore:
    def __init__(self, database_url=None):
        self.database_url = database_url or os.getenv("DATABASE_URL") or "postgresql:///travelos"

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def create(self, username, password_hash):
        with self._connect() as conn:
            return conn.execute(
                "INSERT INTO users(id, username, password_hash) VALUES (%s, %s, %s) "
                "RETURNING id, username, created_at", (uuid4(), username, password_hash)
            ).fetchone()

    def find_by_username(self, username):
        with self._connect() as conn:
            return conn.execute("SELECT * FROM users WHERE username = %s", (username,)).fetchone()

    def create_session(self, user_id, token_hash):
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        with self._connect() as conn:
            # 禁用账号与创建会话串行，避免登录校验后禁用账号却仍创建有效会话。
            user = conn.execute("SELECT id FROM users WHERE id = %s AND is_active FOR UPDATE", (user_id,)).fetchone()
            if user is None:
                raise ValueError("账号不可用")
            conn.execute("INSERT INTO user_sessions(id,user_id,token_hash,expires_at) VALUES (%s,%s,%s,%s)",
                         (uuid4(), user_id, token_hash, expires))
        return expires

    def get_session(self, token_hash):
        with self._connect() as conn:
            return conn.execute(
                "SELECT u.id,u.username,u.created_at,s.expires_at FROM user_sessions s "
                "JOIN users u ON u.id=s.user_id WHERE s.token_hash=%s "
                "AND s.revoked_at IS NULL AND s.expires_at>NOW() AND u.is_active", (token_hash,)
            ).fetchone()

    def revoke_session(self, token_hash):
        with self._connect() as conn:
            conn.execute("UPDATE user_sessions SET revoked_at=NOW() WHERE token_hash=%s AND revoked_at IS NULL",
                         (token_hash,))

    def consume_attempts(self, keys):
        allowed = True
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_rate_limits WHERE expires_at <= NOW()")
            for key, limit in sorted(keys):
                row = conn.execute(
                    "INSERT INTO auth_rate_limits(bucket, count, expires_at) VALUES (%s, 1, NOW() + INTERVAL '15 minutes') "
                    "ON CONFLICT(bucket) DO UPDATE SET count=auth_rate_limits.count + 1 RETURNING count",
                    (key,),
                ).fetchone()
                allowed = allowed and row["count"] <= limit
        return allowed
