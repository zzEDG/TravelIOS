"""Jev HTTP 契约与中英映射的离线回归测试。"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from langchain_core.messages import AIMessage


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.walk import WalkRequest  # noqa: E402
from app.integrations.amap_service import Place  # noqa: E402
from app.integrations.jev_service import JevService  # noqa: E402
from app.integrations.jev_translation import JevTranslation  # noqa: E402


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.inputs = []

    async def ainvoke(self, messages):
        self.inputs.append(messages)
        return AIMessage(content=json.dumps(next(self.outputs), ensure_ascii=False))


class JevTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_contract_and_answers(self):
        captured = {}

        def respond(request):
            captured["authorization"] = request.headers["Authorization"]
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"answers": {"pick": {"type": "choice", "choice": "p1"}}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key", "JEV_MODEL_ID": "typesafe/jev-1.13"}):
            answers = await JevService(client).decide(
                {"city": "Shanghai"},
                {"pick": {"type": "choice", "instructions": "Choose a stop",
                          "criteria": {"p1": "Old Building"}}},
            )
        self.assertEqual(captured["authorization"], "Bearer test-key")
        self.assertEqual(captured["url"], "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(captured["payload"]["model"], "typesafe/jev-1.13")
        self.assertEqual(captured["payload"]["state"], {"city": "Shanghai"})
        self.assertEqual(answers["pick"]["choice"], "p1")

    async def test_rate_limit_retries_then_succeeds(self):
        attempts = []

        def respond(request):
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(429, json={"error": "rate limit"})
            return httpx.Response(200, json={"answers": {"pick": {"type": "choice", "choice": "p1"}}})

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch(
            "app.integrations.jev_service.asyncio.sleep"
        ) as sleep:
            answers = await JevService(httpx.AsyncClient(transport=httpx.MockTransport(respond))).decide(
                {"city": "Shanghai"}, {"pick": {"type": "choice", "instructions": "Choose",
                                                 "criteria": {"p1": "Place"}}},
            )
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once()
        self.assertEqual(answers["pick"]["choice"], "p1")

    async def test_missing_key_is_explicit(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                await JevService().decide({}, {"pick": {"type": "choice"}})

    async def test_translation_preserves_ids_and_rejects_chinese_leak(self):
        llm = FakeLLM([
            {"city": "Shanghai", "theme": "Architecture and Coffee", "notes": "",
             "keywords": {"k0": "historic building", "k1": "cafe"}},
            {"places": {"p1": {"name": "Old Building", "category": "historic building",
                                "address": "Test Road, Shanghai"}}},
        ])
        translator = JevTranslation(llm)
        brief, keywords = await translator.brief(
            WalkRequest(city="上海", theme="建筑与咖啡"), ["历史建筑", "咖啡馆"],
        )
        self.assertEqual(brief["city"], "Shanghai")
        self.assertEqual(keywords["历史建筑"], "historic building")
        places = await translator.places([
            Place("p1", "老建筑", "历史建筑", "上海市测试路",
                  121.47, 31.23, "310101", "历史建筑"),
        ])
        self.assertEqual(places["p1"]["name"], "Old Building")
        self.assertEqual(len(llm.inputs), 2)

        bad = JevTranslation(FakeLLM([
            {"city": "上海", "theme": "Architecture and Coffee", "notes": "",
             "keywords": {"k0": "historic building"}},
        ]))
        with self.assertRaisesRegex(ValueError, "city"):
            await bad.brief(WalkRequest(city="上海", theme="建筑与咖啡"), ["历史建筑"])


if __name__ == "__main__":
    unittest.main()
