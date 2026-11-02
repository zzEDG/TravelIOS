"""服务与 HTTP 错误诊断不得泄露请求参数或凭据。"""
import os
import unittest
from unittest.mock import patch

import httpx

from app.diagnostics import DiagnosticAsyncClient, DiagnosticClient
from app.integrations.jev_service import JevService, JevServiceError
from app.main import app


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_service_error_and_network_failure_are_redacted(self):
        def handler(request):
            return httpx.Response(403, text="secret-response")
        async with DiagnosticAsyncClient(service="qweather", transport=httpx.MockTransport(handler)) as client:
            with self.assertLogs("app.diagnostics", level="WARNING") as captured:
                await client.get("https://example.com/private?key=secret-key")
        output = " ".join(captured.output)
        self.assertIn("service=qweather http_status=403", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("private", output)

        def failed(request):
            raise httpx.ConnectError("secret-key", request=request)
        async with DiagnosticAsyncClient(service="llm", transport=httpx.MockTransport(failed)) as client:
            with self.assertLogs("app.diagnostics", level="WARNING") as captured:
                with self.assertRaises(httpx.ConnectError):
                    await client.get("https://example.com/private?key=secret-key")
        self.assertIn("error_type=ConnectError", " ".join(captured.output))
        self.assertNotIn("secret", " ".join(captured.output))

    async def test_api_validation_records_http_status_without_request_data(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with self.assertLogs("app.diagnostics", level="WARNING") as captured:
                response = await client.post("/api/v1/travel?secret-key=value", json={})
        self.assertEqual(response.status_code, 422)
        output = " ".join(captured.output)
        self.assertIn("http_status=422", output)
        self.assertNotIn("secret-key", output)

    async def test_jev_geo_restriction_is_distinguished(self):
        def handler(request):
            return httpx.Response(403, json={"error": {"metadata": {"failed_routing_step": "Gate Endpoints with Geo Restrictions"}}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret-key"}):
                with self.assertLogs("app.diagnostics", level="WARNING") as captured:
                    with self.assertRaisesRegex(JevServiceError, "地区不可用"):
                        await JevService(client).decide({}, {})
        self.assertIn("reason=geo_restriction", " ".join(captured.output))
        self.assertNotIn("secret-key", " ".join(captured.output))

    def test_sync_client_logs_status_without_url(self):
        with DiagnosticClient(service="llm", transport=httpx.MockTransport(lambda request: httpx.Response(429))) as client:
            with self.assertLogs("app.diagnostics", level="WARNING") as captured:
                client.get("https://example.com/private?key=secret-key")
        output = " ".join(captured.output)
        self.assertIn("service=llm http_status=429", output)
        self.assertNotIn("secret", output)
