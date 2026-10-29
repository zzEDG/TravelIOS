"""配置管理模块"""

import os
import subprocess
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# 系统环境变量优先；.env 仅作为本地开发时的可选补充。

env_path = Path(__file__).parent.parent / ".env"

load_dotenv(env_path)

# VS Code 直接启动 Python 时通常不会读取 ~/.bashrc。仅在 WSL 且缺少密钥时，
# 从交互式 Bash 补齐应用需要的变量；已有环境变量和 .env 优先。
_SHELL_ENV_NAMES = (
    "AMAP_API_KEY",
    "UNSPLASH_ACCESS_KEY",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "MOONSHOT_API_KEY",
    "MOONSHOT_BASE_URL",
    "MOONSHOT_MODEL_ID",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL_ID",
    "JEV_MODEL_ID",
    "QWEATHER_API_HOST",
    "QWEATHER_API_KEY",
    "QWEATHER_JWT",
)


def load_wsl_shell_environment() -> None:
    if os.name != "posix":
        return
    try:
        if "microsoft" not in Path("/proc/version").read_text().lower():
            return
    except OSError:
        return
    if (os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY")) and all(os.environ.get(name) for name in ("AMAP_API_KEY", "UNSPLASH_ACCESS_KEY", "OPENROUTER_API_KEY", "QWEATHER_API_HOST")) and (os.getenv("QWEATHER_API_KEY") or os.getenv("QWEATHER_JWT")) and any(
        os.environ.get(name) for name in ("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    ):
        return

    try:
        result = subprocess.run(
            ["bash", "-ic", "env -0"],
            capture_output=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return

    for entry in result.stdout.split(b"\0"):
        name_bytes, separator, value_bytes = entry.partition(b"=")
        if not separator:
            continue
        name = os.fsdecode(name_bytes)
        if name in _SHELL_ENV_NAMES and not os.environ.get(name):
            os.environ[name] = os.fsdecode(value_bytes)


load_wsl_shell_environment()

class Settings(BaseSettings):
    """应用配置"""

    # 应用基本配置
    app_name: str = "LangChain城市主题漫游Agent"
    app_version: str = "1.0.0"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS配置 - 使用字符串,在代码中分割
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # 密钥在使用时直接从当前进程环境读取，不要求存在 .env 文件。
    @property
    def amap_api_key(self) -> str:
        return os.getenv("AMAP_API_KEY", "")

    @property
    def unsplash_access_key(self) -> str:
        return os.getenv("UNSPLASH_ACCESS_KEY", "")

    @property
    def qweather_api_host(self) -> str:
        return os.getenv("QWEATHER_API_HOST", "")

    @property
    def qweather_api_key(self) -> str:
        return os.getenv("QWEATHER_API_KEY", "")

    @property
    def qweather_jwt(self) -> str:
        return os.getenv("QWEATHER_JWT", "")

    # 日志配置
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 忽略额外的环境变量

    def get_cors_origins_list(self) -> List[str]:
        """获取CORS origins列表"""
        return [origin.strip() for origin in self.cors_origins.split(',')]


# 创建全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings


# 验证必要的配置
def validate_config():
    """验证配置是否完整"""
    errors = []
    if not settings.amap_api_key:
        errors.append("AMAP_API_KEY未配置")
    if not settings.unsplash_access_key:
        errors.append("UNSPLASH_ACCESS_KEY未配置")

    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("MOONSHOT_API_KEY") or os.getenv("KIMI_API_KEY")
    if not llm_api_key:
        errors.append("LLM_API_KEY、OPENAI_API_KEY 或 DEEPSEEK_API_KEY 未配置（英文翻译及中文路线文案）")
    if not os.getenv("OPENROUTER_API_KEY"):
        errors.append("OPENROUTER_API_KEY 未配置（搜索词与地点选择）")

    if errors:
        error_msg = "配置错误:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

    return True


# 打印配置信息(用于调试)
def print_config():
    """打印当前配置(隐藏敏感信息)"""
    print(f"应用名称: {settings.app_name}")
    print(f"版本: {settings.app_version}")
    print(f"服务器: {settings.host}:{settings.port}")
    print(f"高德 Web 服务 Key: {'已配置' if settings.amap_api_key else '未配置'}")
    print(f"Unsplash Access Key: {'已配置' if settings.unsplash_access_key else '未配置'}")
    # 检查LLM配置
    standard_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    using_deepseek_alias = bool(deepseek_key and not standard_key)
    llm_api_key = standard_key or deepseek_key
    llm_base_url = os.getenv("LLM_BASE_URL") or ("https://api.deepseek.com" if using_deepseek_alias else "默认服务地址")
    llm_model = os.getenv("LLM_MODEL_ID") or ("deepseek-chat" if using_deepseek_alias else "gpt-4o-mini")

    print(f"LLM API Key: {'已配置' if llm_api_key else '未配置'}")
    print(f"LLM Base URL: {llm_base_url}")
    print(f"LLM Model: {llm_model}")
    print(f"OpenRouter API Key: {'已配置' if os.getenv('OPENROUTER_API_KEY') else '未配置'}")
    print(f"Jev Model: {os.getenv('JEV_MODEL_ID') or 'typesafe/jev-1.13'}")
    print(f"天气服务: 和风天气（{'已配置' if settings.qweather_api_host and (settings.qweather_api_key or settings.qweather_jwt) else '未配置，天气会降级'}）")
    print(f"日志级别: {settings.log_level}")
