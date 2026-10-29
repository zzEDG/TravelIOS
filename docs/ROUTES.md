# TravelOS 后端控制器与接口

接口控制器集中在 `backend/app/controllers/`，应用入口 `backend/app/main.py` 负责注册。规划控制器调用 `agents/main_agent.py` 委派给三个子 Agent，历史、位置和地图由 `services/` 处理，数据定义和行程存储位于 `models/`。

| 控制器文件 | 接口 | 职责 |
|---|---|---|
| `citywalk.py` | `POST /api/v1/citywalk/plan` | 城市漫步规划与修改 |
| `attraction.py` | `POST /api/v1/attraction/plan` | 景点游览规划与修改 |
| `food.py` | `POST /api/v1/food/search` | 餐厅推荐与修改 |
| `travel.py` | `POST /api/v1/travel` | 根据自然语言选择并调用子系统 |
| `trips.py` | `GET /api/v1/trips` | 搜索、分页查询各子系统规划历史 |
| `trips.py` | `GET /api/v1/trips/{trip_id}` | 读取最新规划或指定历史版本 |
| `trips.py` | `GET /api/v1/trips/{trip_id}/history` | 查看行程历次版本 |
| `locations.py` | `POST /api/v1/locations/search` | 搜索起点名称或地址 |
| `maps.py` | `GET /api/v1/maps/static` | 返回地图图片 |
| `citywalk.py` 的 `legacy_router` | `POST /api/walk/plan`、`GET /api/walk/map` | 旧版接口兼容 |

`controllers/errors.py` 统一转换 V1 接口异常。旧接口仍使用原来的 `detail` 错误格式；旧版地图复用地图控制器的响应处理。

## 注册与路径拼接

```python
# backend/app/main.py
from app.controllers import citywalk, attraction, food, travel, trips, locations, maps

app.include_router(citywalk.router, prefix="/api/v1")
app.include_router(attraction.router, prefix="/api/v1")
app.include_router(food.router, prefix="/api/v1")
app.include_router(travel.router, prefix="/api/v1")
app.include_router(trips.router, prefix="/api/v1")
app.include_router(locations.router, prefix="/api/v1")
app.include_router(maps.router, prefix="/api/v1")
app.include_router(citywalk.legacy_router, prefix="/api")
```

例如 `food.py` 的 `router.prefix` 是 `/food`，函数装饰器是 `@router.post("/search")`，最终路径为 `/api/v1/food/search`。

## 断点顺序

- 城市漫步：`controllers/citywalk.py` → `MainAgent.plan_for("CITYWALK", ...)` → `agents/citywalk_agent.py`。
- 景点游览：`controllers/attraction.py` → `MainAgent.plan_for("ATTRACTION", ...)` → `agents/attraction_agent.py`。
- 餐厅推荐：`controllers/food.py` → `MainAgent.plan_for("FOOD", ...)` → `agents/food_agent.py`。
- 统一对话：`controllers/travel.py` → `MainAgent.plan` → `PreferenceExtractor` → `TravelRouter` → 选中的子 Agent。
- 共用规划状态与保存：`MainAgent._dispatch` → `TripService.prepare` → `子 Agent.execute` → `TripService.save`。
- 行程历史：`controllers/trips.py` → `services/trip_service.py` → `models/trip_store.py`。
- 起点搜索：`controllers/locations.py` → `services/location_service.py` → `integrations/amap_service.py`。
- 地图图片：`controllers/maps.py` → `services/map_service.py` → `integrations/amap_service.py`。

`intent_router.py` 属于业务服务，它决定选择哪个规划子系统。HTTP 路由注册始终在控制器和应用入口中。

详细目录与阅读示例见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)，接口参数见 [TRAVELOS_API.md](TRAVELOS_API.md)。
