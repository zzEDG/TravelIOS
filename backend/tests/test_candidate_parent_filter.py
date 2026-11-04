import unittest

from app.integrations.amap_service import AmapService, Place
from app.services.candidate_search import SearchAction, _SearchSession


def place(poi_id, parent_id=''):
    return Place(poi_id, poi_id, '', '', 121.0, 31.0, '', '', parent_id=parent_id)


class FakeMap:
    async def search_around(self, *args):
        return [place('child1', 'park'), place('child2', 'park'),
                place('park'), place('other')]


class ParentFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_parent_preferred_and_food_unchanged(self):
        for skill, expected in [('ATTRACTION', {'park', 'other'}),
                                ('FOOD', {'park', 'other', 'child1', 'child2'})]:
            session = _SearchSession(FakeMap(), 'city', (121.0, 31.0), 1000, '', skill=skill)
            result = await session.search(SearchAction(name='search_places', keyword='park'))
            self.assertEqual({p['id'] for p in result['candidates']}, expected)

    async def test_earlier_child_reserves_parent(self):
        session = _SearchSession(FakeMap(), 'city', (121.0, 31.0), 1000, '', skill='CITYWALK')
        session.accepted['child1'] = place('child1', 'park')
        result = await session.search(SearchAction(name='search_places', keyword='park'))
        self.assertEqual([p['id'] for p in result['candidates']], ['other'])

    def test_parse_parent_and_missing_parent(self):
        pois = [{'id': 'a', 'name': 'a', 'location': '121,31', 'parent': 'park'},
                {'id': 'b', 'name': 'b', 'location': '121,31', 'parent': []}]
        parsed = AmapService._parse_places({'pois': pois}, 'park')
        self.assertEqual([p.parent_id for p in parsed], ['park', ''])
