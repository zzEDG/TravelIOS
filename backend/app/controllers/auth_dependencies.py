"""HTTP 会话与 CSRF 依赖；在调用规划服务前完成验证。"""
import hmac
from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool
from app.config import get_settings
from app.services.auth_service import AuthService, AuthError, COOKIE_NAME, csrf_token


def get_auth_service():
    return AuthService()


def check_origin(request: Request):
    if request.headers.get("origin") not in get_settings().get_cors_origins_list():
        raise AuthError(400, 40010, "当前请求来源不允许")


async def require_user(request: Request, auth: AuthService = Depends(get_auth_service)):
    token = request.cookies.get(COOKIE_NAME, "")
    user = await run_in_threadpool(auth.session, token)
    if not user:
        raise AuthError(401, 40101, "请先登录或登录已过期")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(supplied.encode(), csrf_token(token).encode()):
            raise AuthError(403, 40301, "请求验证失败")
    return user
