"""Jev 只负责在固定的旅行能力集合中选择一个意图。"""

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from app.models.travel import TripState
from app.integrations.jev_service import JevService, JevServiceError


RouteChoice = Literal["CITYWALK", "ATTRACTION", "FOOD", "CLARIFY"]

_CRITERIA = {
    "CITYWALK": "A walking route through streets, parks, architecture, shops or cafes, with several stops. Follow-up changes to an existing city walk also belong here.",
    "ATTRACTION": "Sightseeing at museums, landmarks, historical sites or a specific attraction as the main goal, rather than a neighborhood walk.",
    "FOOD": "Finding a restaurant, meal or local food as the main goal, rather than a walking route with a food stop.",
    "CLARIFY": "The request is unrelated to travel, lacks enough intent to choose a skill, or asks for incompatible main goals without saying which is primary.",
}


@dataclass(frozen=True)
class RouteDecision:
    choice: RouteChoice
    confidence: float


class TravelRouter:
    def __init__(self, jev: JevService | None = None):
        self.jev = jev if jev is not None else JevService()

    async def route(self, english_request: str, previous: TripState | None) -> RouteDecision:
        answers = await self.jev.decide(
            {
                "request": english_request,
                "previous_skill": previous.current_skill if previous else None,
                "is_follow_up": previous is not None,
            },
            {"travel_skill": {
                "type": "choice",
                "instructions": (
                    "Which travel planning skill best handles the user's current intent? "
                    "Use previous_skill for a short follow-up when the new message only changes an existing trip. "
                    "Adding a cafe, restaurant or park stop to a CITYWALK is still CITYWALK; "
                    "mixed place preferences and visit order are supported within one walking route. "
                    "Use FOOD only when finding standalone restaurant alternatives is the main goal, "
                    "not merely adding a food or coffee stop to an existing walk. "
                    "Choose CLARIFY when the main intent cannot be determined."
                ),
                "criteria": _CRITERIA,
            }},
        )
        answer = answers["travel_skill"]
        choice = answer.get("choice")
        confidence = answer.get("confidence")
        if (
            answer.get("type") != "choice"
            or choice not in _CRITERIA
            or not isinstance(confidence, (float, int))
            or isinstance(confidence, bool)
            or not isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise JevServiceError("Jev 返回的能力路由结果无效")
        return RouteDecision(choice=choice, confidence=float(confidence))
