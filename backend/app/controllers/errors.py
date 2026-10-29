"""V2 新接口的统一错误外壳。旧接口保持原有 FastAPI 错误格式。"""

import logging
import re
import sqlite3
import httpx
from openai import APIError, RateLimitError

from app.integrations.amap_service import AmapRateLimitError, AmapServiceError
from app.integrations.jev_service import JevServiceError
from app.integrations.jev_translation import JevTranslationError
from app.services.preferences import PreferenceExtractionError
from app.services.candidate_search import CandidateSearchError, CandidateModelProtocolError, CandidateSearchTimeout
from app.models.trip_store import TripNotFoundError, TripExpiredError, TripVersionConflictError

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from app.models.travel import ErrorResponse


class TravelAPIError(Exception):
    def __init__(self, status_code: int, code: int, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


class TravelRoute(APIRoute):
    """在 HTTP 边界统一转换业务异常，也覆盖依赖初始化失败。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except (TravelAPIError, RequestValidationError, HTTPException):
                raise
            except Exception as exc:
                raise _handle_error(exc) from exc

        return handle


async def travel_error_handler(_request: Request, exc: TravelAPIError) -> JSONResponse:
    body = ErrorResponse(code=exc.code, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


async def validation_error_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/v1/"):
        return await request_validation_exception_handler(request, exc)
    details = [
        {"field": ".".join(str(part) for part in error["loc"]),
         "message": str(error["msg"])}
        for error in exc.errors()
    ]
    body = ErrorResponse(code=40001, message="请求参数无效", details=details)
    return JSONResponse(status_code=422, content=body.model_dump())


logger = logging.getLogger(__name__)

# 子类放在 ValueError 前，避免外部服务错误被当作用户参数错误。
_ERRORS = {
    TripNotFoundError: (404, 40004),
    TripExpiredError: (410, 40006),
    TripVersionConflictError: (409, 40005),
    PreferenceExtractionError: (502, 50005),
    CandidateSearchTimeout: (504, 50007),
    CandidateModelProtocolError: (502, 50007),
    CandidateSearchError: (502, 50006),
    AmapRateLimitError: (429, 50011),
    AmapServiceError: (502, 50002),
    JevServiceError: (502, 50001),
    JevTranslationError: (502, 50004),
    ValueError: (422, 40002),
}


def _handle_error(exc: Exception) -> TravelAPIError:
    # SDK errors may be wrapped by candidate search or translation services.
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, RateLimitError):
            # 仅提取限额数字，绝不向前端转发包含账号标识的上游正文。
            body = cause.body if isinstance(cause.body, dict) else {}
            detail = body.get("error", body)
            detail = detail if isinstance(detail, dict) else {}
            limit = re.search(r"max RPM:\s*(\d+)", str(detail.get("message", "")), re.IGNORECASE)
            if limit:
                return TravelAPIError(503, 50007,
                    f"当前 AI 账号每分钟最多 {limit.group(1)} 次请求，已触发限流（429）。"
                    "多类别规划需要多次模型调用，请切换模型后重试；已填写内容保留")
            return TravelAPIError(503, 50007, "当前 AI 服务返回 429（请求限流或额度限制），请切换模型，或稍后重试；已填写内容保留")
        if isinstance(cause, APIError):
            return TravelAPIError(502, 50007, "当前 AI 模型调用失败，请切换模型后重试；已填写内容保留")
        cause = cause.__cause__ or cause.__context__
    for error_type, (status, code) in _ERRORS.items():
        if isinstance(exc, error_type):
            return TravelAPIError(status, code, str(exc))
    if isinstance(exc, sqlite3.Error):
        logger.exception("Trip storage error")
        return TravelAPIError(503, 50010, "行程存储暂不可用")
    if isinstance(exc, httpx.HTTPError):
        logger.exception("External service error")
        return TravelAPIError(502, 50003, "外部服务暂不可用，请稍后重试")
    logger.exception("Unexpected travel planning error")
    return TravelAPIError(502, 50000, "路线生成失败，请稍后重试")
