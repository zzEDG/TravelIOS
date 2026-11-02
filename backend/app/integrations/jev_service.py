"""通过 OpenRouter Decisions API 调用 Jev 的 score / choice 决策。"""

import asyncio
import logging
import math
import os
from typing import Any

import httpx

JEV_API_URL = "https://openrouter.ai/api/alpha/decisions"
MAX_ATTEMPTS = 3
logger = logging.getLogger(__name__)
diagnostics_logger = logging.getLogger("app.diagnostics")


class JevServiceError(ValueError):
    pass


def _validate_answers(data: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise JevServiceError("Jev 返回的决策缺少答案")
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, dict) or answer.get("type") != question.get("type"):
            raise JevServiceError(f"Jev 返回的决策格式无效（{name}）")
        if question.get("type") == "choice":
            criteria = question.get("criteria")
            choice = answer.get("choice")
            if not isinstance(choice, str) or not isinstance(criteria, dict) or choice not in criteria:
                raise JevServiceError(f"Jev 返回了候选范围外的选择（{name}）")
            if name == "travel_skill":
                confidence = answer.get("confidence")
                if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                        or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                    raise JevServiceError("Jev 返回的能力路由置信度无效")
        elif question.get("type") == "score":
            score = answer.get("score")
            criteria = question.get("criteria")
            if (not isinstance(score, (int, float)) or isinstance(score, bool)
                    or not math.isfinite(score) or not isinstance(criteria, list)
                    or not 0 <= score < len(criteria)):
                raise JevServiceError(f"Jev 返回的地点评分无效（{name}）")
    return answers


class JevService:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=15.0)

    async def decide(self, state: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise JevServiceError("请设置 OPENROUTER_API_KEY；可在 backend/.env 中填写")
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self.client.post(
                    JEV_API_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": os.getenv("JEV_MODEL_ID") or "typesafe/jev-1.13",
                          "state": state, "questions": questions},
                )
                status = response.status_code
                if status == 401:
                    raise JevServiceError("Jev API Key 无效")
                if status == 403:
                    try:
                        error = response.json().get("error", {})
                        metadata = error.get("metadata", {}) if isinstance(error, dict) else {}
                        region_blocked = isinstance(metadata, dict) and metadata.get("failed_routing_step") == "Gate Endpoints with Geo Restrictions"
                    except (ValueError, AttributeError):
                        region_blocked = False
                    if region_blocked:
                        diagnostics_logger.warning("Jev 访问被拒绝 service=jev http_status=403 reason=geo_restriction")
                        raise JevServiceError("Jev 模型在当前请求地区不可用（HTTP 403）")
                    raise JevServiceError("Jev 无访问权限（HTTP 403）")
                if status == 402:
                    raise JevServiceError("OpenRouter 余额不足，请检查账户额度")
                if status == 404:
                    raise JevServiceError("OpenRouter 找不到 Jev 模型，请检查 JEV_MODEL_ID")
                if status in (400, 422):
                    raise JevServiceError("Jev 请求格式无效，请检查模型和问题配置")
                if status in (408, 429) or 500 <= status <= 599:
                    raise _RetryableJevError(f"Jev 服务暂时繁忙或已限流（HTTP {status}）")
                response.raise_for_status()
                try:
                    data = response.json()
                    return _validate_answers(data, questions)
                except (ValueError, TypeError) as exc:
                    raise _RetryableJevError(str(exc) or "Jev 返回的内容无法解析") from exc
            except (httpx.TimeoutException, httpx.NetworkError, _RetryableJevError) as exc:
                diagnostics_logger.warning("Jev 决策失败 service=jev error_type=%s attempt=%d", type(exc).__name__, attempt + 1)
                if attempt == MAX_ATTEMPTS - 1:
                    raise JevServiceError(f"Jev 决策失败，已尝试 {MAX_ATTEMPTS} 次：{exc}") from exc
                logger.warning("Jev 决策第 %d 次失败（%s），准备重试", attempt + 1, type(exc).__name__)
                await asyncio.sleep(0.4 * (2 ** attempt))
        raise AssertionError("unreachable")


class _RetryableJevError(Exception):
    pass
