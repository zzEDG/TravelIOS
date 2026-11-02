"""共用行程服务：状态合并、坐标归一化、版本校验和历史读写。"""

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.integrations.amap_service import AmapService
from app.models.travel import (
    GeoLocation, PlanData, TravelRequest, TripHistoryPage, TripSnapshot, TripState,
)
from app.models.trip_store import TripRecord, TripVersionConflictError
from app.models.postgres_trip_store import PostgresTripStore as TripStore

if TYPE_CHECKING:
    from app.agents.base import PlanningRequest, SubAgent

TRIP_TTL = timedelta(days=30)


class TripService:
    def __init__(self, store: TripStore | None = None, amap: AmapService | None = None):
        self.store = store if store is not None else TripStore()
        self.amap = amap if amap is not None else AmapService(get_settings().amap_api_key)

    def previous(self, request: "PlanningRequest | TravelRequest") -> TripRecord | None:
        if not request.trip_id:
            return None
        latest = self.store.get(request.trip_id, allow_expired=request.base_version is not None)
        if latest.state.state_version != request.state_version:
            raise TripVersionConflictError("行程版本已变化，请重新载入规划后再提交")
        if request.base_version is None:
            return latest
        base = self.store.get_version(request.trip_id, request.base_version)
        # 用选中历史版本的偏好重新规划，写入时仍与当前最新版本比较。
        return TripRecord(
            state=base.state.model_copy(update={"state_version": latest.state.state_version}),
            latest_plan=base.latest_plan,
        )

    async def _location(self, location: GeoLocation) -> GeoLocation:
        if location.coordinate_system == "GCJ-02":
            return location
        longitude, latitude = await self.amap.convert_gps_to_amap(location.longitude, location.latitude)
        return GeoLocation(latitude=latitude, longitude=longitude, coordinate_system="GCJ-02")

    async def prepare(
        self, request: "PlanningRequest", agent_type: "type[SubAgent]",
        previous: TripRecord | None, *, allow_switch: bool = False,
    ) -> TripState:
        old = previous.state if previous else None
        if old and old.current_skill != agent_type.name and not allow_switch:
            raise ValueError("请使用同一能力接口续接，或通过 /travel 切换能力")
        city = request.city or (old.city if old else None)
        location = getattr(request, agent_type.location_field) or (old.current_location if old else None)
        if not city or location is None:
            raise ValueError("缺少城市或起点坐标")
        now = datetime.now(timezone.utc)

        payload = old.model_dump() if old else {
            "trip_id": "trip_" + uuid4().hex[:12], "created_at": now,
        }
        payload.update(agent_type.state_defaults(request, old))
        if old and old.current_skill != agent_type.name:
            payload["locked_place_ids"] = []
        excluded = {"trip_id", "state_version", "base_version", "city", agent_type.location_field}
        for field in request.model_fields_set - excluded:
            value = getattr(request, field)
            if value is not None or field in ("travel_date", "max_walking_meters", "end_with"):
                payload[agent_type.field_mapping.get(field, field)] = value
        payload.update(
            city=city, current_location=await self._location(location), current_skill=agent_type.name,
            state_version=old.state_version + 1 if old else 1,
            updated_at=now, expires_at=now + TRIP_TTL,
        )
        state = TripState.model_validate(payload)
        if state.locked_place_ids:
            if not previous or previous.latest_plan.routed_skill != agent_type.name:
                raise ValueError("只能锁定所选计划版本中的地点")
            if state.city != previous.state.city:
                raise ValueError("更换城市时请先解除旧计划的地点锁定")
            by_id = {place.poi.id: place for place in previous.latest_plan.places}
            if len(set(state.locked_place_ids)) != len(state.locked_place_ids):
                raise ValueError("锁定地点不能重复")
            if any(poi_id not in by_id for poi_id in state.locked_place_ids):
                raise ValueError("锁定地点不属于所选规划版本，请重新选择")
            target_count = state.food_limit if agent_type.name == "FOOD" else state.target_stops
            if len(state.locked_place_ids) > target_count:
                raise ValueError("锁定地点数量超过目标数量，请增加数量或解除部分锁定")
            state._locked_places = [by_id[poi_id].model_copy(deep=True) for poi_id in state.locked_place_ids]
            if agent_type.name == "CITYWALK":
                missing = {place.poi.id for place in state._locked_places if not place.search_preferences()}
                if missing:
                    sources = await run_in_threadpool(
                        self.store.walk_place_preferences, state.trip_id,
                        previous.latest_plan.state_version, missing,
                    )
                    for place in state._locked_places:
                        if place.poi.id in sources:
                            place.matched_preferences = sources[place.poi.id]
        state.search_limit = min(1000, 2 * (state.food_limit if agent_type.name == "FOOD" else state.target_stops))
        if old and agent_type.name == 'CITYWALK':
            fields = request.model_fields_set
            # 从兴趣中移出，不代表删除仍被顺序或末站明确要求的类别。
            active_preferences = set(state.interests) | set(state.visit_order)
            if state.end_with:
                active_preferences.add(state.end_with)
            state._walk_edits = {
                'clear_visit_order': bool(old.visit_order) and 'visit_order' in fields and request.visit_order == [],
                'clear_end_with': old.end_with is not None and 'end_with' in fields and request.end_with is None,
                'removed_interests': [value for value in old.interests if value not in active_preferences]
                    if 'interests' in fields and request.interests is not None else [],
            }
        return state

    def save(
        self, state: TripState, plan: PlanData, previous: TripRecord | None,
        *, allow_expired: bool = False,
    ) -> TripRecord:
        if (plan.trip_id != state.trip_id or plan.state_version != state.state_version
                or plan.routed_skill != state.current_skill):
            raise ValueError("子 Agent 返回的行程标识或能力类型不一致")
        if not set(state.locked_place_ids).issubset(
                {place.poi.id for place in plan.places}):
            raise ValueError("规划结果未包含所有锁定地点，未保存本次调整")
        record = TripRecord(state=state, latest_plan=plan)
        if previous:
            self.store.replace(record, expected_version=previous.state.state_version, allow_expired=allow_expired)
        else:
            self.store.create(record)
        return record

    def list_trips(self, *, skill: str | None = None, query: str = "",
                   page: int = 1, page_size: int = 5) -> TripHistoryPage:
        return self.store.history(skill=skill, query=query, page=page, page_size=page_size)

    def trip_history(self, trip_id: str, *, page: int = 1, page_size: int = 5) -> TripHistoryPage:
        self.store.get(trip_id, allow_expired=True)
        return self.store.history(trip_id=trip_id, page=page, page_size=page_size)

    def get_trip(self, trip_id: str, version: int | None = None) -> TripSnapshot:
        latest = self.store.get(trip_id, allow_expired=version is not None)
        record = self.store.get_version(trip_id, version) if version is not None else latest
        return TripSnapshot(state=record.state, latest_plan=record.latest_plan,
                            latest_version=latest.state.state_version)

    def delete_trip(self, trip_id: str) -> None:
        self.store.delete(trip_id)
