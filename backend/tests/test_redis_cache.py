import unittest
from unittest.mock import AsyncMock

import httpx

from app.services.redis_cache import RedisCache, cache_key
from app.integrations.amap_service import AmapService


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_key_respects_parameters_and_direction(self):
        self.assertEqual(cache_key("amap", "search", {"a": "1", "b": "2"}),
                         cache_key("amap", "search", {"b": "2", "a": "1"}))
        self.assertNotEqual(cache_key("amap", "walking", {"origin": "a", "destination": "b"}),
                            cache_key("amap", "walking", {"origin": "b", "destination": "a"}))

    async def test_cache_error_is_a_miss(self):
        client = AsyncMock()
        client.get.side_effect = OSError()
        client.set.side_effect = OSError()
        cache = RedisCache(client)
        self.assertIsNone(await cache.get("key"))
        await cache.set("key", {}, 10)

    async def test_amap_cache_hit_and_separate_keywords(self):
        values = {}
        class Cache:
            async def get(self, key): return values.get(key)
            async def set(self, key, data, ttl): values[key] = data
        calls = []
        async def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"status": "1", "pois": [{"id": "1", "name": "park", "location": "121,31"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            amap = AmapService("test", client, Cache())
            await amap.search("city", "park")
            await amap.search("city", "park")
            await amap.search("city", "cafe")
            self.assertEqual(len(calls), 2)
        self.assertEqual(len(values), 2)

    async def test_around_cache_preserves_parent_and_separates_origins(self):
        values = {}
        class Cache:
            async def get(self, key): return values.get(key)
            async def set(self, key, data, ttl): values[key] = (data, ttl)
        class Adapter(Cache):
            async def get(self, key):
                entry = values.get(key)
                return entry[0] if entry else None
        calls = []
        async def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"status": "1", "pois": [{"id": "1", "name": "park", "location": "121.001,31", "parent": "scenic-area"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            amap = AmapService("test", client, Adapter())
            first = await amap.search_around("city", 121, 31, "park", 1000, "")
            second = await amap.search_around("city", 121, 31, "park", 1000, "")
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)
            self.assertEqual(second[0].parent_id, "scenic-area")
            self.assertEqual(second[0].keyword, "park")
            await amap.search_around("city", 121.01, 31, "park", 1000, "")
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0].url.params["location"], "121.000000,31.000000")
            self.assertEqual(calls[0].url.params["offset"], "25")
        data, ttl = next(iter(values.values()))
        self.assertEqual(ttl, 1800)
        self.assertEqual(data["pois"][0]["parent"], "scenic-area")

    async def test_empty_and_failed_searches_are_not_cached(self):
        cache = AsyncMock()
        cache.get.return_value = None
        for response in ({"status": "1", "pois": []},
                         {"status": "0", "infocode": "10001", "info": "INVALID_USER_KEY"}):
            with self.subTest(response=response):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=response)
                )) as client:
                    amap = AmapService("test", client, cache)
                    if response["status"] == "1":
                        self.assertEqual(await amap.search("city", "park"), [])
                    else:
                        with self.assertRaises(ValueError):
                            await amap.search("city", "park")
        cache.set.assert_not_awaited()

    async def test_walking_cache_requires_actual_paths(self):
        from app.integrations.amap_service import Place

        values = {}
        class Cache:
            async def get(self, key): return values.get(key)
            async def set(self, key, data, ttl): values[key] = data
        responses = iter([
            {"status": "1", "route": {"paths": []}},
            {"status": "1", "route": {"paths": [{"distance": "100", "duration": "61"}]}},
        ])
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=next(responses))
        origin = Place("a", "a", "", "", 121, 31, "", "")
        destination = Place("b", "b", "", "", 121.01, 31, "", "")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            amap = AmapService("test", client, Cache())
            self.assertIsNone((await amap.walking_route(origin, destination)).distance_m)
            route = await amap.walking_route(origin, destination)
            self.assertEqual((route.distance_m, route.duration_min), (100, 2))
            self.assertEqual(await amap.walking_route(origin, destination), route)
        self.assertEqual(len(calls), 2)
