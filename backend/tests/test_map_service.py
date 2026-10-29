"""地图应支持起点加六站，并在超出支持数量时提示。"""
import unittest
from unittest.mock import AsyncMock

from app.services.map_service import get_static_map


class StaticMapPointLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_six_stop_walk_includes_start_and_each_stop(self):
        amap = type('Amap', (), {})()
        amap.static_map = AsyncMock(return_value=(b'image', 'image/png'))
        points = ';'.join(f'{121.40 + index / 1000},31.23' for index in range(7))

        image, content_type = await get_static_map(points, amap)

        self.assertEqual((image, content_type), (b'image', 'image/png'))
        self.assertEqual(len(amap.static_map.await_args.args[0]), 7)

    async def test_rejects_more_than_ten_points(self):
        amap = type('Amap', (), {})()
        amap.static_map = AsyncMock()
        points = ';'.join(f'{121.40 + index / 1000},31.23' for index in range(11))

        with self.assertRaisesRegex(ValueError, '2至10'):
            await get_static_map(points, amap)
        amap.static_map.assert_not_awaited()
