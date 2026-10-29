# 异步执行与并发边界

后端继续使用 FastAPI 和规划完成后返回 JSON 的方式。自由偏好版本为城市漫步增加 visit_order、end_with 字段，旧历史通过默认值兼容，数据库表结构保持一致。

## 一次请求的调用链

```text
async Controller
  → await MainAgent.plan / plan_for
  → await TripService.prepare（需要时异步转换坐标）
  → await 子 Agent.execute
  → await 对应讲述者.evaluate
  → await run_in_threadpool(TripService.save, ...)
  → 返回原有 JSON 响应
```

偏好抽取、英文转换和讲述使用 LangChain 的 `ainvoke`；Jev、高德、和风、Unsplash 使用 `httpx.AsyncClient`。Jev 遇到可重试状态码时使用 `await asyncio.sleep`，退避期间不占用事件循环。

国内服务的环境代理在客户端上显式关闭：高德、和风天气及官方 DeepSeek 使用 `trust_env=False`。覆盖服务独立创建的默认客户端与 `ServiceResources` 注入的请求内客户端；DeepSeek 同步、异步 SDK 客户端都设置，使用 `LLM_BASE_URL=https://api.deepseek.com/...` 的兼容调用方式也生效。`ServiceResources` 在请求结束时关闭其创建的同步和异步连接。其他模型服务、OpenRouter 和 Unsplash 保留原有环境代理行为，不修改进程的代理环境变量。显式传给工厂或服务的自定义客户端继续由调用方配置。

`TripService` 的读写方法和 `TripStore` 保持同步，在 Controller 或主 Agent 的调用边界使用 `run_in_threadpool`。每次数据库操作仍创建并关闭自己的 SQLite 连接，保留原来的事务和乐观版本检查，不把网络请求放进数据库事务。

## 哪些操作并发

| 功能 | 并发执行的操作 | 必须顺序执行的操作 |
|---|---|---|
| 城市漫步 | 自由偏好关键词搜索；同轮当前位置到候选的路线；规划后的天气与封面 | 解析检索偏好；逐站选择并更新位置和预算；不再做选点后的距离重排 |
| 景点游览 | 最多三个关键词搜索；需求翻译与 POI 翻译；同一轮当前位置到各候选的路线 | 先选出一站，再更新当前位置与预算，才能进入下一轮 |
| 餐厅推荐 | 最多三个关键词搜索；起点到各候选的路线；需求翻译与 POI 翻译 | 逐个选择独立备选，移除已选 ID，再选择下一个 |
| 历史与起点 | 请求可以在等待数据库或高德时让出执行权 | POI 搜索确实无结果后，才进行地址解析 |

独立查询合并结果使用输入顺序，去重、候选与路线的对应关系不会因响应快慢改变。城市漫步的新偏好、顺序和预算规则见 [自由偏好与逐站规划](FREE_PREFERENCE_WALK.md)；景点与餐厅仍使用原来的规则。

景点仍可能发出较多路线查询。例如八个候选连续选成五站，仍需查询 8+7+6+5+4 次；改造是让同一轮的独立查询并发，并未减少查询次数或引入路线缓存。

## 并发上限与任务清理

- 三个子系统共用 ReAct 候选检索：模型工具调用使用 `ainvoke`，高德工具使用异步接口。请求内最多两类并行，每类最多两次搜索、六轮模型调用，包含排队最多90秒；无效候选不进入后续翻译、路线查询与 Jev 选点。详见 [ReAct候选检索](REACT_CANDIDATE_SEARCH.md)。
- `AmapService` 每个实例最多同时进行四次普通高德查询。由于资源按请求创建，这是每请求限制，不是全进程的配额管理。
- 请求资源创建的 AsyncClient 每个连接池最多八个连接，保留各服务原有超时。不能据此推断所有请求的总耗时上限。
- `concurrency.gather_results` 按输入顺序返回结果。一个子任务失败或整个操作被取消时，先取消并等待同组任务退出，再把原异常继续向上传递。
- `ServiceResources` 使用 `AsyncExitStack`，登记 `client.aclose`；FastAPI 的异步 yield 依赖退出后关闭已创建客户端。
- `CancelledError` 不会被可选服务的 `except Exception` 当成普通降级吞掉。取消发生在保存前时不会保存未完成方案；进入数据库提交阶段后，仍以事务的实际提交结果为准。

这里处理的是应用任务收到取消后的清理。浏览器关闭连接是否触发正在运行任务的取消，取决于 ASGI 服务器的行为，当前未额外实现断开连接监控。

## await 不代表单次调用立即变快

`await` 让等待网络时的执行权交还给事件循环，其他请求能继续处理；并发可以减少互不依赖步骤的串行等待。单次模型推理、供应商排队与接口网络延迟仍然存在。

此版本仍是完整规划后返回 JSON，没有新增流式输出或后台任务轮询。因此用户仍需等待完整规划和讲述，但独立 API 查询可以同时进行。未进行真实第三方服务测速，不填写固定的提速百分比。

## 阅读和调用示例

Controller：

```python
@router.post('/plan', response_model=CityWalkPlanResponse)
async def plan_citywalk(request: CityWalkPlanRequest,
                        agent: MainAgent = Depends(get_main_agent)):
    return await agent.plan_for('CITYWALK', request)
```

同一轮候选路线：

```python
routes = await gather_results(*(self.route(origin, p) for p in candidates))
by_id = {p.poi_id: route for p, route in zip(candidates, routes)}
```

模型调用：

```python
result = await self.resources.llm.with_structured_output(NarrationDraft).ainvoke(messages)
```

同步 SQLite 保存：

```python
record = await run_in_threadpool(self.trips.save, state, plan, previous)
```

FastAPI 路由中直接 await，不要调用 `asyncio.run` 再创建一个事件循环。独立脚本可在最外层使用 `asyncio.run`，资源使用 `async with ServiceResources()`；完整示例见 [项目结构](PROJECT_STRUCTURE.md)。

## 验证

```bash
cd /Projects/fullstack-learning-agent/backend
python -m unittest discover -s tests -q
```

原有规划、数量修改、版本历史、讲述和错误契约用例迁移为异步替身。新增事件屏障验证独立查询重叠、并发上限、等待模型时的健康响应、取消后的资源关闭，以及并发编辑只有一个新版本成功保存。验证使用离线数据与 MockTransport，不调用真实付费模型和外部接口。
