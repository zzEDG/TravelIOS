# TravelOS 三子系统接口规范

本文是当前实现的契约；阶段文档保留为历史设计记录。Base URL 为 /api/v1；Content-Type 为 application/json。接口内部使用异步 I/O，规划完成后一次返回 JSON。旧 /api/walk/plan 和地图接口保留；新地图统一使用GET /api/v1/maps/static。前端已接入三个结构化表单、对话规划及行程结果页。

## 共同规则

坐标对象：latitude 为纬度（-90..90）、longitude 为经度（-180..180）、coordinate_system 为 GCJ-02（默认）或 WGS84。WGS84 由高德转换后才检索和计算路线。polyline 使用经度,纬度;经度,纬度。

新建不传 trip_id/state_version；续接必须同时传两者。新建必须有 city 和对应位置字段；CityWalk 的 theme 为可选名称，应提供想去的地方、顺序或补充要求，旧主题文字也可作为兼容输入。续接省略的字段保持旧值；列表传 [] 可清空，null 通常表示不修改；travel_date、max_walking_meters、end_with 显式 null 表示取消对应要求。默认值仅对新建生效。同一 trip_id 可通过统一入口切换能力，公共偏好保留；餐厅口味/距离和景点半径/天气参数各自保留。切回 CityWalk 时默认时长3小时、站数按其规则初始化，顺序和末站重新初始化；切到 Attraction 默认目标2站，显式输入优先。独立接口不允许用另一能力的 trip_id 续接。

成功返回 code=0、message=success、data、meta。meta 含 schema_version、decision_source=JEV、route_confidence（仅统一路由，集中程度而非正确率）、fallback_applied、degraded_services。有真实地点但天气等增强缺失时仍成功并标记；没有可用候选或没有满足硬约束的路线时返回错误，失败/澄清均不更新状态。

SQLite 路径为 backend/.runtime/trips.sqlite3；trips 保存最新状态，trip_history 保存每次成功规划的状态和结果快照。续接有效期 30 天；历史快照持续保留，过期后可查看并显式重新规划。每次成功续接 state_version 加一，在同一事务中检查旧版本、更新最新状态并追加历史。旧数据库首次载入时只迁移实际保留的最新版本，不能恢复已覆盖的版本。API Key 不进入存储。

## 1. CityWalk：POST /citywalk/plan

新建示例：

    {"city":"上海","interests":["公园","咖啡馆"],"visit_order":["公园"],"end_with":"咖啡馆","available_hours":3,"target_stops":3,
     "start_location":{"latitude":31.23,"longitude":121.46}}

| 字段 | 类型/范围 | 说明 |
|---|---|---|
| trip_id / state_version | string / integer>=1，可选 | 续接标识 |
| city | string，2..30 字符 | 新建必填 |
| theme | string，2..40 字符，可选 | 行程名称，默认“自由漫步”；不再限定主题类别 |
| start_location | GeoLocation | 新建必填 |
| available_hours | integer，2..8，默认3 | 按已核实步行与估计停留检查时间预算，不含排队、返程 |
| target_stops | integer，2..5 | 默认依据时长 |
| max_walking_meters | integer，500..30000，可空 | 全路线硬距离上限 |
| avoid_commercial | boolean，默认false | 软偏好 |
| travel_date | date，可空 | 所选日期天气 |
| additional_requirements | string，最多500字符 | 补充偏好 |
| interests / dislikes | string数组，每项1..40字符，最多10项 | 表单提交完整列表；interests按重要程度排列，不代表游览顺序 |
| visit_order | string数组，每项1..40字符，最多10项 | 可选的逐站先后要求；重复类别允许，但不能重复同一POI；空数组取消顺序 |
| end_with | string，1..40字符，可空 | 希望安排在最后一站的类别或地点；显式null取消 |

data.routed_skill=CITYWALK；places 是逐站决策的实际顺序，选完后不再按最近距离重排。routes 连续且包含起点到第一站。每轮先核实当前位置到候选的真实步行路线，再过滤估计时间和累计距离预算，最后由 Jev 选择。路线不可核实的候选排除，天气和封面可降级。初始检索为起点周边4km，每种偏好独立保留候选，同轮最多核实12个候选。

