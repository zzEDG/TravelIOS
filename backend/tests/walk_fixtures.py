"""旧回归使用的离线偏好解析替身；真实解析契约另行验证。"""

from app.models.walk_preferences import WalkSearchProfile, SearchPreference


async def fake_walk_preferences(request, **kwargs):
    keys = list(dict.fromkeys([*request.interests, *request.visit_order,
                              *([request.end_with] if request.end_with else [])]))
    if not keys:
        keys = ['历史建筑','咖啡馆','博物馆']
    names = {'历史建筑':'historic building','咖啡馆':'cafe','博物馆':'museum','公园':'park','书店':'bookstore'}
    return WalkSearchProfile(searches=[SearchPreference(query=key,english=names.get(key,'Requested place')) for key in keys],
                             request_english='Shanghai walk with the requested preferences and order',
                             visit_order=request.visit_order,end_with=request.end_with)


def preference_agent(model):
    from types import SimpleNamespace
    from app.agents.citywalk_agent import CityWalkAgent
    agent = CityWalkAgent.__new__(CityWalkAgent)
    agent.resources = SimpleNamespace(llm=model)
    return agent
