"""共享周边检索和 Jev 批量推荐，距离统一以起点为参照。"""

import json
from dataclasses import dataclass
from functools import cached_property
from datetime import datetime, timezone

from app.dependencies import ServiceResources
from app.concurrency import gather_results
from app.models.travel import GeoLocation, PlaceCandidate, PlannedPlace
from app.integrations.amap_service import AmapServiceError, Place, amap_marker_url, distance_m
from app.agents.base import SubAgent


@dataclass
class DiscoveryResult:
    places: list[Place]
    matches: dict[str, set[str]]
    failures: list[str]


class DiscoveryAgent(SubAgent):
    def __init__(self, amap=None, jev=None, translator=None, *, resources=None):
        self.resources = resources if resources is not None else ServiceResources()
        self.amap = amap if amap is not None else self.resources.amap
        self.jev = jev if jev is not None else self.resources.jev
        self.translator = translator if translator is not None else self.resources.translator

    @cached_property
    def candidate_search(self):
        return self.resources.candidate_search

    @staticmethod
    def origin(state):
        return Place("origin", "当前位置", "", "", state.current_location.longitude,
                     state.current_location.latitude, "", "")

    async def discover(self, *, city, searches, origin, radius, types="", search_limit=10,
                       per_category_limit=None, allow_empty=False):
        """统一检索、半径校验与去重；保留每个地点命中的偏好。"""
        async def search(keyword, context):
            return await self.candidate_search.search(
                self.amap, city=city, keyword=keyword, origin=origin, radius=radius,
                types=types if keyword in ("餐厅", "景点") else "",
                context={"skill": self.name, **context,'search_limit': search_limit},
            )

        results = await gather_results(*(search(k, context) for k, context in searches.items()))
        center = Place("origin", "起点", "", "", *origin, "", "") if origin else None
        found, matches, failures = {}, {}, []
        for keyword, result in zip(searches, results):
            if result.failed:
                failures.append(keyword)
            valid = []
            for place in result.places:
                if not place.poi_id or (center and distance_m(center, place) > radius):
                    continue
                if self.name == "FOOD":
                    correct_type = place.typecode.startswith("05") if place.typecode else any(
                        word in place.category for word in ("餐饮", "餐厅"))
                elif self.name == "ATTRACTION":
                    correct_type = place.typecode.startswith(("11", "1401", "1402", "1404", "1406", "1407")) if place.typecode else any(
                        word in place.category for word in ("风景", "博物馆", "美术馆", "展览", "科技馆", "天文馆"))
                else:
                    correct_type = True
                if correct_type:
                    valid.append(place)
            if per_category_limit is not None and center:
                valid.sort(key=lambda place: distance_m(center, place))
            for place in valid[:per_category_limit]:
                found.setdefault(place.poi_id, place)
                matches.setdefault(place.poi_id, set()).add(keyword)
        if not found and not allow_empty:
            if failures and len(failures) == len(searches):
                raise AmapServiceError("周边搜索暂不可用")
            raise ValueError("搜索并校验后未找到符合要求的地点，请调整位置、偏好或搜索范围")
        return DiscoveryResult(list(found.values()), matches, failures)

    async def prepare_candidates(self, state, *, searches, radius, target_count,
                                 types="", per_category_limit=None, include_locked=False):
        """三个规划器统一的搜索入口；已锁定的完整计划无需重复搜索。"""
        if len(state._locked_places) < target_count:
            result = await self.discover(
                city=state.city, searches=searches,
                origin=(state.current_location.longitude, state.current_location.latitude),
                radius=radius, types=types, search_limit=state.search_limit,
                per_category_limit=per_category_limit,
                allow_empty=include_locked and bool(state._locked_places),
            )
        else:
            result = DiscoveryResult([], {}, [])

        if include_locked:
            by_id = {place.poi_id: place for place in result.places}
            for old in state._locked_places:
                poi = old.poi
                by_id[poi.id] = Place(
                    poi.id, poi.name, poi.address, poi.category,
                    poi.location.longitude, poi.location.latitude, "", "",
                )
                # 旧计划的真实检索来源参与类别筛选，避免搜索未命中时锁定点被丢弃。
                result.matches.setdefault(poi.id, set()).update(
                    key for key in old.search_preferences() if key in searches
                )
            result.places = list(by_id.values())
        return result

    @staticmethod
    def planned_place(place, *, step_order, stay_minutes=None, why_selected,
                      distance_from_origin_meters=None):
        """由已核验的高德地点构建统一响应。"""
        return PlannedPlace(
            step_order=step_order, stay_minutes=stay_minutes, why_selected=why_selected,
            distance_from_origin_meters=distance_from_origin_meters,
            poi=PlaceCandidate(
                id=place.poi_id, name=place.name, category=place.category, address=place.address,
                location=GeoLocation(latitude=place.latitude, longitude=place.longitude),
                source_url=amap_marker_url(place), retrieved_at=datetime.now(timezone.utc),
            ),
        )

    def locked_recommendations(self, state, radius):
        """把旧计划的锁定点转换为本轮结果，并核实距新起点的直线距离。"""
        origin = self.origin(state)
        locked = []
        for old in state._locked_places:
            poi = old.poi
            point = Place(poi.id, poi.name, poi.address, poi.category,
                          poi.location.longitude, poi.location.latitude, "", "")
            distance = round(distance_m(origin, point))
            if distance > radius:
                raise ValueError(f"锁定地点“{poi.name}”超出当前搜索距离，请调整范围或解除锁定")
            locked.append(old.model_copy(deep=True, update={
                "step_order": len(locked) + 1,
                "stay_minutes": None,
                "distance_from_origin_meters": distance,
                "why_selected": "用户锁定的原计划地点；距离为到当前起点的直线距离。",
            }))
        return locked

    async def complete_recommendations(self, state, candidates, target_count, locked):
        ids = {place.poi.id for place in locked}
        remaining = [place for place in candidates if place.poi_id not in ids]
        slots = target_count - len(locked)
        if slots and remaining:
            extra = await self.recommend(state, remaining, slots, allow_empty=bool(locked))
        else:
            extra = []
        places = locked + extra
        for index, place in enumerate(places, 1):
            place.step_order = index
        return places

    async def recommend(self, state, candidates, target_count, *, allow_empty=False):
        english, translations = await gather_results(
            self.english(state), self.translator.places(candidates),
        )
        origin = self.origin(state)
        distances = {p.poi_id: round(distance_m(origin, p)) for p in candidates}
        answers = await self.jev.decide(
            {"request": english, "target_count": target_count,
             "available_hours": state.available_hours,
             "candidates": {
                 p.poi_id: {**translations[p.poi_id], "distance_from_origin_meters": distances[p.poi_id]}
                 for p in candidates
             }},
            {p.poi_id: {
                "type": "score",
                "instructions": (
                    f"Rate candidates[{p.poi_id}] for the user's preferences. "
                    "All candidates are independent recommendations, not a walking itinerary. "
                    "Distances are straight-line distances from the same origin. "
                    "Use only the supplied place facts. Do not infer opening hours, prices, visit duration, menu ingredients, allergens or historical facts."
                ),
                "criteria": [
                    "Unsuitable: conflicts with the user's preferences.",
                    "Suitable: a reasonable match for the user's preferences.",
                    "Excellent: closely matches the user's main interests and requirements.",
                ],
            } for p in candidates},
        )
        selected = sorted(
            (p for p in candidates if answers[p.poi_id]["score"] >= 1),
            key=lambda p: answers[p.poi_id]["score"], reverse=True,
        )[:target_count]
        if not selected:
            if allow_empty:
                return []
            raise ValueError("没有符合偏好的地点，请调整偏好或搜索范围")
        return [
            self.planned_place(p, step_order=i, distance_from_origin_meters=distances[p.poi_id],
                               why_selected=f"高德类别为{p.category}。")
            for i, p in enumerate(selected, 1)
        ]

    async def english(self, state):
        payload = {"city": state.city, "notes": state.additional_requirements}
        payload.update(self.selection_preferences(state))
        return await self.translator.request(json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def selection_preferences(state) -> dict:
        return {"interests": state.interests, "dislikes": state.dislikes}
