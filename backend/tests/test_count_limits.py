"""所有规划入口的数量上限保持一致，包括对话偏好提取。"""
import unittest

from pydantic import ValidationError

from app.models.travel import CityWalkPlanRequest, AttractionPlanRequest, FoodSearchRequest, TripState
from app.services.preferences import PreferencePatch


class CountLimitTests(unittest.TestCase):
    def test_plan_requests_accept_100_and_reject_101(self):
        for model, field, position in (
            (CityWalkPlanRequest, 'target_stops', 'start_location'),
            (AttractionPlanRequest, 'target_stops', 'location'),
            (FoodSearchRequest, 'limit', 'center_location'),
        ):
            with self.subTest(model=model.__name__):
                payload = {'city': '上海', position: {'latitude': 31.23, 'longitude': 121.47},
                           'additional_requirements': '寻找附近地点'}
                for count in (10, 100):
                    self.assertEqual(getattr(model.model_validate({**payload, field: count}), field), count)
                with self.assertRaises(ValidationError):
                    model.model_validate({**payload, field: 101})

    def test_chat_and_state_share_request_bounds(self):
        for field in ('limit', 'target_stops'):
            with self.subTest(field=field):
                patch = {'routing_english': 'Find nearby places', field: 100}
                self.assertEqual(getattr(PreferencePatch.model_validate(patch), field), 100)
                with self.assertRaises(ValidationError):
                    PreferencePatch.model_validate({**patch, field: 101})
        # Validate each quantity against the actual state field, without requiring
        # unrelated trip metadata or external services.
        from typing import Annotated
        from pydantic import TypeAdapter
        for field in ('food_limit', 'target_stops'):
            schema = TripState.model_fields[field]
            adapter = TypeAdapter(Annotated[schema.annotation, *schema.metadata])
            self.assertEqual(adapter.validate_python(100), 100)
            with self.assertRaises(ValidationError):
                adapter.validate_python(101)
