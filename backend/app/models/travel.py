"""TravelOS 三子系统的共享输入、行程状态与响应契约。"""

from datetime import date, datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints, model_validator

from app.models.walk import CoverPhoto


PreferenceList = Annotated[
    list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]],
    Field(max_length=10),
]


class GeoLocation(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    coordinate_system: Literal["GCJ-02", "WGS84"] = "GCJ-02"


class PlaceCandidate(BaseModel):
    id: str
    name: str
    category: str
    address: str
    location: GeoLocation
    source: Literal["AMAP"] = "AMAP"
    source_url: str | None = None
    retrieved_at: datetime | None = None
    knowledge: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class WeatherState(BaseModel):
    source: Literal["AMAP", "QWEATHER"] = "AMAP"
    source_url: str | None = None
    temperature_max_c: float | None = None
    temperature_min_c: float | None = None
    precipitation_probability_day: float | None = Field(default=None, ge=0, le=1)
    precipitation_probability_night: float | None = Field(default=None, ge=0, le=1)
    forecast_date: date
    day_condition: str
    night_condition: str
    day_temperature_c: float | None = None
    night_temperature_c: float | None = None
    rain_probability: float | None = None
    is_outdoor_favorable: bool | None = None


class TripState(BaseModel):
    # 仅本轮请求使用的编辑意图，不写入历史 JSON；防止旧备注复活已清除的偏好。
    _walk_edits: dict[str, Any] = PrivateAttr(default_factory=dict)
    _previous_places: list[Any] = PrivateAttr(default_factory=list)
    _locked_places: list[Any] = PrivateAttr(default_factory=list)
    trip_id: str
    state_version: int = Field(ge=1)
    city: str
    theme: str
    current_location: GeoLocation
    origin_name: str = ""
    available_hours: float = Field(ge=0.5, le=12)
    target_stops: int = Field(ge=1, le=100)
    max_walking_meters: int | None = Field(default=None, ge=100, le=30000)
    avoid_commercial: bool = False
    travel_date: date | None = None
    additional_requirements: str = ""
    interests: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    visit_order: PreferenceList = Field(default_factory=list)
    end_with: str | None = Field(default=None, min_length=1, max_length=40)
    locked_place_ids: list[str] = Field(default_factory=list, max_length=1000)
    visited_places: list[str] = Field(default_factory=list)
    rejected_places: list[str] = Field(default_factory=list)
    current_skill: Literal["CITYWALK", "ATTRACTION", "FOOD"] = "CITYWALK"
    radius_meters: int = Field(default=5000, ge=500, le=10000)
    consider_weather: bool = True
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] = "dinner"
    cuisine_preference: list[str] = Field(default_factory=list)
    food_avoid: list[str] = Field(default_factory=list)
    max_distance_meters: int = Field(default=1500, ge=100, le=10000)
    food_limit: int = Field(default=3, ge=1, le=1000,description="餐厅推荐的最大数量")
    search_limit: int = Field(default=10, ge=2, le=1000,description="搜索服务的搜索结果的最大数量")
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class TripPlanFields(BaseModel):
    """三个规划接口共享的续接标识、位置名称和锁定状态。"""
    trip_id: str | None = None
    state_version: int | None = Field(default=None, ge=1)
    base_version: int | None = Field(default=None, ge=1)
    city: str | None = Field(default=None, min_length=2, max_length=30)
    origin_name: str | None = Field(default=None, max_length=200)
    additional_requirements: str | None = Field(default=None, max_length=500)
    locked_place_ids: list[str] | None = Field(default=None, max_length=1000, description="完整锁定地点 ID 列表；空列表解除全部锁定")


