"""HTTP 依赖组装；FastAPI 在请求结束时释放该请求创建的资源。"""

from collections.abc import AsyncIterator

from fastapi import Depends, Header
from app.integrations.llm_service import validate_provider
from starlette.concurrency import run_in_threadpool

from app.agents.main_agent import MainAgent
from app.dependencies import ServiceResources
from app.integrations.amap_service import AmapService
from app.services.trip_service import TripService
from app.models.postgres_trip_store import PostgresTripStore
from app.controllers.auth_dependencies import require_user


async def get_resources(x_ai_model: str = Header(default="default")) -> AsyncIterator[ServiceResources]:
    validate_provider(x_ai_model)
    async with ServiceResources(provider=x_ai_model) as resources:
        yield resources


async def get_main_agent(user=Depends(require_user), resources: ServiceResources = Depends(get_resources)) -> MainAgent:
    store = await run_in_threadpool(PostgresTripStore, user_id=user["id"])
    trips = TripService(store=store, amap=resources.amap)
    return MainAgent(trips=trips, resources=resources)


async def get_trip_service(user=Depends(require_user), resources: ServiceResources = Depends(get_resources)) -> TripService:
    store = await run_in_threadpool(PostgresTripStore, user_id=user["id"])
    return TripService(store=store, amap=resources.amap)


async def get_amap(user=Depends(require_user), resources: ServiceResources = Depends(get_resources)) -> AmapService:
    return resources.amap