补充要求会整理为检索偏好、主次、顺序和末站，不只作为展示文字。对话新增偏好默认合并，明确删除或整体替换才移除旧偏好；新增一种偏好不自动修改总地点数。原theme字段保留用于旧记录和名称兼容，不再映射五种固定类别。完整流程见 [自由偏好与逐站规划](FREE_PREFERENCE_WALK.md)。

## 2. Attraction：POST /attraction/plan

新建示例：

    {"city":"上海","available_hours":3.5,"interests":["博物馆","历史建筑"],
     "target_stops":2,"location":{"latitude":31.23,"longitude":121.46},
     "radius_meters":5000,"max_walking_meters":8000,"consider_weather":true}

| 字段 | 类型/范围 | 说明 |
|---|---|---|
| trip_id / state_version | 同上 | 可续接 |
| city / location | string / GeoLocation | 新建必填 |
| available_hours | number，0.5..12，默认3 | 停留估计+已核实步行耗时的硬预算 |
| interests | string数组，最多10项，每项1..40字符 | 默认景点；最多前三项作为检索关键词 |
| target_stops | integer，1..5，默认2 | 目标上限，约束不足时可缩短 |
| radius_meters | integer，500..10000，默认5000 | 起点周边直线搜索范围 |
| max_walking_meters | integer，100..30000，可空 | 全路线步行硬上限 |
| consider_weather | boolean，默认true | 返回天气提示，不推断室内属性 |
| travel_date | date，可空 | 查询指定日期 |
| additional_requirements / dislikes | string / string数组 | 软偏好 |

data.routed_skill=ATTRACTION；与 CityWalk 共用地点和路段结构，routes 为连续步行路线。每站按目标数量和总时长分配30–60分钟停留估计，是真实游览时长的近似值；无法取得距离/耗时的站点不纳入预算路线。最多5站，最终顺序由 Jev 候选选择与预算可行性共同决定。不得声称营业时间、票价、预约、室内环境或史实经过验证。knowledge 缺少已核实来源时为 null。

## 3. Food：POST /food/search

新建示例：

    {"city":"上海","meal_type":"dinner","cuisine_preference":["本帮菜"],
     "avoid":["过于辛辣"],"center_location":{"latitude":31.23,"longitude":121.46},
     "max_distance_meters":1500,"limit":3}

| 字段 | 类型/范围 | 说明 |
|---|---|---|
| trip_id / state_version | 同上 | 可续接 |
| city / center_location | string / GeoLocation | 新建必填 |
| meal_type | breakfast/lunch/dinner/snack，默认dinner | 偏好，不代表营业时间已核验 |
| cuisine_preference / avoid | string数组，最多10项，每项1..40字符 | 完整口味偏好列表；默认空 |
| max_distance_meters | integer，100..10000，默认1500 | 从起点到每家餐厅的真实步行距离上限 |
| limit | integer，1..5，默认3 | 最多返回几家，可能不足 |
| additional_requirements | string，最多500字符 | 软偏好 |

data.routed_skill=FOOD，route_semantics=ALTERNATIVES。places.step_order 表示推荐排名，不是串联用餐路线；routes 全部 from_poi_id=null，各自从同一起点到对应餐厅。estimated_minutes 和 total_walking_meters 为 null，不能把备选餐厅路线相加。meal_type 和 max_distance_meters 回显有效值。没有路线或超过距离上限的餐厅排除。

avoid 是根据 POI 名称/类别判断的软偏好，没有菜单、食材、过敏原数据，不能保证少辣或过敏安全；营业状态、评分、价格不编造，metadata 不可用时为 null。每个地点返回高德 URI source_url 和 retrieved_at，表明地点来源，不代表独立验证全部属性。

## 共享响应结构

data 包含 trip_id、state_version、routed_skill、title、summary、origin、places、routes、estimated_minutes、total_walking_meters、weather、cover、notes。Food 额外含 route_semantics、meal_type、max_distance_meters。

