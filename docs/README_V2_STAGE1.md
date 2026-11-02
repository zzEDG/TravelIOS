# TravelOS V2 第一阶段：CityWalk 调试说明

第一阶段已新增独立的 CityWalk API 与行程状态保存；旧前端继续使用原接口。V2 尚未实现自然语言主入口、Attraction、Food 和顶层 Jev Router。第一阶段的同步 `TravelSkill` 契约只约束 `execute()`；检索、决策、增强仍由现有 `CityWalkAgent.plan()` 完成，后续增加其他 Skill 时再按共同需求拆分。

## 启动与入口

在 WSL 的 `agents-learn` 环境运行后端：

```bash
cd /Projects/fullstack-learning-agent/backend
conda run --no-capture-output -n agents-learn uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

浏览器打开 `http://127.0.0.1:8000/docs`，展开 `TravelOS V2 CityWalk`。当前前端仍走 `/api/walk/plan`；V2 可以先在 API 文档中调试。

## VS Code 断点调试与环境变量

在 VS Code 的 WSL 窗口打开项目根目录，进入“运行和调试”，选择“调试后端 API（WSL）”，然后按 F5。这个入口使用 agents-learn 环境，在导入 FastAPI 应用前从交互式 Bash 补充 ~/.bashrc 中的 API 配置，因此能命中 lifespan() 和请求处理函数中的断点。调试时不启用热重载，避免断点落在另一个进程。启动后访问 http://127.0.0.1:8000/docs 发起请求。

VS Code 启动的 Python 进程通常不会自行读取 ~/.bashrc；后端配置现在会在 WSL 中缺少必需密钥时补读交互式 Bash 环境，因此直接调试 app/api/main.py 也能取到已导出的密钥。仍建议使用专用调试入口，它固定工作目录、Python 环境并关闭热重载。也可以在 backend/.env 填写真实密钥；不要直接复制 .env.example 的占位值。.env 已被 Git 忽略，调试入口不会打印密钥内容。修改环境配置后须停止并重新启动调试进程；已占用 8000 端口时先关闭旧后端。

## 新建、读取与续接

新建行程：`POST /api/v1/citywalk/plan`。

```json
{
  "city": "上海",
  "theme": "建筑与咖啡",
  "available_hours": 3,
  "target_stops": 2,
  "start_location": {
    "latitude": 31.2300,
    "longitude": 121.4600,
    "coordinate_system": "GCJ-02"
  },
  "additional_requirements": "想找安静的地方",
  "max_walking_meters": 8000
}
```

`coordinate_system` 可选 `GCJ-02`（默认，高德坐标）或 `WGS84`（设备 GPS）；后者经高德转换后再规划。经纬度 JSON 字段分别为纬度、经度；高德内部及返回的 `polyline` 坐标串为“经度,纬度”。`start_location` 是真实路线起点，第一条 `routes` 的 `from_poi_id` 为 `null`，终点是第一站。候选 POI 必须位于起点附近 4 公里内，至少有两处；`max_walking_meters` 是可选硬上限，包含起点到第一站及站点之间的步行距离。超过上限会返回错误，本阶段不会自动改乘其他交通方式。

`available_hours` 当前为 2–8 的整数，`target_stops` 为 2–5。成功响应的 `data` 包含 `trip_id`、`state_version`、地点、路段、总步行距离、天气、封面及提示。`meta.degraded_services` 列出缺失的天气、封面或路段数据。天气源仍是高德，暂不提供降雨概率，所以相关字段为 `null`。

读取已保存的行程：`GET /api/v1/trips/{trip_id}`。返回保存的 `TripState` 和最近一次规划结果。

续接时只传需要改变的字段，并带上读取到的版本号：

```json
{
  "trip_id": "trip_替换为实际返回值",
  "state_version": 1,
  "additional_requirements": "改成更安静的路线"
}
```

保存成功后版本号加 1；使用旧版本重复提交会得到 HTTP 409、业务码 `40005`。行程保存在 `backend/.runtime/trips.sqlite3`，有效期 30 天，运行数据已被 Git 忽略。API Key 不会进入该数据库。当前为本地单用户设计，没有账户隔离。

## 断点顺序

1. `backend/app/api/routes/citywalk.py`：`plan_citywalk()` 接收 HTTP 请求并映射错误。
2. `backend/app/application/planner.py`：`TravelPlanner.plan_citywalk()` 读取旧状态、合并字段、转换 WGS84 坐标、执行 Skill、按版本保存。
3. `backend/app/skills/citywalk.py`：`CityWalkSkill.execute()` 将 `TripState` 转为现有 `WalkRequest`，并把 `WalkPlan` 映射为 V2 响应。
4. `backend/app/agents/citywalk_agent.py`：`CityWalkAgent.plan()` 执行翻译、Jev 选词、高德搜索、Jev 选点、步行路径与文案生成；`origin_coordinates` 控制真实起点。
5. `backend/app/storage/trip_store.py`：SQLite `create/get/replace`；`replace()` 使用版本检查避免覆盖更新。
6. `backend/app/api/errors.py`：统一的 V2 参数错误外壳；旧接口仍使用原来的错误格式。

## 错误及兼容性

| HTTP / code | 含义 |
| --- | --- |
| 422 / 40001 | 请求字段无效；`details` 列出字段和原因 |
| 422 / 40002 | 规划被拒绝，例如附近地点不足或超出步行上限 |
| 404 / 40004 | 行程不存在 |
| 409 / 40005 | 行程版本冲突 |
| 410 / 40006 | 行程过期 |
| 502 / 50001 | Jev 调用失败 |
| 502 / 50002 | 高德坐标转换等服务失败 |
| 502 / 50004 | 中译英结果无效 |
| 503 / 50010 | 本地状态存储不可用 |

保留 `POST /api/walk/plan`、`GET /api/walk/map` 及当前前端响应结构。V2 新接口还没有顶层能力路由；`avoid_commercial` 目前作为 Jev 的偏好提示，不是硬过滤条件。规划链仍使用同步外部客户端，FastAPI 在同步路由线程池中执行。

验证命令：

```bash
cd /Projects/fullstack-learning-agent/backend
conda run --no-capture-output -n agents-learn python -m unittest discover -s tests -v
```

本机验证：14 项离线测试通过；真实 V2 请求返回 2 个高德 POI、2 段步行路线（包括起点到第一站）和可读取的 `trip_id`；真实 WGS84 坐标转换成功。实时 POI 与天气可能随时间变化，不应把示例结果当作固定快照。
