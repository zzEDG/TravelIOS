"""账号密码、会话签发和 CSRF；不依赖规划或外部模型。"""
import hashlib
import hmac
import os
import secrets
import threading

import psycopg
from app.models.user_store import UserStore
from app.services.passwords import hash_password, verify_password

COOKIE_NAME = "travelos_session"
COOKIE_PATH = "/api/v1"
_hash_slots = threading.BoundedSemaphore(4)


class AuthError(Exception):
    def __init__(self, status, code, message):
        self.status_code, self.code, self.message = status, code, message
        super().__init__(message)


def csrf_secret():
    secret = os.getenv("AUTH_CSRF_SECRET", "")
    if len(secret.encode()) < 32:
        raise RuntimeError("请配置至少32字节的随机 AUTH_CSRF_SECRET")
    return secret.encode()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token):
    return hmac.new(csrf_secret(), b"travelos:csrf:v1:" + token.encode(), hashlib.sha256).hexdigest()


def cookie_secure():
    return os.getenv("AUTH_COOKIE_SECURE", "true").lower() not in {"false", "0"}


class AuthService:
    def __init__(self, store=None):
        self.store = store if store is not None else UserStore()

    def throttle(self, username, source, action):
        # 数据库存计数；多 worker 共用限额，不保存用户名或来源明文。
        buckets = [("account:" + username, 10), ("source:" + source, 50)]
        keys = [(hmac.new(csrf_secret(), (action + ":" + key).encode(), hashlib.sha256).hexdigest(), limit)
                for key, limit in buckets]
        if not self.store.consume_attempts(keys):
            raise AuthError(429, 42901, "请求过于频繁，请稍后重试")

    def register(self, username, password, source):
        self.throttle(username, source, "register")
        # 本地常见密码检查；不将密码交给外部服务。
        if password.casefold() in {"password123456", "123456789012", "1234567890123456", "qwerty12345678"}:
            raise AuthError(422, 40001, "请使用不常见的密码")
        with _hash_slots:
            hashed = hash_password(password)
        try:
            return self.store.create(username, hashed)
        except psycopg.errors.UniqueViolation as exc:
            raise AuthError(409, 40901, "用户名已被使用") from exc

    def login(self, username, password, source):
        self.throttle(username, source, "login")
        user = self.store.find_by_username(username)
        with _hash_slots:
            valid = verify_password(user["password_hash"] if user else None, password)
        if not valid or not user or not user["is_active"]:
            raise AuthError(401, 40102, "账号或密码错误")
        token = secrets.token_urlsafe(32)
        try:
            expires = self.store.create_session(user["id"], token_hash(token))
        except ValueError as exc:
            raise AuthError(401, 40102, "账号或密码错误") from exc
        return user, token, expires

    def session(self, token):
        if not token or len(token) != 43:
            return None
        return self.store.get_session(token_hash(token))

    def logout(self, token):
        if token and len(token) == 43:
            self.store.revoke_session(token_hash(token))
