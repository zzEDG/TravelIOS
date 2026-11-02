# 偏好提取调试流程

位置：`backend/app/services/preferences.py`，`PreferenceExtractor.extract`。

原来的 `prompt | model` chain 已移除，现在依次执行：

1. `input_data`：本轮话语、旧版本偏好、旧地点和锁定状态。
2. `messages = _PROMPT.format_messages(...)`：完整提示词。
3. `model_result = await self.model.ainvoke(messages)`：调用模型，返回原始响应和结构化解析结果。SDK 仍负责 JSON 解析，`include_raw=True` 保留响应及解析错误。
4. `raw_response`、`parsed_data`、`parsing_error`：分别查看模型原始响应、字段字典、解析异常。模型配置使用 JSON Schema，不在 SDK 内执行 PreferencePatch 的业务校验。
5. `patch = PreferencePatch.model_validate(parsed_data)`：显式校验、规范化。可以单步进入 `validate_routing_english`。
6. `return patch`：返回主 Agent，再执行路由、锁定名称匹配、规划。

建议在模型调用后的 `raw_response` 赋值行和 `model_validate` 行打断点。变量均为本次调用的局部变量，避免并发请求覆盖调试状态；不默认打印用户行程全文。

`PreferenceExtractionError.stage` 区分 `prompt`、`model`、`parse`、`validate`。原始异常在 `__cause__`；字段校验失败会列出字段名。上层 HTTP 错误处理仍会优先识别原始限流异常。

例如“改成4个地点吧，保留石藤咖啡和百新书局”：检查 `parsed_data.target_stops` 是否为 4、`lock_place_names` 是否包含两个原名，然后查看 `patch.routing_english` 中中文店名规范化后的结果。

重启正在运行的后端调试进程后生效。