class CityWalkPlanRequest(TripPlanFields):
    locked_place_ids: list[str] | None = Field(default=None, max_length=100, description="完整锁定地点 ID 列表；空列表解除全部锁定")
    theme: str | None = Field(default=None, min_length=2, max_length=40)
    available_hours: int | None = Field(default=None, ge=2, le=8)
    target_stops: int | None = Field(default=None, ge=2, le=10)
    start_location: GeoLocation | None = None
    max_walking_meters: int | None = Field(default=None, ge=500, le=30000)
    radius_meters: int | None = Field(default=None, ge=500, le=10000)
    avoid_commercial: bool | None = None
    travel_date: date | None = None
    interests: PreferenceList | None = None
    dislikes: PreferenceList | None = None
    visit_order: PreferenceList | None = None
    end_with: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_creation_or_resume(self):
        if self.base_version is not None and not self.trip_id:
            raise ValueError("base_version 必须与 trip_id 一起提供")
        if self.trip_id and self.state_version is None:
            raise ValueError("续接行程时必须提供 state_version")
        if not self.trip_id and (not self.city or not self.start_location):
            raise ValueError("新建行程必须提供 city 和 start_location")
        if not self.trip_id and not (self.interests or self.visit_order or self.end_with
                                     or self.additional_requirements or self.theme):
            raise ValueError("新建漫步请说明想去的地方、顺序或补充要求")
        if self.state_version is not None and not self.trip_id:
            raise ValueError("state_version 必须与 trip_id 一起提供")
        return self


class LocationSearchRequest(BaseModel):
    city: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=30)]
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class DiscoveryRequest(TripPlanFields):
    model_config = ConfigDict(extra="forbid")
    position_field: ClassVar[str]

    @model_validator(mode="after")
    def creation_or_resume(self):
        if self.base_version is not None and not self.trip_id:
            raise ValueError("base_version 必须与 trip_id 一起提供")
        if bool(self.trip_id) != (self.state_version is not None):
            raise ValueError("trip_id 和 state_version 必须一起提供")
        if not self.trip_id and (not self.city or getattr(self, self.position_field) is None):
            raise ValueError(f"新建必须提供 city 和 {self.position_field}")
        return self


class AttractionPlanRequest(DiscoveryRequest):
    position_field = "location"
    location: GeoLocation | None = None
    available_hours: float | None = Field(default=None, ge=0.5, le=12)
    interests: PreferenceList | None = None
    dislikes: PreferenceList | None = None
    target_stops: int | None = Field(default=None, ge=1, le=100)
    radius_meters: int | None = Field(default=None, ge=500, le=10000)
    max_walking_meters: int | None = Field(default=None, ge=100, le=30000)
    consider_weather: bool | None = None
    travel_date: date | None = None


class FoodSearchRequest(DiscoveryRequest):
    position_field = "center_location"
    center_location: GeoLocation | None = None
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    cuisine_preference: PreferenceList | None = None
    avoid: PreferenceList | None = None
    max_distance_meters: int | None = Field(default=None, ge=100, le=10000)
    limit: int | None = Field(default=None, ge=1, le=1000)


class PlannedPlace(BaseModel):
    step_order: int
    poi: PlaceCandidate
    why_selected: str
    stay_minutes: int | None
    distance_from_origin_meters: int | None = None
    matched_preferences: list[str] = Field(default_factory=list)

    def search_preferences(self) -> list[str]:
        """读取结构化检索来源；旧版本从原始选择原因中恢复。"""
        if self.matched_preferences:
            return list(dict.fromkeys(self.matched_preferences))
        prefix = "检索偏好："
        if not self.why_selected.startswith(prefix):
            return []
        value = self.why_selected[len(prefix):].split("；", 1)[0].strip()
        return list(dict.fromkeys(item.strip() for item in value.split("、") if item.strip()))


class RouteSegment(BaseModel):
    from_poi_id: str | None = None
    to_poi_id: str
    distance_meters: int | None = None
    duration_minutes: int | None = None
    polyline: str | None = None


class StayRecommendation(BaseModel):
    poi_id: str = Field(min_length=1)
    suggestion: str = Field(min_length=1, max_length=250)


