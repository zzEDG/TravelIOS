"""Attraction：将全部候选交给 Jev 评分，返回景点及距起点的直线距离。"""

from app.models.travel import AttractionPlanData, AttractionPlanRequest, TripState
from app.agents.discovery import DiscoveryAgent
from app.concurrency import gather_results


class AttractionAgent(DiscoveryAgent):
    name = "ATTRACTION"
    request_model = AttractionPlanRequest
    location_field = "location"
    default_theme = "景点游览"

    @classmethod
    def state_defaults(cls, request, previous):
        defaults = super().state_defaults(request, previous)
        if previous is None or previous.current_skill != cls.name:
            defaults["target_stops"] = 2
        return defaults

    @staticmethod
    def selection_preferences(state):
        return {"interests": state.interests, "dislikes": state.dislikes, "search_limit": state.search_limit}

    def __init__(self, amap=None, jev=None, translator=None, weather_service=None, *, resources=None):
        super().__init__(amap, jev, translator, resources=resources)
        self.weather_service = weather_service if weather_service is not None else self.resources.weather

    async def execute(self, state: TripState) -> AttractionPlanData:
        locked = self.locked_recommendations(state, state.radius_meters)
        selection = self.prepare_recommendations(
            state, locked=locked, searches={key: {"notes": state.additional_requirements, **self.selection_preferences(state)}
                             for key in (state.interests or ["景点"])[:3]},
            radius=state.radius_meters, target_count=state.target_stops,
            types="110000|140100|140200|140400|140600|140700",
        )
        weather, weather_notice = None, None
        if state.consider_weather:
            (result, places), (weather, weather_notice) = await gather_results(
                selection, self.weather_service.lookup(state.current_location, state.travel_date),
            )
        else:
            result, places = await selection
        notes = ["距离为各景点到同一起点的直线距离。",
                 "未核验开放时间、预约和票价。"]
        if locked:
            notes.insert(0, "锁定地点优先展示，其余地点按偏好匹配度排序。")
        if state.consider_weather and weather is None:
            notes.append(weather_notice or "所选日期天气暂不可用。")
        if len(places) < state.target_stops:
            notes.append("符合偏好的候选不足，返回较少景点。")
        if result.failures:
            notes.append("部分周边搜索失败，使用其余真实结果。")
        return AttractionPlanData(
            trip_id=state.trip_id, state_version=state.state_version,
            title=f"{state.city}景点推荐", summary=f"为你挑选了{len(places)}个景点。",
            origin=state.current_location, places=places, routes=[],
            weather=weather, notes=notes,
        )
