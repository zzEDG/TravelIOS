"""统一对话控制器：把自然语言规划请求交给主 Agent。"""
from fastapi import APIRouter, Depends
from app.controllers.errors import TravelRoute
from app.models.travel import TravelRequest, TravelResponse
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent

router = APIRouter(route_class=TravelRoute, tags=["统一入口"])

@router.post("/travel", response_model=TravelResponse)
async def plan_travel(
    request: TravelRequest,
    agent: MainAgent = Depends(get_main_agent),
) -> TravelResponse:
    return await agent.plan(request)