places 每项：step_order、poi、why_selected、stay_minutes。poi 含 id/name/category/address/location/source=AMAP/source_url/retrieved_at/knowledge/metadata。why_selected 使用模板说明可验证的地点类别与路线事实，不声称 Jev 返回了文字理由。Food stay_minutes=0，表示不安排串联停留。

routes 每项：from_poi_id（null 表示起点）、to_poi_id、distance_meters、duration_minutes、polyline。距离单位米、时间单位分钟。天气使用和风预报 WeatherState（source=QWEATHER，兼容旧高德记录）；缺失则 null，降雨概率未知时不填数值。Food 不请求天气/封面，其 null 不算服务降级；Attraction 暂不请求封面。

## 起点位置搜索：POST /locations/search

请求：{"city":"上海","query":"人民广场地铁站"}。city为2..30字符，query为1..100字符，自动去除首尾空白；空字符串拒绝。

响应code=0、message=success、data为PlaceCandidate数组（最多8项）、meta为共同格式。候选含id、name、address、category、location、source_url、retrieved_at。优先高德城市内POI检索，无POI结果时回退地址地理编码；地理编码id为geocode:序号，只在当前候选中用于选择，不是高德POI ID。没有匹配返回422/40002；服务失败按共同错误契约返回。

前端让用户选择候选，不自动采用第一个。用户只填写城市与地名/地址，坐标由服务解析为GCJ-02，再传入各子系统。三个独立请求及统一请求支持origin_name（string，最多200字符），保存用于起点回显；不影响硬路线计算。旧记录缺少该字段时默认空字符串，仍可沿用已保存坐标。

## 统一入口与读取

POST /travel 接收 message（1..1000字符）、可选 city/location/travel_date、可选成对的 trip_id/state_version。DeepSeek 抽取能力参数和英文摘要，Jev Choice 路由，主 Agent 委派给 CityWalkAgent、AttractionAgent 或 FoodAgent。能力参数保持自身命名；自然语言取消不确定硬上限时推荐使用独立接口显式 null。

CLARIFY 响应 data 含 routed_skill=CLARIFY、question、trip_id、state_version，无新状态写入。用户明确切换能力可保留同一 trip_id，版本加一。

GET /trips/{trip_id} 返回 data.state、data.latest_plan 与 data.latest_version，按 routed_skill 区分计划类型。

## 规划历史与版本编辑

- `GET /trips?skill=CITYWALK&q=上海&page=1&page_size=5`：skill 可选 CITYWALK / ATTRACTION / FOOD；q 检索城市、标题和起点，最多100字。page >= 1，page_size 为1..50，默认5。每条行程在每个子系统显示该子系统最后一次成功规划，切换能力不会使原子系统历史消失。结果按更新时间倒序。
- `GET /trips/{trip_id}/history?page=1&page_size=5`：分页读取同一行程的全部已保存版本，包含能力切换；无此行程返回404。
- 上述接口的 data 为 `{items,total,page,page_size}`。每项包含 trip_id、state_version（这条历史的版本）、latest_version（行程当前最新版本）、skill、city、title、summary、origin_name、place_count、created_at、updated_at、expired（当前行程是否超过续接有效期）。
- `GET /trips/{trip_id}?version=1`：读取指定历史快照，data.state / latest_plan 对应指定版本，latest_version 仍指行程的当前最新版本。历史读取不受30天有效期限制；省略 version 保持原续接读取规则，过期返回410。未知版本返回404。
- 三个子系统规划接口和 `/travel` 均接受可选 `base_version`（整数>=1，必须提供 trip_id / state_version）。base_version 指定作为编辑基础的历史版本，state_version 必须是当前最新版本。例如当前版本4，要修改历史版本2：`{"trip_id":"trip_...","state_version":4,"base_version":2,"additional_requirements":"更安静"}`。成功保存为版本5；版本2和4保留。若其他请求先保存则返回409，不自动重试。
- 选择历史版本后，未传的偏好沿用该历史快照。独立接口要求历史基础与所选子系统一致；统一入口允许以该快照为基础切换能力。显式 base_version 可重新规划过期行程，并在成功后延长有效期；失败不改变最新行程或历史。

