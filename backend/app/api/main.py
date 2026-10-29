"""旧启动路径兼容。应用实现统一在app/main.py。"""
from app.main import app, lifespan

if __name__ == "__main__":
    import uvicorn
    from app.config import get_settings
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
