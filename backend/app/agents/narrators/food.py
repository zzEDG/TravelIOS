"""餐厅讲述者：口味偏好、可达性和独立备选方案。"""

from app.agents.narrators.base import NarratorAgent


class FoodNarratorAgent(NarratorAgent):
    skill = "FOOD"
    name = "FoodNarratorAgent"
    instructions = (
        "你专门评价餐厅推荐，重点是菜系偏好、用餐类型、距起点的直线距离和备选方案之间的取舍。"
        "这些餐厅是从同一起点出发的独立备选，用户通常选一家，不是连续吃遍的路线，不推算步行距离或耗时。"
        "修改后说明数量、菜系、忌口或搜索半径的实际变化。"
        "忌口只是软偏好，未核实菜单与过敏原，不能保证餐厅符合任何饮食限制。"
    )

    def fallback(self, state, plan):
        return (f"提供 {len(plan.places)} 家独立备选餐厅，距离均为到同一起点的直线距离，"
                f"搜索半径为 {state.max_distance_meters} 米。可从中选择一家；菜单与忌口适配仍需向餐厅确认。")