## 错误契约

错误响应含 code、message、data=null；参数错误含 details=[{field,message}]。

| HTTP / code | 含义 |
|---|---|
| 422 / 40001 | 输入字段/范围/续接关系无效 |
| 422 / 40002 | 无合适地点、预算不足、能力续接不匹配 |
| 404 / 40004 | 行程不存在 |
| 409 / 40005 | 旧版本冲突 |
| 410 / 40006 | 行程过期 |
| 502 / 50001 | Jev 决策无效或调用失败 |
| 502 / 50002 | 高德核心服务失败 |
| 502 / 50003 | 外部网络服务失败 |
| 502 / 50004 | 英文翻译无效 |
| 502 / 50005 | 偏好抽取格式无效 |
| 502 / 50006 | ReAct 候选检索或审核失败、超时、超过限定轮次 |
| 503 / 50010 | SQLite 存储失败 |
| 502 / 50000 | 其他处理失败 |

## 响应字段类型补充

| 字段 | 类型与语义 |
|---|---|
| code / message | 成功固定0 / success |
| data.trip_id / state_version | string / integer，成功续接递增 |
| data.routed_skill | CITYWALK / ATTRACTION / FOOD |
| data.title / summary | string；新能力采用中文事实模板 |
| data.origin | GeoLocation，归一化为GCJ-02 |
| data.places / routes | PlannedPlace[] / RouteSegment[] |
| data.estimated_minutes | integer；Food固定null |
| data.total_walking_meters | integer或null |
| data.weather / cover | WeatherState或null / CoverPhoto或null |
| data.notes | string[] |
| data.narration | TripNarration或null；各子功能的讲述、评价和修改说明 |
| meta.schema_version | 固定字符串1.0 |
| meta.decision_source | 固定JEV |
| meta.route_confidence | number(0..1)或null，仅统一路由产生 |
| meta.fallback_applied | boolean，讲述降级为基础评价时为true |
| meta.degraded_services | string[]，可能WEATHER、UNSPLASH、AMAP_ROUTE、NARRATOR |

PlannedPlace：step_order为integer（从1开始）；poi为PlaceCandidate；why_selected为string；stay_minutes为integer。

PlaceCandidate：id/name/category/address为string；location为GeoLocation；source固定AMAP；source_url为string或null；retrieved_at为ISO8601日期时间或null；knowledge/metadata为对象或null。新Food/Attraction填来源URL与抓取时间，旧CityWalk未填时允许null。

RouteSegment：from_poi_id为string或null；to_poi_id为string；distance_meters/duration_minutes为integer或null；polyline为string或null。新能力仅接纳距离和耗时可用的路线，polyline缺失时仍可能降级。

WeatherState：source为AMAP或QWEATHER；forecast_date为YYYY-MM-DD；day_condition/night_condition为string；day_temperature_c/night_temperature_c/rain_probability为number或null；is_outdoor_favorable为boolean或null。新景点能力不依天气自行推断室内或露天属性。

CoverPhoto沿用CityWalk：url、photographer、photographer_url、unsplash_url、alt均为string；Food/Attraction固定null，不请求Unsplash。

TripSnapshot返回code/message/meta以及data={state,latest_plan,latest_version}。state为当前有效状态：trip_id、state_version、city、theme、current_location、origin_name、available_hours、target_stops、max_walking_meters、avoid_commercial、travel_date、additional_requirements、interests、dislikes、visited_places、rejected_places、current_skill、radius_meters、consider_weather、meal_type、cuisine_preference、food_avoid、max_distance_meters、food_limit、created_at、updated_at、expires_at。时间为ISO8601；visited/rejected当前保存但不提供编辑入口。latest_plan与能力响应data相同。

## 调试与实现边界

