"""LangChain 聊天模型配置。"""

import os
from urllib.parse import urlsplit

import httpx

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI


def available_models():
    return [
        {"value": "default", "label": "环境默认模型", "available": True},
        {"value": "deepseek", "label": "DeepSeek", "available": bool(os.getenv("DEEPSEEK_API_KEY"))},
        {"value": "moonshot", "label": "Kimi（月之暗面）", "available": bool(os.getenv("MOONSHOT_API_KEY") or os.getenv("KIMI_API_KEY"))},
    ]


def validate_provider(provider):
    option = next((m for m in available_models() if m["value"] == provider), None)
    if not option or not option["available"]:
        raise ValueError("所选模型不可用，请选择已配置的模型")


def llm_trust_env(provider="default") -> bool:
    if provider in ("deepseek", "moonshot"):
        return False

    """官方 DeepSeek 直连；其他模型服务保留原有环境代理配置。"""
    if os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"):
        host = urlsplit(os.getenv("LLM_BASE_URL") or "").hostname or ""
        return not (host == "deepseek.com" or host.endswith(".deepseek.com"))
    return not bool(os.getenv("DEEPSEEK_API_KEY"))


def get_llm(*, provider="default", http_async_client=None, http_client=None) -> BaseChatModel:
    """从当前进程环境创建模型；优先使用显式配置，其次使用 DeepSeek Key。"""
    client_options = {"http_async_client": http_async_client} if http_async_client is not None else {}
    if http_client is not None:
        client_options["http_client"] = http_client
    explicit_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    model = os.getenv("LLM_MODEL_ID")
    # 独立调用工厂时也显式关闭 DeepSeek 环境代理；注入客户端由调用方配置和管理。
    validate_provider(provider)
    if not llm_trust_env(provider):
        if http_client is None:
            client_options["http_client"] = httpx.Client(timeout=35, trust_env=False)
        if http_async_client is None:
            client_options["http_async_client"] = httpx.AsyncClient(timeout=35, trust_env=False)
    if provider == "moonshot" or (provider == "default" and not explicit_key and not deepseek_key):
        key = os.getenv("MOONSHOT_API_KEY") or os.getenv("KIMI_API_KEY")
        if key:
            return ChatOpenAI(
                api_key=key,
                base_url=os.getenv("MOONSHOT_BASE_URL") or "https://api.moonshot.cn/v1",
                model=os.getenv("MOONSHOT_MODEL_ID") or "kimi-k2.6",
                temperature=0.6,
                extra_body={"thinking": {"type": "disabled"}},
                timeout=35, max_retries=1, **client_options,
            )
    if provider == "deepseek":
        explicit_key = None
        model = "deepseek-chat"
    if explicit_key:
        base_url = os.getenv("LLM_BASE_URL") or None
        return ChatOpenAI(
            api_key=explicit_key,
            base_url=base_url,
            model=model or ("deepseek-chat" if base_url and "deepseek" in base_url else "gpt-4o-mini"),
            timeout=35,
            max_retries=1,
            **client_options,
        )
    if deepseek_key:
        return ChatDeepSeek(
            api_key=deepseek_key,
            model=model or "deepseek-chat",
            timeout=35,
            max_retries=1,
            **client_options,
        )
    raise ValueError("请设置 DEEPSEEK_API_KEY、LLM_API_KEY 或 OPENAI_API_KEY")
