"""景点讲述者：兴趣匹配和距起点的距离。"""

from app.agents.narrators.base import NarratorAgent


class AttractionNarratorAgent(NarratorAgent):
    skill = "ATTRACTION"
    name = "AttractionNarratorAgent"
    instructions = (
        "你专门评价景点游览推荐，重点是兴趣匹配和各景点距起点的直线距离。"
        "列表按匹配度排序，不是游览顺序，不推算步行距离、停留时间或总耗时。"
        "修改后结合兴趣、景点数量或搜索范围的实际变化，评价推荐的取舍。"
        "地点的室内属性、开放时间、预约与票价没有核验，不可据此作确定判断。"
    )

    def fallback(self, state, plan):
        return (f"本次按偏好推荐 {len(plan.places)} 个景点，距离均以同一起点为参照。"
                "可结合兴趣与距离选择，开放与预约情况仍需确认。")
