"""行程控制器：历史列表、历次版本与行程恢复。"""
from typing import Literal
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, Path, Query
from app.controllers.errors import TravelRoute
from app.services.plan_result import snapshot_response
from app.models.travel import TripHistoryResponse, TripSnapshotResponse, TripDeleteResponse
from app.services.trip_service import TripService
from app.controllers.dependencies import get_trip_service

router = APIRouter(route_class=TravelRoute, tags=["统一入口"])

@router.get("/trips", response_model=TripHistoryResponse, summary="按子系统检索规划历史")
async def list_trips(
    skill: Literal["CITYWALK", "ATTRACTION", "FOOD"] | None = None,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=5, ge=1, le=50),
    trips: TripService = Depends(get_trip_service),
) -> TripHistoryResponse:
    return TripHistoryResponse(data=await run_in_threadpool(trips.list_trips, skill=skill, query=q, page=page, page_size=page_size))

@router.get("/trips/{trip_id}/history", response_model=TripHistoryResponse, summary="查看行程的历次版本")
async def trip_history(
    trip_id: str = Path(..., min_length=5, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=5, ge=1, le=50),
    trips: TripService = Depends(get_trip_service),
) -> TripHistoryResponse:
    return TripHistoryResponse(data=await run_in_threadpool(trips.trip_history, trip_id, page=page, page_size=page_size))

@router.get("/trips/{trip_id}", response_model=TripSnapshotResponse)
async def get_trip(
    trip_id: str = Path(..., min_length=5, max_length=64),
    version: int | None = Query(default=None, ge=1),
    trips: TripService = Depends(get_trip_service),
) -> TripSnapshotResponse:
    return snapshot_response(await run_in_threadpool(trips.get_trip, trip_id, version))


@router.delete("/trips/{trip_id}", response_model=TripDeleteResponse, summary="删除自己的规划及全部历史")
async def delete_trip(
    trip_id: str = Path(..., min_length=5, max_length=64),
    trips: TripService = Depends(get_trip_service),
) -> TripDeleteResponse:
    await run_in_threadpool(trips.delete_trip, trip_id)
    return TripDeleteResponse()
