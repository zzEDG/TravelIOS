"""请求资源管理：只负责依赖的创建和释放，不参与路由、规划或存储。"""

from contextlib import AsyncExitStack
from functools import cached_property

import httpx

from app.config import get_settings
from app.integrations.amap_service import AmapService
from app.integrations.jev_service import JevService
from app.integrations.jev_translation import JevTranslation
from app.integrations.llm_service import get_llm, llm_trust_env
from app.integrations.qweather_service import QWeatherService
from app.integrations.unsplash_service import UnsplashService
from app.services.intent_router import TravelRouter
from app.services.preferences import PreferenceExtractor
from app.services.candidate_search import ReActCandidateSearch


class ServiceResources(AsyncExitStack):
    """每个请求各有一个普通实例；请求内按需创建，请求结束关闭连接。"""

    def __init__(self, provider="default"):
        super().__init__()
        self.provider = provider

    def _client(self, timeout=15, *, trust_env=True):
        client = httpx.AsyncClient(
            timeout=timeout,
            trust_env=trust_env,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
        )
        self.push_async_callback(client.aclose)
        return client

    @cached_property
    def amap(self) -> AmapService:
        return AmapService(get_settings().amap_api_key, self._client(trust_env=False))

    @cached_property
    def jev(self) -> JevService:
        return JevService(self._client())

    @cached_property
    def llm(self):
        trust_env = llm_trust_env(self.provider)
        options = {"http_async_client": self._client(timeout=35, trust_env=trust_env)}
        if not trust_env:
            client = httpx.Client(timeout=35, trust_env=False)
            self.callback(client.close)
            options["http_client"] = client
        return get_llm(provider=self.provider, **options)

    @cached_property
    def translator(self) -> JevTranslation:
        return JevTranslation(self.llm)

    @cached_property
    def candidate_search(self) -> ReActCandidateSearch:
        return ReActCandidateSearch(self.llm, sync_invoke=True)

    @cached_property
    def weather(self) -> QWeatherService:
        return QWeatherService(client=self._client(timeout=12, trust_env=False))

    @cached_property
    def unsplash(self) -> UnsplashService:
        return UnsplashService(get_settings().unsplash_access_key, self._client())

    @cached_property
    def extractor(self) -> PreferenceExtractor:
        return PreferenceExtractor(self.llm)

    @cached_property
    def router(self) -> TravelRouter:
        return TravelRouter(self.jev)
