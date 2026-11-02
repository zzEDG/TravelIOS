# TravelOS 第三阶段：Food 与 Attraction

项目位置：/Projects/fullstack-learning-agent。实现沿用现有 FastAPI、Pydantic、Skill、服务适配及SQLite结构。

## 实现内容

- CityWalk 保留原规划和旧 /api/walk/plan；统一入口可以执行三个Skill。
- Food：高德周边餐厅检索、类别校验、去重、真实步行距离硬过滤，DeepSeek翻译后由OpenRouter Jev选择备选餐厅。每家路线都从同一起点出发。
- Attraction：高德景点检索、类别校验，Jev在预算可行的候选中选点；真实步行耗时加每站60分钟停留估计不得超过时长，累计步行不得超过可选距离预算。
- 自然语言参数提取扩展菜系、忌口、餐次、距离、半径、天气与景点数量；明确的博物馆等类别进入实际搜索关键词。
- /travel 可以在同一trip_id中切换能力；独立接口防止错误能力续接。SQLite保存结果类型、偏好和版本，旧CityWalk记录兼容。
- 新POI携带高德来源URL和抓取时间，地点、坐标和路线数据来自高德。无法核验的菜单/价格/开放时间/历史知识不生成冒充事实。

## 修改入口

| 文件 | 职责 |
|---|---|
| backend/app/models/travel.py | 三能力请求、返回联合类型、状态参数 |
| backend/app/skills/discovery.py | 共用真实搜索、类别过滤、翻译和Jev选点 |
| backend/app/skills/food.py | 餐厅备选与独立步行距离约束 |
| backend/app/skills/attraction.py | 连续景点路线、时间与累计距离约束 |
| backend/app/integrations/amap_service.py | 周边搜索、typecode解析、分钟向上取整 |
| backend/app/integrations/jev_translation.py | 新能力请求翻译 |
| backend/app/application/preferences.py | 三能力自然语言参数提取 |
| backend/app/application/orchestrator.py | 统一路由与能力切换 |
| backend/app/application/planner.py | 状态合并、结果保存、增强降级标记 |
| backend/app/storage/trip_store.py | 按routed_skill恢复联合计划类型 |
| backend/app/api/routes/citywalk.py、attraction.py、food.py、travel.py（共用接口集中在 travel.py） | 三子系统及统一/共用接口 |
| backend/tests/test_discovery.py | 新能力、距离/时长、版本、旧数据兼容测试 |

## 本机调试

在WSL终端运行：

    cd /Projects/fullstack-learning-agent/backend
    conda run --no-capture-output -n agents-learn uvicorn app.main:app --host 127.0.0.1 --port 8000

打开 http://localhost:8000/docs。使用接口规范中的上海坐标示例，先测试：
1. POST /api/v1/food/search，观察每家距离都不超过max_distance_meters。
2. POST /api/v1/attraction/plan，观察连续路段、estimated_minutes与预算。
3. GET /api/v1/trips/{trip_id}，确认latest_plan、current_skill和偏好。
4. POST /api/v1/travel，提交上次trip_id/state_version及“改去博物馆参观，安排两小时，只去一个景点”，确认切换后版本加一。
5. 再提交旧版本，应该返回409；修改距离为0，应该返回422。

前端现已接入三个子系统，启动后打开 http://localhost:5173，首页切换城市漫步、景点游览、餐厅推荐或对话规划。完整页面验证见 [前端说明](docs/FRONTEND_TRAVELOS.md)。

建议断点：api/routes/travel.py端点 → TravelOrchestrator.plan（仅统一入口）→ PreferenceExtractor.extract → TravelRouter.route → TravelPlanner.plan_food/plan_attraction → Skill.execute → DiscoverySkill.retrieve/choose → TripStore.create/replace。

## 验证记录（2026-10-11）

自动化测试命令：

    cd /Projects/fullstack-learning-agent/backend
    /home/zzz/miniconda3/envs/agents-learn/bin/python -m unittest discover -s tests -q

26项自动化测试全部通过；测试使用模拟外部服务，覆盖原CityWalk和新增能力。新增用例包括真实距离过滤、预算不足、非法Jev ID、无候选、坐标转换、HTTP参数/能力错误、版本冲突、SQLite恢复、统一切换、类别过滤以及旧JSON兼容。

真实服务通过FastAPI TestClient（执行lifespan）连接当前配置的高德、DeepSeek deepseek-chat和OpenRouter typesafe/jev-1.13；使用临时SQLite，不写入个人正式历史：
- Food，上海人民广场坐标，要求本帮菜，返回“圆苑(新世界城店)”，步行889米，HTTP200，SQLite恢复FOOD。
- Attraction，同一起点要求博物馆，返回“上海市历史博物馆”，步行428米，HTTP200，SQLite恢复ATTRACTION。
- 统一入口从Food切换到博物馆，返回“上海市历史博物馆”，偏好保留博物馆，版本2，HTTP200，恢复类型正确。
- 天气此次未返回有效数据，景点结果标记WEATHER降级并提示缺失；未将它当成天气联调成功。
- 修复真实联调发现的两处问题：高德宽类别与具体关键词混用导致候选不符；自然语言目的地类别仅进入路由摘要、未进入检索偏好。

精简真实结果见 docs/stage3_smoke.json（不包含密钥）。地点和路线依赖第三方当前数据，之后调用可能不同。

## 当前边界与后续

本轮交付三个可调用的后端子系统与统一契约。前端三个表单及对话、结果页现已接入；Wikimedia实体及知识来源、QWeather天气、多用户鉴权、异步任务与流式进度、用户访问/拒绝反馈接口尚未实现。下一步可接来源知识与反馈，再做地图交互和全局路线优化。

天气沿用高德；没有菜单或过敏原数据，忌口只作为软偏好。景点停留60分钟是预算估计，不代表现场耗时已核实。候选最多8个、检索最多3个关键词以控制外部调用规模。

位置输入与路由拆分补充：起点现使用地名/地址搜索并选择候选，接口及代码结构见 [路由结构](docs/ROUTES.md)。

后续天气更新：现已接入和风v1每日预报，上面的高德天气联调结果为历史记录。当前配置、限制及34项测试记录见 [QWEATHER.md](docs/QWEATHER.md)。
