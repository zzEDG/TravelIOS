"""地图业务：解析和校验坐标，再调用地图集成；不依赖 HTTP 框架。"""
from app.integrations.amap_service import AmapService, AmapServiceError

class MapServiceError(RuntimeError):
    """外部地图不可用时交给控制器转换成接口错误。"""

async def get_static_map(points: str, amap: AmapService, *, start: int | None = None) -> tuple[bytes, str]:
    try:
        coordinates = []
        for pair in points.split(";"):
            lon, lat = (float(value) for value in pair.split(","))
            if not (73 <= lon <= 135 and 3 <= lat <= 54):
                raise ValueError("坐标超出支持范围")
            coordinates.append((lon, lat))
        if not (2 <= len(coordinates) <= 10):
            raise ValueError("需要2至10个地点（含起点）")
        return await amap.static_map(coordinates, start=start) if start is not None else await amap.static_map(coordinates)
    except AmapServiceError as exc:
        raise MapServiceError("高德地图服务暂不可用") from exc
    except (ValueError, TypeError):
        raise
    except Exception as exc:
        raise MapServiceError("地图暂不可用") from exc
