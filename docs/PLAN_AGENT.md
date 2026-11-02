# 主 Agent 与三个子 Agent

统一入口 `POST /api/v1/travel` 接收用户需求，由 [MainAgent](backend/app/agents/main_agent.py) 读取行程上下文、抽取偏好并路由到一个子 Agent。完整目录和调试顺序见 [后端阅读指南](docs/PROJECT_STRUCTURE.md)。

```text
用户需求 → MainAgent
            ├─ CITYWALK   → CityWalkAgent   → 主题步行路线
            ├─ ATTRACTION → AttractionAgent → 景点游览安排
            ├─ FOOD       → FoodAgent       → 附近餐厅备选
            └─ CLARIFY    → 追问目标或缺失信息
规划成功 → TripService → TripStore → 新版本及历史快照
```

## 模型与代码如何分工

| 环节 | 实现 | 职责 |
|---|---|---|
| 需求理解 | `PreferenceExtractor` + 配置的生成模型 | 从中文需求提取偏好补丁及英文意图摘要，不凭空补充城市或距离 |
| 需求路由 | `TravelRouter` + Jev Choice | 根据本轮需求和旧行程选择三个子 Agent 之一，或要求澄清 |
| 选词与选点 | 子 Agent + `JevService` | 在预设搜索词、已核实的高德 POI 中评分或选择，不生成新 POI |
| 约束与路线 | 各子 Agent 的 Python 逻辑 | 核实类型、真实步行距离、时间预算，组装路线或备选项 |
| 翻译与文案 | `JevTranslation`、步行文案链 | 保留 POI ID 映射，将说明译成英文供 Jev 判断，生成中文步行说明 |
| 上下文和历史 | `TripService`、`TripStore` | 合并偏好、校验版本、保存快照，以历史版本为基础继续修改 |

所有外部服务封装在 `backend/app/integrations/`。默认模型、密钥变量与启动方式见 [README](README.md)，天气配置见 [docs/WEATHER.md](docs/WEATHER.md)。

## 三个子 Agent 各自做什么

### CityWalkAgent

[实现](backend/app/agents/citywalk_agent.py)。内置建筑与咖啡、书店与慢生活、艺术与展览、公园与自然、街巷与小吃五种主题。Jev 对主题搜索词评分，通过高德查询真实地点并去重，按主题与偏好选出 2–5 站；代码安排步行顺序、查询真实路段并校验步行上限，补充和风天气及 Unsplash 氛围封面。

`execute(TripState)` 是统一子 Agent 入口。步行算法复用 `plan_legacy`，同时支持已经发布的 `POST /api/walk/plan`；不会再经过一层 CityWalkSkill 包装。

### AttractionAgent

[实现](backend/app/agents/attraction_agent.py)。按兴趣搜索景点，校验景点类型，在候选中选择地点并依次计算步行路线。累计步行和停留时长必须满足时间、距离预算；每站停留根据目标数量和可用时长按 30–60 分钟估计，可选择是否查询天气。

### FoodAgent

[实现](backend/app/agents/food_agent.py)。按菜系搜索餐厅，以从起点出发的真实步行距离过滤，再根据用餐类型、口味和忌口选择备选项。每家餐厅都是独立候选，不把多家餐厅串成游览路线；未核实菜单和过敏原。

景点和餐厅继承 `DiscoveryAgent`，共用周边检索、去重、候选选择、路线查询和结果组装。所有子 Agent 继承 `SubAgent`，共用参数准备约定。两个基类均为抽象类，主 Agent 的注册表只有三个可执行子 Agent。

## 续接和历史编辑

- 三个子系统的表单通过 `MainAgent.plan_for` 明确指定目标，与对话入口共用状态合并、执行和保存逻辑。
- 后续对话传 `trip_id` 和最新 `state_version`，主 Agent 使用历史上下文判断继续当前子系统还是切换。
- 选择旧版本修改时增加 `base_version`；输入偏好来自该历史快照，保存时仍校验最新版本以防覆盖并发修改。
- 澄清、规划失败或版本冲突都不保存新规划；成功后追加新快照，保留旧版本。
- 历史查询直接使用行程服务，不初始化主 Agent 或子 Agent。

## 实现边界

每轮需求路由到一个子 Agent，目前不自动把三个子 Agent 合成为跨子系统的全天行程。子 Agent 依赖外部数据和模型判断，不保证步行顺序全局最优；地点开放时间、票价、预约、餐厅菜单等需要另行确认。天气和图片不可用时保留可用规划并说明缺失。

`tests/test_main_agent.py` 验证路由只调用一个子 Agent、按需初始化、澄清不执行、实际三子 Agent 调用链及统一状态处理；其余回归覆盖历史版本、约束与 HTTP 契约。


明确的数量续接由 services/count_preferences.py 解析，并沿用当前子 Agent；参数范围、相对增减和数量不足说明见 [数量接口说明](docs/TRAVELOS_API.md)。
