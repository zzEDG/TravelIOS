"""城市漫步的逐站路线规划；候选搜索和地点模型由共享层提供。"""

from app.concurrency import gather_results
from app.integrations.amap_service import AmapRateLimitError, AmapServiceError, Place, distance_m
from app.models.travel import RouteSegment


class CityWalkRoutePlanner:
    def __init__(self, *, route, choose, shortlist, translator, planned_place):
        self.route = route
        self.choose = choose
        self.shortlist = shortlist
        self.translator = translator
        self.planned_place = planned_place

    async def plan(self, state, request, profile, discovered):
        target = state.target_stops
        origin = (state.current_location.longitude, state.current_location.latitude)
        max_walking = state.max_walking_meters
        locked_by_id = {p.poi.id: p for p in state._locked_places}
        order = list(profile.visit_order)
        if profile.end_with and order and order[-1] == profile.end_with:
            order.pop()
        if len(order) + bool(profile.end_with) > target:
            raise ValueError('游览顺序需要的地点多于目标数量，请增加目标数或缩短顺序要求')
        pool, matches = discovered.places, discovered.matches
        current = Place('origin', '当前位置', '', '', *origin, '', '')
        translations = await self.translator.places(pool)
        selected, places, segments, notes = [], [], [], []
        minutes = meters = order_index = 0
        finished = False
        rate_limited = False
        while pool and len(selected) < target:
            active = order[order_index] if order_index < len(order) else None
            finishing = profile.end_with is not None and active is None and len(selected) == target-1
            if finishing:
                active = profile.end_with
            if active:
                eligible = [p for p in pool if active in matches[p.poi_id]]
            else:
                final_options = [p for p in pool if profile.end_with in matches[p.poi_id]] if profile.end_with else []
                reserved = min(final_options,key=lambda p:distance_m(current,p)) if final_options else None
                # 只为末站保留一个候选，不禁掉整个类别；“最后逛公园”仍可前面也逛公园。
                eligible = [p for p in pool if p is not reserved]
                if not eligible and profile.end_with:
                    active, finishing = profile.end_with, True
                    eligible = [p for p in pool if active in matches[p.poi_id]]
            candidates = self.shortlist(eligible, matches, profile, current)
            # 显式把当前 keyword 对应的未访问锁定点加入候选；其他候选照常保留。
            locked_for_keyword = [
                p for p in (pool if active else eligible)
                if p.poi_id in locked_by_id
                and (active is None or active in matches[p.poi_id])
            ]
            candidates_by_id = {p.poi_id: p for p in candidates}
            candidates_by_id.update({p.poi_id: p for p in locked_for_keyword})
            candidates = list(candidates_by_id.values())
            if not candidates:
                notes.append(f'未找到可用于“{active}”的剩余地点，未擅自打乱顺序。')
                break
            results = await gather_results(*(self.route(current, p) for p in candidates))
            rate_limited = rate_limited or any(isinstance(result, AmapRateLimitError) for result in results)
            routes = {p.poi_id: r for p, r in zip(candidates, results)
                      if not isinstance(r, AmapRateLimitError)}
            feasible = [p for p in candidates if routes.get(p.poi_id) is not None
                        and minutes + routes[p.poi_id].duration_min <= request.duration_hours*60
                        and (max_walking is None or meters + routes[p.poi_id].distance_m <= max_walking)]
            if not feasible:
                if any(isinstance(result, AmapRateLimitError) for result in results):
                    raise AmapRateLimitError()
                if len(selected) < 2 and all(route is None for route in routes.values()):
                    raise AmapServiceError('漫步步行路线暂不可用，无法核实距离和时长')
                notes.append('剩余候选无法核实路线，或超过剩余时间、步行距离上限。')
                break
            chosen = await self.choose(request, profile, current, feasible, selected, routes, translations,
                                        matches, target, minutes, meters, max_walking, active, finishing,
                                        locked_ids=set(locked_by_id))
            if chosen is None:
                notes.append('剩余候选未能满足本次偏好与顺序，已停止追加地点。')
                break
            route = routes[chosen.poi_id]
            leg_minutes = route.duration_min
            leg_meters = route.distance_m
            places.append(self.planned_place(
                chosen, step_order=len(selected)+1,
                why_selected=f'检索偏好：{"、".join(k for k in profile.keywords if k in matches[chosen.poi_id])}；'
                             f'高德类别为{chosen.category}；本段步行{leg_meters}米，约{leg_minutes}分钟。',
            ))
            preferences = set(matches[chosen.poi_id])
            if chosen.poi_id in locked_by_id:
                preferences.update(locked_by_id[chosen.poi_id].search_preferences())
                places[-1].poi = locked_by_id[chosen.poi_id].poi.model_copy(deep=True)
                places[-1].why_selected = f"用户锁定的原计划地点；本段步行{leg_meters}米，约{leg_minutes}分钟。"
            places[-1].matched_preferences = sorted(preferences)
            segments.append(RouteSegment(from_poi_id=selected[-1].poi_id if selected else None,
                                         to_poi_id=chosen.poi_id, distance_meters=route.distance_m,
                                         duration_minutes=route.duration_min, polyline=route.polyline))
            selected.append(chosen)
            minutes += leg_minutes
            meters += leg_meters
            current = chosen
            pool = [p for p in pool if p.poi_id != chosen.poi_id]
            if finishing:
                finished = True
                break
            if active:
                order_index += 1
        if not set(locked_by_id).issubset({p.poi_id for p in selected}):
            if rate_limited:
                raise AmapRateLimitError()
            raise ValueError("本次规划未保留所有锁定地点，未保存本次调整，请重试")
        if locked_by_id and (order_index < len(order) or (profile.end_with and not finished)):
            raise ValueError("锁定地点与要求的游览顺序或末站无法同时满足，请调整条件后重试")
        if len(selected) < target:
            notes.append(f'目标为 {target} 个地点，实际找到 {len(selected)} 个，路线已缩短。')
        if order_index < len(order):
            notes.append('尚未完成游览顺序：'+' → '.join(order[order_index:]))
        if profile.end_with and not finished:
            notes.append(f'尚未满足最后一站“{profile.end_with}”的要求。')
        if discovered.failures:
            notes.append('部分偏好搜索暂不可用：'+'、'.join(discovered.failures))
        covered = set().union(*(matches[p.poi_id] for p in selected))
        missing = [key for key in profile.keywords if key not in covered]
        if missing:
            notes.append('按检索来源尚未覆盖的偏好：'+'、'.join(missing))
        if rate_limited:
            notes.append("高德地图接口请求过于频繁，已触发限流；部分候选地点的步行路线未能查询，结果可能不完整。")
        notes += ['预计时长仅包含步行，不含停留；未核验开放时间、预约、票价与返程耗时。']
        return places, segments, minutes, meters, notes, rate_limited
