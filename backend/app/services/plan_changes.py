"""用前后版本的真实状态和结果描述修改，不让模型猜测差异。"""

from app.models.travel import PlanData, TripState
from app.models.trip_store import TripRecord

SKILL_NAMES = {"CITYWALK": "城市漫步", "ATTRACTION": "景点游览", "FOOD": "餐厅推荐"}
COMMON_FIELDS = {
    "city": "城市", "origin_name": "起点名称", "travel_date": "出行日期",
    "additional_requirements": "补充要求",
}
SKILL_FIELDS = {
    "CITYWALK": {"theme": "行程名称", "available_hours": "可用时长（小时）",
                 "max_walking_meters": "步行上限（米）", "radius_meters": "搜索半径（米）",
                 "avoid_commercial": "避开商业区",
                 "interests": "兴趣偏好", "dislikes": "避开偏好",
                 "visit_order": "游览顺序偏好", "end_with": "最后一站偏好"},
    "ATTRACTION": {"available_hours": "可用时长（小时）", "interests": "兴趣偏好",
                   "dislikes": "避开偏好", "radius_meters": "搜索半径（米）",
                   "consider_weather": "考虑天气"},
    "FOOD": {"meal_type": "用餐类型", "cuisine_preference": "菜系偏好",
             "food_avoid": "忌口偏好", "max_distance_meters": "搜索半径（米）"},
}


def display(value):
    if value is None or value == "" or value == []:
        return "未设置"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return "、".join(value)
    return str(value)


def target_count(state):
    return state.food_limit if state.current_skill == "FOOD" else state.target_stops


def describe_changes(previous: TripRecord | None, state: TripState, plan: PlanData) -> list[str]:
    if previous is None:
        return []
    old, old_plan = previous.state, previous.latest_plan
    changes = []
    if old.current_skill != state.current_skill:
        changes.append(f"规划类型：{SKILL_NAMES[old.current_skill]} → {SKILL_NAMES[state.current_skill]}")
    fields = {**COMMON_FIELDS, **SKILL_FIELDS[state.current_skill]}
    for field, label in fields.items():
        before, after = getattr(old, field), getattr(state, field)
        if before != after:
            changes.append(f"{label}：{display(before)} → {display(after)}")
    if old.current_location != state.current_location:
        changes.append(f"起点坐标：{old.current_location.longitude},{old.current_location.latitude} → "
                       f"{state.current_location.longitude},{state.current_location.latitude}")
    if target_count(old) != target_count(state):
        changes.append(f"目标地点数量：{target_count(old)} → {target_count(state)}")
    if len(old_plan.places) != len(plan.places):
        changes.append(f"实际地点数量：{len(old_plan.places)} → {len(plan.places)}")
    if old.current_skill == state.current_skill:
        if state.current_skill == "CITYWALK" and old_plan.duration_semantics != plan.duration_semantics:
            changes.append("时长口径已变化：旧版包含停留，新版仅包含步行，两者不能直接比较。")
        elif old_plan.estimated_minutes != plan.estimated_minutes:
            label = ("预计步行时长（分钟）" if plan.duration_semantics == "WALKING_ONLY"
                     else "估计总时长（分钟）")
            changes.append(f"{label}：{display(old_plan.estimated_minutes)} → {display(plan.estimated_minutes)}")
        if old_plan.total_walking_meters != plan.total_walking_meters:
            changes.append(f"总步行距离（米）：{display(old_plan.total_walking_meters)} → "
                           f"{display(plan.total_walking_meters)}")
        if old_plan.routes != plan.routes and state.current_skill == "CITYWALK":
            changes.append("步行路线信息已更新，请以当前各段距离与耗时为准。")
        if old_plan.weather != plan.weather:
            changes.append("天气信息已更新，请参考当前预报安排出行。")
    before = {p.poi.id: p.poi.name for p in old_plan.places}
    after = {p.poi.id: p.poi.name for p in plan.places}
    added = [name for key, name in after.items() if key not in before]
    removed = [name for key, name in before.items() if key not in after]
    if added:
        changes.append("新增地点：" + "、".join(added))
    if removed:
        changes.append("移除地点：" + "、".join(removed))
    if before.keys() == after.keys() and list(before) != list(after):
        label = "备选排序调整为：" if state.current_skill == "FOOD" else "地点顺序调整为："
        changes.append(label + " → ".join(after.values()))
    return changes or ["已重新规划；地点和已记录的主要条件未变化。"]
