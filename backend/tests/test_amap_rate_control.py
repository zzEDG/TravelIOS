"""高德统一入口的限速、重试和错误分类测试。"""
import asyncio
import unittest
from unittest.mock import patch

import httpx

from app.integrations.amap_service import AmapRateLimitError, AmapService, _RateLimiter


class AmapRateControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_limiter_includes_static_map(self):
        starts = []
        async def respond(request):
            starts.append(asyncio.get_running_loop().time())
            if request.url.path.endswith("staticmap"):
                return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
            return httpx.Response(200, json={"status": "1", "pois": []})
        with patch.dict("os.environ", {"AMAP_MAX_QPS": "20"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                first, second = AmapService("secret", client), AmapService("secret", client)
                await asyncio.gather(first.search("上海", "公园"),
                                     second.static_map([(121.0, 31.0)]),
                                     second.search("上海", "餐厅"))
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(b - a >= 0.045 for a, b in zip(starts, starts[1:])))

    async def test_qps_failure_retries_then_succeeds(self):
        calls = []
        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, json={"status": "0", "infocode": "10020",
                                                 "info": "CKQPS_HAS_EXCEEDED_THE_LIMIT"})
            return httpx.Response(200, json={"status": "1", "pois": []})
        with patch.dict("os.environ", {"AMAP_MAX_QPS": "1000"}), patch("app.integrations.amap_service.random.uniform", return_value=0):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                self.assertEqual(await AmapService("secret", client).search("上海", "公园"), [])
        self.assertEqual(len(calls), 2)

    async def test_minute_and_daily_limits_do_not_retry(self):
        for code, info, message in (("10003", "DAILY_QUERY_OVER_LIMIT", "日配额"),
                                    ("10004", "ACCESS_TOO_FREQUENT", "分钟"),
                                    ("10045", "USER_ABROAD_DAILY_QUERY_OVER_LIMIT", "日配额")):
            with self.subTest(code=code):
                calls = []
                def respond(request):
                    calls.append(request)
                    return httpx.Response(200, json={"status": "0", "infocode": code, "info": info})
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    with self.assertRaisesRegex(AmapRateLimitError, message):
                        await AmapService("secret", client).search("上海", "公园")
                self.assertEqual(len(calls), 1)

    async def test_http_429_respects_retry_after_and_bounded_wait(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "60"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(AmapRateLimitError):
                await asyncio.wait_for(AmapService("secret", client).search("上海", "公园"), 1)
        self.assertEqual(len(calls), 1)

    async def test_http_429_retries_and_static_map_recovers(self):
        calls = []
        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
        with patch.dict("os.environ", {"AMAP_MAX_QPS": "1000"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                result = await AmapService("secret", client).static_map([(121, 31)])
        self.assertEqual(result, (b"png", "image/png"))
        self.assertEqual(len(calls), 2)

    async def test_auth_error_does_not_retry(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"status": "0", "infocode": "10001", "info": "INVALID_USER_KEY"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaisesRegex(ValueError, "INVALID_USER_KEY"):
                await AmapService("secret", client).search("上海", "公园")
        self.assertEqual(len(calls), 1)

    async def test_failure_logs_status_without_secret_or_location(self):
        def respond(request):
            return httpx.Response(403, json={"status": "0", "infocode": "10001", "info": "INVALID_USER_KEY"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertLogs("app.integrations.amap_service", level="WARNING") as logs:
                with self.assertRaises(httpx.HTTPStatusError):
                    await AmapService("sensitive-key", client).search("上海", "private-place")
        output = " ".join(logs.output)
        for expected in ("/place/text", "http_status=403", "infocode=10001", "info=INVALID_USER_KEY", "attempt=1"):
            self.assertIn(expected, output)
        self.assertNotIn("sensitive-key", output)
        self.assertNotIn("private-place", output)

    async def test_network_failure_logs_type_without_request_url(self):
        def respond(request):
            raise httpx.ConnectError("sensitive-key", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertLogs("app.integrations.amap_service", level="WARNING") as logs:
                with self.assertRaises(httpx.ConnectError):
                    await AmapService("sensitive-key", client).search("上海", "公园")
        output = " ".join(logs.output)
        self.assertIn("error_type=ConnectError", output)
        self.assertIn("http_status=none", output)
        self.assertNotIn("sensitive-key", output)

    async def test_cancelled_waiter_releases_limiter(self):
        limiter = _RateLimiter(20)
        await limiter.wait()
        waiter = asyncio.create_task(limiter.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await asyncio.wait_for(limiter.wait(), 0.5)
