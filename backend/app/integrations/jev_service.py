"""通过 OpenRouter Decisions API 调用 Jev 的 score / choice 决策。"""

import os
import asyncio
from typing import Any

import httpx


JEV_API_URL = "https://openrouter.ai/api/alpha/decisions"


class JevServiceError(ValueError):
    pass


class JevService:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=15.0)

    async def decide(self, state: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise JevServiceError("请设置 OPENROUTER_API_KEY；可在 backend/.env 中填写")
        for attempt in range(3):
            response = await self.client.post(
                JEV_API_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": os.getenv("JEV_MODEL_ID") or "typesafe/jev-1.13",
                      "state": state, "questions": questions},
            )
            if response.status_code in (429, 502, 503, 529) and attempt < 2:
                await asyncio.sleep(0.4 * (2 ** attempt))
                continue
            break
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise JevServiceError("Jev API Key 无效或无访问权限") from exc
            if status == 402:
                raise JevServiceError("OpenRouter 余额不足，请检查账户额度") from exc
            if status == 404:
                raise JevServiceError("OpenRouter 找不到 Jev 模型，请检查 JEV_MODEL_ID") from exc
            if status in (400, 422):
                raise JevServiceError("Jev 请求格式无效，请检查模型和问题配置") from exc
            if status in (429, 502, 503, 529):
                raise JevServiceError("OpenRouter Jev 服务暂时繁忙或已限流，请稍后重试") from exc
            raise
        data = response.json()
        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict) or not all(name in answers for name in questions):
            raise JevServiceError("Jev 返回的决策缺少答案")
        return answers
