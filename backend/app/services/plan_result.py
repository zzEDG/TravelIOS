"""规划结果组装：数量提示、服务降级信息和响应数据，不负责规划或存储。"""

from app.models.travel import (
    PlanData, ResponseMeta, TravelResponse, TripSnapshot, TripSnapshotResponse, TripState,
)
from app.models.trip_store import TripRecord


def response_meta(record: TripRecord) -> ResponseMeta:
    plan = record.latest_plan
    degraded = list(plan.warning_codes)
    if plan.weather is None and (plan.routed_skill == "CITYWALK" or
                                 (plan.routed_skill == "ATTRACTION" and record.state.consider_weather)):
        degraded.append("WEATHER")
    if plan.cover is None and plan.routed_skill == "CITYWALK":
        degraded.append("UNSPLASH")
    if any(route.distance_meters is None or route.polyline is None for route in plan.routes):
        degraded.append("AMAP_ROUTE")
    narration_fallback = plan.narration is not None and plan.narration.source == "RULES"
    if narration_fallback:
        degraded.append("NARRATOR")
    return ResponseMeta(degraded_services=degraded, fallback_applied=narration_fallback)


def add_count_notice(state: TripState, plan: PlanData) -> None:
    requested = state.food_limit if state.current_skill == "FOOD" else state.target_stops
    if len(plan.places) < requested:
        plan.notes.insert(0, f"本次目标为 {requested} 个地点，实际返回 {len(plan.places)} 个；"
                          "请查看后续提示了解原因。")


def plan_response(record: TripRecord) -> TravelResponse:
    return TravelResponse(data=record.latest_plan, meta=response_meta(record))


def snapshot_response(snapshot: TripSnapshot) -> TripSnapshotResponse:
    record = TripRecord(state=snapshot.state, latest_plan=snapshot.latest_plan)
    return TripSnapshotResponse(data=snapshot, meta=response_meta(record))
