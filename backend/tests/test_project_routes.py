"""验证集中注册、旧启动/URL兼容及数据库位置，不请求外部服务。"""
from unittest.mock import AsyncMock
import sys
import unittest
from unittest.mock import PropertyMock
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import app
from app.api.main import app as legacy_app
from app.models.trip_store import DEFAULT_DB_PATH


class ProjectRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_versioned_routes_and_legacy_urls_registered_once(self):
        self.assertIs(app,legacy_app)
        paths=app.openapi()["paths"]
        expected={
            "/api/v1/citywalk/plan":"post", "/api/v1/attraction/plan":"post",
            "/api/v1/food/search":"post", "/api/v1/travel":"post",
            "/api/v1/locations/search":"post", "/api/v1/trips/{trip_id}":"get",
            "/api/v1/maps/static":"get", "/api/walk/plan":"post", "/api/walk/map":"get",
        }
        for url,method in expected.items():
            self.assertIn(method,paths[url])
            self.assertNotIn("/v1/v1",url)
        operations=[v["operationId"] for path in paths.values() for v in path.values() if isinstance(v,dict) and "operationId" in v]
        self.assertEqual(len(operations),len(set(operations)))
        self.assertTrue(paths["/api/walk/plan"]["post"]["deprecated"])
        self.assertEqual(DEFAULT_DB_PATH.resolve(),Path(__file__).resolve().parents[1]/".runtime/trips.sqlite3")

    async def test_new_and_legacy_maps_share_implementation_but_keep_error_contracts(self):
        with patch("app.dependencies.ServiceResources.amap", new_callable=PropertyMock) as service, patch("builtins.print"),TestClient(app) as client:
            service.return_value.static_map = AsyncMock(return_value=(b"image","image/png"))
            for path in ("/api/v1/maps/static","/api/walk/map"):
                response=client.get(path,params={"points":"121.47,31.23;121.48,31.24"})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.content,b"image")
            new=client.get("/api/v1/maps/static",params={"points":"bad"})
            old=client.get("/api/walk/map",params={"points":"bad"})
            self.assertEqual(new.status_code,422)
            self.assertEqual(new.json()["code"],40002)
            self.assertEqual(old.status_code,422)
            self.assertIn("detail",old.json())
