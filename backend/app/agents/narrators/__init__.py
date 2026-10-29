"""三个规划子功能各自对应一个讲述者 Agent。"""

from app.agents.narrators.citywalk import CityWalkNarratorAgent
from app.agents.narrators.attraction import AttractionNarratorAgent
from app.agents.narrators.food import FoodNarratorAgent

NARRATOR_TYPES = {
    "CITYWALK": CityWalkNarratorAgent,
    "ATTRACTION": AttractionNarratorAgent,
    "FOOD": FoodNarratorAgent,
}