启动后访问 http://127.0.0.1:8000/docs，先分别测试独立接口，再测试 /travel 路由和续接，GET 验证保存，重复提交旧版本验证409。结构化请求的完整字段定义也可在 /openapi.json 查看，静态副本见 [TRAVELOS_OPENAPI.json](TRAVELOS_OPENAPI.json)。

本次实现使用高德周边搜索、实际步行路线、现有 DeepSeek 英文转换与 OpenRouter Jev Choice。Wikimedia 实体匹配尚未接入；QWeather已接入，配置说明见 [QWEATHER.md](QWEATHER.md)；不会以生成模型编造知识摘要来替代来源。前端首页提供三个表单及统一对话，结果页支持修改、续接、切换能力与刷新恢复；旧 /result 页保留用于查看会话中旧版结果。鉴权、多用户和路线全局优化不在本地版范围内。

参考：https://lbs.amap.com/api/webservice/guide/api-advanced/search ，https://docs.typesafe.ai/primitives/choice 。

和风天气扩展字段（未提供的数据为null）：source_url为string或null；temperature_max_c/temperature_min_c为number或null；precipitation_probability_day/precipitation_probability_night为0..1的number或null。未配置或服务失败仍按WEATHER降级，具体原因写入notes。Food不查询天气。


## 路由注册与地图补充

目录结构、路由前缀归属及断点位置见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)。新规范地图：GET /api/v1/maps/static?points=经度,纬度;经度,纬度，支持2..6个中国境内位置，查询字符串最多250字符，返回图片和Cache-Control。参数无效按V1错误契约返回422，地图服务异常返回502；旧/api/walk/map兼容原detail错误格式。


## 数量修改

三个子系统均支持最多 5 个目标地点（城市漫步最少 2 个，景点和餐厅最少 1 个）。已有行程的“改成五个地点”“再加两个”“减少一个”等明确数量指令，由主 Agent 直接修改当前子系统对应的数量字段，其他偏好继承，不重复调用抽取和路由模型。包含新的活动目标或其他条件的请求仍走正常需求路由。

“增加到五个”表示目标总数为 5；“再加两个”以正在修改的版本实际返回的地点数为基础增加 2。超出数量范围返回参数错误，原行程和历史不变。修改成功后追加新版本，历史版本可以继续读取和编辑。

城市漫步从已选起点周边检索；各子 Agent 的选点模型同时接收目标数和剩余数。景点按数量和时长分配每站 30–60 分钟停留，仍以实际步行耗时检查总预算。候选不足、偏好不匹配或时间/距离不足时允许返回较少地点，响应 notes 和结果页明确显示目标数与实际数，不将其显示为已满足数量要求。

## 三个讲述者与修改评价

三个 V1 子系统以及 `/api/v1/travel` 的规划成功响应新增可选的 `data.narration`。城市漫步、景点、餐厅分别由 `CityWalkNarratorAgent`、`AttractionNarratorAgent`、`FoodNarratorAgent` 生成评价。餐厅讲述针对独立备选方案，不能将它们描述为串联游览路线。

```json
{
  "narrator": "FoodNarratorAgent",
  "evaluation": "这次缩减了备选餐厅数量，可以更集中地比较可达性与偏好。",
  "changes": ["目标地点数量：5 → 3", "实际地点数量：5 → 3", "移除地点：地点4、地点5"],
  "based_on_version": 1,
  "user_request": "改成三个地点",
  "source": "LLM"
}
```

首次规划的 `changes` 为空、`based_on_version` 为null。对话修改会保留本次原话，表单编辑的 `user_request` 为null，但两者都根据前后真实状态和地点生成修改说明；无实际变化会明确说明。`base_version` 指定历史版本时，讲述与该选中版本比较，写入仍使用最新状态版本校验。

`source` 为LLM或RULES。讲述模型失败时使用本子功能的基础评价，真实修改说明仍会保留；meta.degraded_services包含NARRATOR，fallback_applied为true，规划仍可正常保存。讲述不会修改地点和路线，数量不足会明确提示。

讲述随规划一起保存，历史恢复与刷新不重复调用模型。旧记录缺少narration时按null读取，无需数据库迁移。澄清响应与位置搜索不运行讲述者。
