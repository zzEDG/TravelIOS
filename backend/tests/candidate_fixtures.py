"""旧规划测试隔离新增检索模型，ReAct 工具循环另有专门测试。"""
import httpx
from app.integrations.amap_service import AmapServiceError
from app.services.candidate_search import CandidateSearchResult


class OfflineCandidateSearch:
    async def search(self, amap, *, city, keyword, origin, radius, types='', context=None):
        try:
            places = (await amap.search_around(city,*origin,keyword,radius,types)
                      if origin else await amap.search(city,keyword))
            return CandidateSearchResult(places)
        except (AmapServiceError, httpx.HTTPError):
            return CandidateSearchResult([], failed=True)
