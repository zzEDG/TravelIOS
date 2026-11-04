"""Food：全量候选交给 Jev 批量筛选，返回餐厅距起点的直线距离。"""

from app.models.travel import FoodPlanData, FoodSearchRequest, TripState
from app.agents.discovery import DiscoveryAgent
from app.concurrency import gather_results


class FoodAgent(DiscoveryAgent):
    name = "FOOD"
    request_model = FoodSearchRequest
    location_field = "center_location"
    count_field = "limit"
    default_theme = "餐厅推荐"
    field_mapping = {"avoid": "food_avoid", "limit": "food_limit"}

    @classmethod
    def request_from_message(cls, request, patch, previous):
        if patch.limit is None and patch.target_stops is not None:
            patch = patch.model_copy(update={"limit": patch.target_stops})
        return super().request_from_message(request, patch, previous)

    @staticmethod
    def selection_preferences(state):
        return {"meal_type": state.meal_type, "cuisine": state.cuisine_preference,
                "avoid": state.food_avoid}

    async def execute(self, state: TripState) -> FoodPlanData:
        locked = self.locked_recommendations(state, state.max_distance_meters)
        (result, places), cover, (weather, weather_notice) = await gather_results(
            self.prepare_recommendations(
                state, locked=locked, searches={key: {"notes": state.additional_requirements, **self.selection_preferences(state)}
                                 for key in (state.cuisine_preference or ["餐厅"])[:3]},
                radius=state.max_distance_meters, target_count=state.food_limit, types="050000",
            ),
            self.cover("restaurant food"),
            self.resources.weather.lookup(state.current_location, state.travel_date),
        )
        notes = ["距离为各餐厅到同一起点的直线距离。",
                 "未核验菜单、过敏原、营业时间或价格；口味与忌口仅作软偏好，请向餐厅确认。"]

        if weather is None:
            notes.append(weather_notice or "所选日期天气暂不可用。")
        if cover:
            notes.append("Unsplash 图片仅为氛围示意，不代表餐厅实景或菜品。")
        if locked:
            notes.insert(0, "锁定地点优先展示，其余地点按偏好匹配度排序。")
        if result.failures:
            notes.append("部分周边搜索失败，使用其余真实结果。")
        return FoodPlanData(
            trip_id=state.trip_id, state_version=state.state_version,
            title=f"{state.city}餐厅备选", summary=f"找到{len(places)}家距离范围内的真实餐厅。",
            origin=state.current_location, meal_type=state.meal_type,
            max_distance_meters=state.max_distance_meters,
            places=places, routes=[], weather=weather, cover=cover, notes=notes,
        )
