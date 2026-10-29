"""三个子 Agent 的共同约定：参数准备、默认值和规划执行。"""

from abc import ABC, abstractmethod
from typing import ClassVar, Literal

from app.models.travel import (
    AttractionPlanRequest, CityWalkPlanRequest, FoodSearchRequest,
    PlanData, TravelRequest, TripState,
)
from app.services.preferences import PreferencePatch

AgentName = Literal["CITYWALK", "ATTRACTION", "FOOD"]
PlanningRequest = CityWalkPlanRequest | AttractionPlanRequest | FoodSearchRequest


class ClarificationNeeded(ValueError):
    """请求缺少必需信息，应向用户追问而不是执行规划。"""


class SubAgent(ABC):
    name: ClassVar[AgentName]
    request_model: ClassVar[type[PlanningRequest]]
    location_field: ClassVar[str]
    count_field: ClassVar[str] = "target_stops"
    field_mapping: ClassVar[dict[str, str]] = {}
    default_theme: ClassVar[str]

    @classmethod
    def _message_payload(
        cls, request: TravelRequest, patch: PreferencePatch, previous: TripState | None,
    ) -> dict:
        """只接收当前子 Agent 支持的偏好；未提及的字段留给状态层继承。"""
        city = request.city or patch.city or (previous.city if previous else None)
        location = request.location or (previous.current_location if previous else None)
        if not city:
            raise ClarificationNeeded("请告诉我你所在或要前往的城市。")
        if location is None:
            raise ClarificationNeeded(
                "请提供起点坐标 location（latitude、longitude），以便计算真实步行路线。"
            )
        payload = {
            field: value for field, value in patch.model_dump(exclude_none=True).items()
            if field in cls.request_model.model_fields
        }
        if previous:
            # 模型可能用 [] 表示本轮未提及；明确清空时才覆盖已有偏好。
            for field in ("interests", "dislikes"):
                if payload.get(field) == [] and not getattr(patch, f"clear_{field}"):
                    payload.pop(field)
            payload.update(
                trip_id=previous.trip_id, state_version=request.state_version,
                base_version=request.base_version,
            )
        payload.update(city=city, **{cls.location_field: location})
        for field in ("origin_name", "travel_date"):
            value = getattr(request, field)
            if field in cls.request_model.model_fields and value is not None:
                payload[field] = value
        return payload

    @classmethod
    def request_from_message(
        cls, request: TravelRequest, patch: PreferencePatch, previous: TripState | None,
    ) -> PlanningRequest:
        return cls.request_model.model_validate(cls._message_payload(request, patch, previous))

    @classmethod
    def state_defaults(cls, request: PlanningRequest, previous: TripState | None) -> dict:
        return {} if previous else {
            "theme": cls.default_theme, "available_hours": 3, "target_stops": 2,
        }

    @abstractmethod
    async def execute(self, state: TripState) -> PlanData:
        """根据已经合并并校验的状态，生成本子系统的规划结果。"""
