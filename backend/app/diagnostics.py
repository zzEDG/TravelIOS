"""脱敏的请求诊断：只记录状态和异常类型，不记录 URL 参数或消息正文。"""
import logging

import httpx

logger = logging.getLogger("app.diagnostics")


class DiagnosticAsyncClient(httpx.AsyncClient):
    def __init__(self, *args, service, **kwargs):
        self.service = service
        super().__init__(*args, **kwargs)

    async def send(self, request, *args, **kwargs):
        try:
            response = await super().send(request, *args, **kwargs)
        except httpx.RequestError as exc:
            logger.warning("外部请求异常 service=%s http_status=none error_type=%s",
                           self.service, type(exc).__name__)
            raise
        if response.status_code >= 400:
            logger.warning("外部请求失败 service=%s http_status=%d", self.service, response.status_code)
        return response


class DiagnosticClient(httpx.Client):
    def __init__(self, *args, service, **kwargs):
        self.service = service
        super().__init__(*args, **kwargs)

    def send(self, request, *args, **kwargs):
        try:
            response = super().send(request, *args, **kwargs)
        except httpx.RequestError as exc:
            logger.warning("外部请求异常 service=%s http_status=none error_type=%s",
                           self.service, type(exc).__name__)
            raise
        if response.status_code >= 400:
            logger.warning("外部请求失败 service=%s http_status=%d", self.service, response.status_code)
        return response
