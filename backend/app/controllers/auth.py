"""用户认证控制器：统一响应外壳与 HttpOnly 会话 Cookie。"""
import hmac
import logging
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from app.models.auth import Credentials, PublicUser, UserResponse, LoginResponse, LoginData, CsrfResponse, CsrfData, LogoutResponse
from app.services.auth_service import AuthService, AuthError, COOKIE_NAME, COOKIE_PATH, csrf_token, cookie_secure
from app.controllers.auth_dependencies import get_auth_service, require_user, check_origin
from app.controllers.errors import TravelRoute

router = APIRouter(prefix="/auth", tags=["用户认证"], route_class=TravelRoute)
logger = logging.getLogger("app.diagnostics")


def no_cache(response):
    response.headers["Cache-Control"] = "no-store"


async def auth_error_handler(request, exc):
    logger.warning("认证拒绝 http_status=%d app_code=%d", exc.status_code, exc.code)
    headers = {"Cache-Control": "no-store"}
    if exc.status_code == 429:
        headers["Retry-After"] = "900"
    return JSONResponse(status_code=exc.status_code,
                        content={"code": exc.code, "message": exc.message, "details": None}, headers=headers)


@router.post("/register", status_code=201, response_model=UserResponse, dependencies=[Depends(check_origin)])
async def register(body: Credentials, request: Request, response: Response, auth: AuthService = Depends(get_auth_service)):
    user = await run_in_threadpool(auth.register, body.username, body.password.get_secret_value(), request.client.host if request.client else "unknown")
    no_cache(response)
    logger.warning("账号注册成功")
    return UserResponse(data=PublicUser.model_validate(user))


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(check_origin)])
async def login(body: Credentials, request: Request, response: Response, auth: AuthService = Depends(get_auth_service)):
    user, token, expires = await run_in_threadpool(auth.login, body.username, body.password.get_secret_value(), request.client.host if request.client else "unknown")
    response.set_cookie(COOKIE_NAME, token, max_age=604800, httponly=True, secure=cookie_secure(), samesite="lax", path=COOKIE_PATH)
    no_cache(response)
    logger.warning("登录成功")
    return LoginResponse(data=LoginData(user=PublicUser.model_validate(user), expires_at=expires, csrf_token=csrf_token(token)))


@router.get("/me", response_model=UserResponse)
async def me(response: Response, user=Depends(require_user)):
    no_cache(response)
    return UserResponse(data=PublicUser.model_validate(user))


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(request: Request, response: Response, user=Depends(require_user)):
    no_cache(response)
    return CsrfResponse(data=CsrfData(csrf_token=csrf_token(request.cookies[COOKIE_NAME])))


@router.post("/logout", response_model=LogoutResponse, dependencies=[Depends(check_origin)])
async def logout(request: Request, response: Response, auth: AuthService = Depends(get_auth_service)):
    token = request.cookies.get(COOKIE_NAME, "")
    user = await run_in_threadpool(auth.session, token)
    if user and not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), csrf_token(token).encode()):
        raise AuthError(403, 40301, "请求验证失败")
    await run_in_threadpool(auth.logout, token)
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, secure=cookie_secure(), httponly=True, samesite="lax")
    no_cache(response)
    return LogoutResponse()
