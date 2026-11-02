"""验证实际模型客户端的同步与异步 HTTP 重试策略。"""
import os
import unittest
from unittest.mock import patch, AsyncMock
import httpx
import openai
from app.integrations.llm_service import get_llm

SUCCESS = {"id":"chatcmpl-test", "object":"chat.completion", "created":1, "model":"test", "choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}

class LlmRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_retries_503_and_succeeds(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(503, json={"error":{"message":"unavailable"}}) if len(calls) < 3 else httpx.Response(200, json=SUCCESS)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with patch.dict(os.environ, {"LLM_API_KEY":"test", "LLM_BASE_URL":"https://example.test/v1"}), patch("openai._base_client.anyio.sleep", new_callable=AsyncMock):
                result = await get_llm(http_async_client=client).ainvoke("hello")
        self.assertEqual(result.content, "ok")
        self.assertEqual(len(calls), 3)

    async def test_auth_error_is_not_retried(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(401, json={"error":{"message":"invalid"}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with patch.dict(os.environ, {"LLM_API_KEY":"test", "LLM_BASE_URL":"https://example.test/v1"}):
                with self.assertRaises(openai.AuthenticationError):
                    await get_llm(http_async_client=client).ainvoke("hello")
        self.assertEqual(len(calls), 1)

    def test_sync_retries_429_and_stops_after_three_attempts(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(429, json={"error":{"message":"limited"}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            with patch.dict(os.environ, {"LLM_API_KEY":"test", "LLM_BASE_URL":"https://example.test/v1"}), patch("openai._base_client.time.sleep"):
                with self.assertRaises(openai.RateLimitError):
                    get_llm(http_client=client).invoke("hello")
        self.assertEqual(len(calls), 3)
