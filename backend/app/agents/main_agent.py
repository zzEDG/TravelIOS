"""主 Agent：理解需求 → 路由到一个子 Agent → 执行 → 保存新版本。"""

from starlette.concurrency import run_in_threadpool

from app.dependencies import ServiceResources

from app.agents.attraction_agent import AttractionAgent
from app.agents.base import AgentName, ClarificationNeeded, PlanningRequest, SubAgent
from app.agents.citywalk_agent import CityWalkAgent
from app.agents.food_agent import FoodAgent
from app.agents.narrators import NARRATOR_TYPES
from app.agents.narrators.base import NarratorAgent
from app.models.travel import ResponseMeta, TravelClarificationData, TravelRequest, TravelResponse
from app.models.trip_store import TripRecord
from app.services.intent_router import RouteDecision, TravelRouter
from app.services.preferences import PreferenceExtractor, PreferencePatch, explicitly_keep_all_previous_places
from app.services.count_preferences import count_edit
from app.services.trip_service import TripService
from app.services.plan_result import add_count_notice, plan_response


# 路由结果与三个子 Agent 一一对应；新增能力时只需注册其实现。
AGENT_TYPES: dict[AgentName, type[SubAgent]] = {
    "CITYWALK": CityWalkAgent,
    "ATTRACTION": AttractionAgent,
    "FOOD": FoodAgent,
}


class MainAgent:
    def __init__(
        self, trips: TripService | None = None,
        extractor: PreferenceExtractor | None = None, router: TravelRouter | None = None,
        *, agents: dict[AgentName, SubAgent] | None = None,
        resources: ServiceResources | None = None,
        narrators: dict[AgentName, NarratorAgent] | None = None,
    ):
        self.resources = resources if resources is not None else ServiceResources()
        self.trips = trips if trips is not None else TripService(amap=self.resources.amap)
        self.extractor = extractor
        self.router = router
        self.agents = dict(agents or {})
        self.narrators = dict(narrators or {})
        if (self.agents.keys() | self.narrators.keys()) - AGENT_TYPES.keys():
            raise ValueError("只能注册城市漫步、景点游览和餐厅推荐子 Agent")

    def get_subagent(self, name: AgentName) -> SubAgent:
        if name not in self.agents:
            self.agents[name] = AGENT_TYPES[name](amap=self.trips.amap, resources=self.resources)
        return self.agents[name]

    def get_narrator(self, name: AgentName) -> NarratorAgent:
        if name not in self.narrators:
            self.narrators[name] = NARRATOR_TYPES[name](resources=self.resources)
        return self.narrators[name]

    async def plan(self, request: TravelRequest) -> TravelResponse:
        # 在模型调用前检查版本，避免对过期请求进行无效规划。
        previous = await run_in_threadpool(self.trips.previous, request)
        state = previous.state if previous else None
        if state:
            state._previous_places = previous.latest_plan.places
        edit = count_edit(request.message)
        if edit and edit.can_keep_agent(previous):
            # “改成五个 / 再加两个”只改当前规划的数量，不让模型丢掉数字或切换子系统。
            patch = PreferencePatch(routing_english="Update the number of places in the current plan")
            decision = RouteDecision(state.current_skill, 1.0)
        else:
            extractor = self.extractor if self.extractor is not None else self.resources.extractor
            router = self.router if self.router is not None else self.resources.router
            patch = await extractor.extract(request.message, state)
            decision = await router.route(patch.routing_english, state)
        try:
            if decision.choice == "CLARIFY":
                raise ClarificationNeeded(
                    "你主要想安排城市漫步、参观景点，还是寻找餐厅？请补充主要目标。"
                )
            agent_type = AGENT_TYPES[decision.choice]
            if edit and edit.applies_to(decision.choice):
                count = edit.resolve(previous, decision.choice)
                patch = patch.model_copy(update={agent_type.count_field: count})
            child_request = agent_type.request_from_message(request, patch, state)
            explicit_keep_all = explicitly_keep_all_previous_places(request.message)
            keep_all = patch.keep_all_previous_places or explicit_keep_all
            if keep_all or patch.lock_place_names or patch.unlock_place_names or patch.clear_locked_places:
                if not previous:
                    raise ClarificationNeeded("请先生成计划，再选择要保留的地点。")
                if previous.latest_plan.routed_skill != decision.choice:
                    raise ClarificationNeeded("保留地点需要续接该地点所在的计划，请明确要调整的计划类型。")
                places = previous.latest_plan.places
                def resolve(name):
                    exact = [p for p in places if p.poi.name.strip() == name.strip()]
                    matches = exact or [p for p in places if name.strip() in p.poi.name]
                    if len(matches) != 1:
                        raise ClarificationNeeded(f"无法唯一确定“{name}”，请提供当前计划中的完整地点名称。")
                    return matches[0].poi.id
                locked = [] if patch.clear_locked_places else list(state.locked_place_ids)
                if keep_all:
                    locked = list(dict.fromkeys([*locked, *(place.poi.id for place in places)]))
                for name in ([] if explicit_keep_all else patch.unlock_place_names or []):
                    poi_id = resolve(name)
                    locked = [item for item in locked if item != poi_id]
                for name in ([] if keep_all else patch.lock_place_names or []):
                    poi_id = resolve(name)
                    if poi_id not in locked:
                        locked.append(poi_id)
                child_request = child_request.model_copy(update={"locked_place_ids": locked})
                child_request = agent_type.request_model.model_validate(child_request.model_dump(exclude_unset=True))
        except ClarificationNeeded as exc:
            return TravelResponse(
                data=TravelClarificationData(
                    question=str(exc), trip_id=state.trip_id if state else None,
                    state_version=state.state_version if state else None,
                ),
                meta=ResponseMeta(route_confidence=decision.confidence),
            )
        result = await self._dispatch(decision.choice, child_request, previous, allow_switch=True, message=request.message)
        result.meta.route_confidence = decision.confidence
        return result

    async def plan_for(
        self, name: AgentName, request: PlanningRequest, *, allow_switch: bool = False,
    ) -> TravelResponse:
        """子系统表单已明确目标，直接委派；与自然语言入口共用执行和保存流程。"""
        if name not in AGENT_TYPES or not isinstance(request, AGENT_TYPES[name].request_model):
            raise ValueError("规划参数与目标子 Agent 不匹配")
        return await self._dispatch(name, request, await run_in_threadpool(self.trips.previous, request), allow_switch=allow_switch)

    async def _dispatch(
        self, name: AgentName, request: PlanningRequest, previous: TripRecord | None,
        *, allow_switch: bool, message: str | None = None,
    ) -> TravelResponse:
        state = await self.trips.prepare(
            request, AGENT_TYPES[name], previous, allow_switch=allow_switch,
        )
        agent = self.get_subagent(name)
        plan = await agent.execute(state)
        add_count_notice(state, plan)
        plan.narration = await self.get_narrator(name).evaluate(
            state, plan, previous, message=message,
            requested_fields=request.model_dump(mode="json", exclude_unset=True,
                exclude={"trip_id", "state_version", "base_version"}),
        )
        record = await run_in_threadpool(self.trips.save,
            state, plan, previous, allow_expired=request.base_version is not None,
        )
        return plan_response(record)
