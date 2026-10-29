"""设置 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 后，验证真实客户端仍为国内服务选择直连。"""
import os
import unittest
from unittest.mock import patch

import httpx

from app.dependencies import ServiceResources
from app.integrations.amap_service import AmapService
from app.integrations.qweather_service import QWeatherService
from app.integrations.llm_service import get_llm, llm_trust_env


PROXY_ENV = {
    'HTTP_PROXY':'http://127.0.0.1:9', 'HTTPS_PROXY':'http://127.0.0.1:9',
    'ALL_PROXY':'http://127.0.0.1:9', 'NO_PROXY':'',
}
DEEPSEEK_ENVIRONMENTS = (
    {'DEEPSEEK_API_KEY':'offline-key'},
    {'LLM_API_KEY':'offline-key','LLM_BASE_URL':'https://api.deepseek.com/v1'},
    {'OPENAI_API_KEY':'offline-key','LLM_BASE_URL':'https://api.deepseek.com'},
)


class DomesticProxyTests(unittest.IsolatedAsyncioTestCase):
    def assert_direct(self, client, url):
        self.assertFalse(client.trust_env)
        # 验证 HTTPX 为实际目标 URL 选择默认直连 transport，而不是环境代理 transport。
        self.assertIs(client._transport_for_url(httpx.URL(url)), client._transport)

    async def test_amap_and_weather_standalone_clients_ignore_environment_proxy(self):
        with patch.dict(os.environ,PROXY_ENV,clear=True):
            amap = AmapService('offline-key')
            weather = QWeatherService(host='test.qweatherapi.com',api_key='offline-key')
            try:
                self.assert_direct(amap.client,'https://restapi.amap.com/v3/place/around')
                self.assert_direct(weather.client,'https://test.qweatherapi.com/weather/v1/daily/31/121')
            finally:
                await amap.client.aclose()
                await weather.client.aclose()

    async def test_request_resources_direct_domestic_services_but_preserve_foreign_proxy(self):
        for environment in DEEPSEEK_ENVIRONMENTS:
            with self.subTest(provider=next(iter(environment))), patch.dict(
                os.environ,{**PROXY_ENV,**environment},clear=True
            ):
                async with ServiceResources() as resources:
                    self.assert_direct(resources.amap.client,'https://restapi.amap.com/v3/place/around')
                    self.assert_direct(resources.weather.client,'https://test.qweatherapi.com/weather/v1/daily/31/121')
                    model = resources.llm
                    self.assert_direct(model.http_async_client,'https://api.deepseek.com/chat/completions')
                    self.assert_direct(model.http_client,'https://api.deepseek.com/chat/completions')
                    self.assertIs(model.root_async_client._client,model.http_async_client)
                    self.assertIs(model.root_client._client,model.http_client)
                    for client,url in ((resources.jev.client,'https://openrouter.ai/api/alpha/decisions'),
                                       (resources.unsplash.client,'https://api.unsplash.com/search/photos')):
                        self.assertTrue(client.trust_env)
                        self.assertIsNot(client._transport_for_url(httpx.URL(url)),client._transport)
                    self.assertEqual({name:os.getenv(name) for name in PROXY_ENV},PROXY_ENV)
                self.assertTrue(model.http_client.is_closed)
                self.assertTrue(model.http_async_client.is_closed)

    async def test_standalone_deepseek_factory_configures_sync_and_async_sdk_clients(self):
        for environment in DEEPSEEK_ENVIRONMENTS:
            with self.subTest(provider=next(iter(environment))), patch.dict(
                os.environ,{**PROXY_ENV,**environment},clear=True
            ):
                model = get_llm()
                try:
                    self.assert_direct(model.root_client._client,'https://api.deepseek.com/chat/completions')
                    self.assert_direct(model.root_async_client._client,'https://api.deepseek.com/chat/completions')
                finally:
                    model.root_client.close()
                    await model.root_async_client.close()

    async def test_other_model_services_retain_environment_proxy(self):
        with patch.dict(os.environ,{**PROXY_ENV,'LLM_API_KEY':'offline-key',
                                  'LLM_BASE_URL':'https://openrouter.ai/api/v1'},clear=True):
            async with ServiceResources() as resources:
                model = resources.llm
                try:
                    for client in (model.root_client._client,model.root_async_client._client):
                        self.assertTrue(client.trust_env)
                        self.assertIsNot(client._transport_for_url(httpx.URL('https://openrouter.ai/api/v1/chat/completions')),
                                         client._transport)
                finally:
                    model.root_client.close()

    async def test_explicit_injected_clients_are_reused(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'offline-key'},clear=True), \
             httpx.Client(trust_env=False) as sync_client:
            async with httpx.AsyncClient(trust_env=False) as async_client:
                model = get_llm(http_client=sync_client,http_async_client=async_client)
                self.assertIs(model.root_client._client,sync_client)
                self.assertIs(model.root_async_client._client,async_client)

    async def test_model_initialization_failure_closes_both_owned_clients(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'offline-key'},clear=True), \
             patch('app.dependencies.get_llm',side_effect=ValueError('invalid model')) as factory:
            with self.assertRaises(ValueError):
                async with ServiceResources() as resources:
                    resources.llm
            self.assertTrue(factory.call_args.kwargs['http_client'].is_closed)
            self.assertTrue(factory.call_args.kwargs['http_async_client'].is_closed)

    def test_deepseek_detection_uses_the_actual_hostname(self):
        with patch.dict(os.environ,{'LLM_API_KEY':'offline-key',
                                  'LLM_BASE_URL':'https://api.deepseek.com.proxy.example/v1'},clear=True):
            self.assertTrue(llm_trust_env())
