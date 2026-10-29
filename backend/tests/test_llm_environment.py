"""验证系统环境变量可直接用于 LangChain 模型配置。"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.integrations import llm_service  # noqa: E402


class LLMEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_factory_does_not_cache_between_requests(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only-key"}, clear=True), \
             patch.object(llm_service, "ChatDeepSeek", side_effect=[object(), object()]) as model_class:
            self.assertIsNot(llm_service.get_llm(), llm_service.get_llm())
            self.assertEqual(model_class.call_count, 2)
            for call in model_class.call_args_list:
                self.addCleanup(call.kwargs['http_client'].close)
                self.addAsyncCleanup(call.kwargs['http_async_client'].aclose)

    async def test_deepseek_key_selects_langchain_deepseek_without_mutating_environment(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only-key"}, clear=True), \
             patch.object(llm_service, "ChatDeepSeek") as model_class:
            self.assertIs(llm_service.get_llm(), model_class.return_value)
            clients = model_class.call_args.kwargs
            self.addCleanup(clients['http_client'].close)
            self.addAsyncCleanup(clients['http_async_client'].aclose)
            model_class.assert_called_once_with(
                api_key="test-only-key", model="deepseek-chat", timeout=35, max_retries=1,
                http_client=clients['http_client'], http_async_client=clients['http_async_client'],
            )
            self.assertIsNone(os.getenv("LLM_API_KEY"))


if __name__ == "__main__":
    unittest.main()
