"""Unsplash 主题氛围图；仅使用官方返回的热链与署名信息。"""

from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx

from app.models.walk import CoverPhoto


class UnsplashService:
    def __init__(self, access_key: str, client: Optional[httpx.AsyncClient] = None):
        self.access_key = access_key
        self.client = client or httpx.AsyncClient(timeout=15)

    async def cover(self, query: str) -> Optional[CoverPhoto]:
        if not self.access_key:
            return None
        response = await self.client.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "orientation": "landscape", "per_page": "1"},
            headers={"Authorization": f"Client-ID {self.access_key}", "Accept-Version": "v1"},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            return None
        photo = results[0]
        user = photo.get("user") or {}
        url = (photo.get("urls") or {}).get("regular") or ""
        profile = (user.get("links") or {}).get("html") or ""
        photo_url = (photo.get("links") or {}).get("html") or ""
        if not all(urlparse(value).scheme == "https" for value in (url, profile, photo_url)):
            return None

        def with_attribution(link: str) -> str:
            parsed = urlsplit(link)
            params = dict(parse_qsl(parsed.query))
            params.update(utm_source="citywalk-agent", utm_medium="referral")
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), parsed.fragment))

        return CoverPhoto(
            url=url,
            photographer=str(user.get("name") or "Unsplash 摄影师"),
            photographer_url=with_attribution(profile),
            unsplash_url=with_attribution(photo_url),
            alt=str(photo.get("alt_description") or "城市主题氛围示意图"),
        )
