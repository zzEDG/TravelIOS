"""旧功能回归隔离新增的外部讲述模型调用。"""
from unittest.mock import patch, PropertyMock
from candidate_fixtures import OfflineCandidateSearch

from app.agents.narrators.base import NarrationDraft


class OfflineNarrationMixin:
    def setUp(self):
        super().setUp()
        model = patch('app.agents.narrators.base.NarratorAgent._generate',
                      return_value=NarrationDraft(evaluation='根据已核实的地点和路线，结合本次偏好安排出行。'))
        model.start()
        self.addCleanup(model.stop)
        for target in ('app.agents.citywalk_agent.CityWalkAgent.candidate_search',
                       'app.agents.discovery.DiscoveryAgent.candidate_search'):
            search = patch(target, new_callable=PropertyMock, return_value=OfflineCandidateSearch())
            search.start()
            self.addCleanup(search.stop)
