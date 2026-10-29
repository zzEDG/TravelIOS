import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from openai import InternalServerError, RateLimitError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.integrations import llm_service
from app.controllers.errors import _handle_error
from app.services.candidate_search import CandidateSearchError
from app.integrations.amap_service import AmapServiceError
from app.dependencies import ServiceResources

class ModelSwitchTests(unittest.TestCase):
    def test_kimi_alias_and_explicit_selection(self):
        with patch.dict(os.environ, {"KIMI_API_KEY":"test-kimi", "DEEPSEEK_API_KEY":"test-deepseek"}, clear=True), patch.object(llm_service, "ChatOpenAI") as factory:
            llm_service.get_llm(provider="moonshot", http_client=object(), http_async_client=object())
            self.assertEqual(factory.call_args.kwargs["api_key"], "test-kimi")
            self.assertEqual(factory.call_args.kwargs["model"], "kimi-k2.6")
            self.assertNotIn("LLM_API_KEY", os.environ)

    def test_missing_or_unknown_provider_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            for provider in ("moonshot", "deepseek", "unknown"):
                with self.assertRaises(ValueError):
                    llm_service.validate_provider(provider)

    def test_request_resources_do_not_share_provider(self):
        a, b = ServiceResources("deepseek"), ServiceResources("moonshot")
        self.assertEqual(a.provider, "deepseek")
        self.assertEqual(b.provider, "moonshot")

    def test_wrapped_502_suggests_switching(self):
        response=httpx.Response(502, request=httpx.Request("POST", "https://example.com"))
        upstream=InternalServerError("Bad Gateway", response=response, body=None)
        wrapped=CandidateSearchError("search failed")
        wrapped.__cause__=upstream
        result=_handle_error(wrapped)
        self.assertEqual(result.code, 50007)
        self.assertIn("请切换模型", result.message)

    def test_map_failure_does_not_suggest_switching(self):
        result=_handle_error(AmapServiceError("map unavailable"))
        self.assertEqual(result.code, 50002)
        self.assertNotIn("切换模型", result.message)

    def test_rate_limit_explains_upstream_status(self):
        response = httpx.Response(429, request=httpx.Request("POST", "https://example.com"))
        result = _handle_error(RateLimitError("rate limited", response=response, body=None))
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.code, 50007)
        self.assertIn("429", result.message)
        self.assertIn("切换模型", result.message)

    def test_rpm_limit_is_explained_without_exposing_account(self):
        response = httpx.Response(429, request=httpx.Request("POST", "https://example.com"))
        for body in (
            {"message":"account private-org request reached organization max RPM: 3, retry later"},
            {"error":{"message":"account private-org request reached organization max RPM: 3"}},
        ):
            with self.subTest(body=body):
                result = _handle_error(RateLimitError("rate limited", response=response, body=body))
                self.assertEqual(result.code,50007)
                self.assertIn("每分钟最多 3 次请求",result.message)
                self.assertNotIn("private-org",result.message)