class TripNarration(BaseModel):
    narrator: Literal["CityWalkNarratorAgent", "AttractionNarratorAgent", "FoodNarratorAgent"]
    evaluation: str = Field(min_length=1, max_length=2000)
    stay_recommendations: list[StayRecommendation] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    based_on_version: int | None = Field(default=None, ge=1)
    user_request: str | None = None
    source: Literal["LLM", "RULES"] = "LLM"


class CityWalkPlanData(BaseModel):
    trip_id: str
    state_version: int
    routed_skill: Literal["CITYWALK"] = "CITYWALK"
    title: str
    summary: str
    origin: GeoLocation
    places: list[PlannedPlace]
    routes: list[RouteSegment]
    estimated_minutes: int
    duration_semantics: Literal["TOTAL_ESTIMATE", "WALKING_ONLY"] = "TOTAL_ESTIMATE"
    warning_codes: list[str] = Field(default_factory=list)
    total_walking_meters: int | None = None
    weather: WeatherState | None = None
    cover: CoverPhoto | None = None
    notes: list[str] = Field(default_factory=list)
    narration: TripNarration | None = None


class AttractionPlanData(CityWalkPlanData):
    routed_skill: Literal["ATTRACTION"] = "ATTRACTION"
    estimated_minutes: int | None = None


class FoodPlanData(CityWalkPlanData):
    routed_skill: Literal["FOOD"] = "FOOD"
    route_semantics: Literal["ALTERNATIVES"] = "ALTERNATIVES"
    estimated_minutes: int | None = None
    total_walking_meters: int | None = None
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"]
    max_distance_meters: int


PlanData = Annotated[
    CityWalkPlanData | AttractionPlanData | FoodPlanData,
    Field(discriminator="routed_skill"),
]


class ResponseMeta(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    decision_source: Literal["JEV"] = "JEV"
    fallback_applied: bool = False
    degraded_services: list[str] = Field(default_factory=list)
    route_confidence: float | None = Field(default=None, ge=0, le=1)


class LocationSearchResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: list[PlaceCandidate]
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class CityWalkPlanResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: CityWalkPlanData
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class AttractionPlanResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: AttractionPlanData
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class FoodSearchResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: FoodPlanData
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class TravelRequest(BaseModel):
    origin_name: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1, max_length=1000)
    trip_id: str | None = None
    state_version: int | None = Field(default=None, ge=1)
    base_version: int | None = Field(default=None, ge=1)
    city: str | None = Field(default=None, min_length=2, max_length=30)
    location: GeoLocation | None = None
    travel_date: date | None = None

    @model_validator(mode="after")
    def validate_resume(self):
        if self.base_version is not None and not self.trip_id:
            raise ValueError("base_version 必须与 trip_id 一起提供")
        if bool(self.trip_id) != (self.state_version is not None):
            raise ValueError("trip_id 与 state_version 必须一起提供")
        if not self.message.strip():
            raise ValueError("message 不能为空")
        return self


class TravelClarificationData(BaseModel):
    routed_skill: Literal["CLARIFY"] = "CLARIFY"
    question: str
    trip_id: str | None = None
    state_version: int | None = None


class TravelResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: PlanData | TravelClarificationData
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class TripSnapshot(BaseModel):
    state: TripState
    latest_plan: PlanData
    latest_version: int


class TripHistoryItem(BaseModel):
    trip_id: str
    state_version: int
    latest_version: int
    skill: Literal["CITYWALK", "ATTRACTION", "FOOD"]
    city: str
    title: str
    summary: str
    origin_name: str
    place_count: int
    created_at: datetime
    updated_at: datetime
    expired: bool


class TripHistoryPage(BaseModel):
    items: list[TripHistoryItem]
    total: int
    page: int
    page_size: int


class TripHistoryResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: TripHistoryPage
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class TripSnapshotResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: TripSnapshot
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class ErrorResponse(BaseModel):
    code: int
    message: str
    data: None = None
    meta: dict[str, Any] | None = None
    details: list[dict[str, str]] | None = None


class TripDeleteResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: None = None
    meta: ResponseMeta = Field(default_factory=ResponseMeta)
