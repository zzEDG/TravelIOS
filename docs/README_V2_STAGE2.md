> 历史阶段记录：文中 Food/Attraction 未实现的描述已由第三阶段替代。当前接口以 [三子系统接口规范](docs/TRAVELOS_API.md) 为准，开发与验证见 [第三阶段](README_V2_STAGE3.md)。

# TravelOS V2 第二阶段：统一入口、Jev 路由与续接

第二阶段新增 POST /api/v1/travel。生成模型从中文消息抽取偏好补丁，同时生成给 Jev 的英文意图摘要；Jev Choice 在 CITYWALK、ATTRACTION、FOOD、CLARIFY 中选择。代码只执行已实现的 CityWalk，其他两类能力将在第三阶段加入。原有 /api/v1/citywalk/plan、/api/walk/plan 保留。

## 请求和响应

新建漫步：

    {
      "message": "在上海看看老建筑，顺便喝咖啡，大约四小时，不想去商业街",
      "city": "上海",
      "location": {
        "latitude": 31.22,
        "longitude": 121.46,
        "coordinate_system": "GCJ-02"
      }
    }

message 必填（1–1000 字）；city 可由消息抽取，但新建可执行的漫步必须有起点 location。坐标是纬度、经度；设备给 WGS84 时应声明 coordinate_system，规划层会调用高德转换。时间和站点数沿用第一阶段的整数约束：2–8 小时、2–5 站。travel_date 可选，格式为 YYYY-MM-DD。

成功响应沿用 code: 0, message: success 外壳。data.routed_skill 为 CITYWALK 时，data 是第一阶段的完整路线，包括真实地点、起点到第一站的步行路段、天气和封面。meta.route_confidence 是 Jev Choice 分布的集中程度（0–1），不是结果正确率，当前仅供观察，不设置未经样例校准的硬阈值。

意图不明确或缺少可执行的城市、起点、主题时，HTTP 200 返回：

    {
      "code": 0,
      "message": "success",
      "data": {
        "routed_skill": "CLARIFY",
        "question": "请提供起点坐标 location（latitude、longitude），以便计算真实步行路线。",
        "trip_id": null,
        "state_version": null
      },
      "meta": {
        "schema_version": "1.0",
        "decision_source": "JEV",
        "fallback_applied": false,
        "degraded_services": [],
        "route_confidence": 0.82
      }
    }

澄清不会写入新行程；续接时会返回当前 trip_id 和 state_version，已保存状态保持不变。识别为 FOOD 或 ATTRACTION 时返回 HTTP 501、业务码 40007，明确告知能力尚未接入，不伪造规划结果，也不修改行程。偏好抽取结果格式无效时返回 HTTP 502、业务码 50005；Jev 调用失败仍使用 50001。

## 带状态续接

将上次规划返回的 trip_id 和 state_version 一起传入：

    {
      "message": "换成两小时，避开商业街，安静一点",
      "trip_id": "trip_替换为实际值",
      "state_version": 1
    }

系统先从 SQLite 读取旧状态并检查版本，再进行偏好抽取与路由。未提及的城市、主题、起点和时长保留旧值；模型输出的偏好列表是更新后的完整列表。空列表只有在模型明确标记清空时才会删除旧偏好，避免把“未提及”误判成“清空”。执行成功后版本加一；过期、不存在、旧版本冲突分别沿用第一阶段的 410、404、409。生成失败和澄清都不提交新状态。GET /api/v1/trips/{trip_id} 可查看已保存的状态及最近一次成功路线。

## 断点顺序

1. backend/app/api/routes/travel.py 的 plan_travel()：接收并校验 HTTP 请求。
2. backend/app/application/orchestrator.py 的 TravelOrchestrator.plan()：读取旧状态、检查版本、决定澄清或执行。
3. backend/app/application/preferences.py 的 PreferenceExtractor.extract()：调用生成模型并验证结构化补丁及英文摘要。
4. backend/app/application/routing.py 的 TravelRouter.route()：通过 OpenRouter Decisions API 调用 Jev Choice；核对返回选项与 confidence。
5. backend/app/application/planner.py 的 TravelPlanner.plan_citywalk()：合并状态、调用现有 CityWalk Skill、用乐观锁保存。
6. backend/app/storage/trip_store.py 的 TripStore.replace()：检查版本并写入 SQLite。

在 VS Code 的 WSL 窗口选择“调试后端 API（WSL）”并按 F5，打开 http://127.0.0.1:8000/docs 发起请求。请勿使用热重载调试断点。

## 当前边界和验证

前端仍使用旧 CityWalk 接口；顶层新接口先通过 API 文档调试。Attraction、Food、QWeather、Wikimedia 和前端迁移属于后续阶段。route_confidence 不触发规则兜底，Jev 故障会明确报错；列表型偏好由生成模型抽取，建议在调试时查看原消息和实际补丁。API Key 只从后端进程环境或本地 .env 读取，不写入行程库。

离线验证：18 项测试通过，覆盖新建、续接、旧版本冲突、澄清、未实现能力、模型输出校验及旧接口回归。真实 DeepSeek + OpenRouter 测试：上海老建筑与咖啡请求抽出 4 小时、建筑与咖啡、避开商业区，Jev 路由为 CITYWALK；“换成两小时，别去商业街”可基于旧状态继续路由为 CITYWALK。

真实 HTTP 链路验证：使用实际 DeepSeek 偏好抽取和 OpenRouter Jev 路由、模拟地点 Skill，通过 POST /api/v1/travel 新建及续接均返回 HTTP 200；第二次保存的 state_version 为 2，读取行程的 available_hours 为 2。完整高德地点、天气和 Unsplash 路线生成仍需在用户环境中按第一阶段方式调试。
