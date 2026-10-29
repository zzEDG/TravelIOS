"""FastAPI主应用"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings, validate_config, print_config
from app.controllers import citywalk, attraction, food, travel, trips, locations, maps
from app.controllers.errors import TravelAPIError, travel_error_handler, validation_error_handler

# 获取配置
settings = get_settings()

app_logger = logging.getLogger("app")
app_logger.setLevel(settings.log_level.upper())
if not app_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    app_logger.addHandler(handler)
app_logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):

    """应用启动事件"""
    print("\n" + "="*60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("="*60)
    
    # 打印配置信息
    print_config()
    
    # 验证配置
    try:
        validate_config()
        print("\n✅ 配置验证通过")
    except ValueError as e:
        print(f"\n❌ 配置验证失败:\n{e}")
        print("\n请检查当前进程可见的系统环境变量，或本地开发用的 .env 文件")
        raise
    
    print("\n" + "="*60)
    print("📚 API文档: http://localhost:8000/docs")
    print("📖 ReDoc文档: http://localhost:8000/redoc")
    print("="*60 + "\n")

    yield  # 允许应用继续运行

    """应用关闭事件"""
    print("\n" + "="*60)
    print("👋 应用正在关闭...")
    print("="*60 + "\n")


# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="TravelOS：城市漫步、景点游览、餐厅推荐；高德地点与路线、和风天气、Jev辅助决策",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
# Controller 层：三个子系统、统一对话、历史、位置与地图。
app.include_router(citywalk.router, prefix="/api/v1")
app.include_router(attraction.router, prefix="/api/v1")
app.include_router(food.router, prefix="/api/v1")
app.include_router(travel.router, prefix="/api/v1")
app.include_router(trips.router, prefix="/api/v1")
app.include_router(locations.router, prefix="/api/v1")
app.include_router(maps.router, prefix="/api/v1")
# 兼容旧URL，实现仍在citywalk.py中。
app.include_router(citywalk.legacy_router, prefix="/api")
app.add_exception_handler(TravelAPIError, travel_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/api/v1/models")
async def models():
    from app.integrations.llm_service import available_models
    return {"data": available_models()}


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )
