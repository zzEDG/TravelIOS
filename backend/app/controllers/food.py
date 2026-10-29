"""TravelOS 餐厅推荐子系统接口。"""

from fastapi import APIRouter, Depends
from app.models.travel import FoodSearchRequest, FoodSearchResponse
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent
from app.controllers.errors import TravelRoute

router = APIRouter(route_class=TravelRoute, prefix="/food", tags=["餐厅推荐"])


@router.post("/search", response_model=FoodSearchResponse)
async def search_food(request: FoodSearchRequest,
           agent: MainAgent = Depends(get_main_agent)) -> FoodSearchResponse:
    return await agent.plan_for("FOOD", request)
