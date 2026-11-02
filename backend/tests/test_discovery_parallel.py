"""Event barriers verify discovery overlap and cancellation without external services."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from app.agents.attraction_agent import AttractionAgent
from app.agents.food_agent import FoodAgent
from narrator_fixtures import OfflineNarrationMixin
from test_discovery import FakeAmap, FakeJev, FakeTranslator, state


class DiscoveryParallelTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_search_preferences_and_weather_overlap(self):
        for skill, agent_type in (("FOOD", FoodAgent), ("ATTRACTION", AttractionAgent)):
            with self.subTest(skill=skill):
                entered = set()
                ready = asyncio.Event()
                expected = {"search", "preferences"}
                if skill == "ATTRACTION":
                    expected.add("weather")

                async def enter(name):
                    entered.add(name)
                    if entered == expected:
                        ready.set()
                    await ready.wait()

                class Translator(FakeTranslator):
                    async def request(self, text):
                        await enter("preferences")
                        return await super().request(text)

                    async def places(self, places):
                        self.assert_search_done = search_done.is_set()
                        return await super().places(places)

                search_done = asyncio.Event()
                translator = Translator()
                weather = Mock()

                async def lookup(*args):
                    await enter("weather")
                    return None, "weather unavailable"

                weather.lookup = AsyncMock(side_effect=lookup)
                kwargs = {"weather_service": weather} if skill == "ATTRACTION" else {}
                agent = agent_type(FakeAmap(), FakeJev(), translator, **kwargs)
                original = agent.prepare_candidates

                async def prepare(*args, **kwargs):
                    await enter("search")
                    result = await original(*args, **kwargs)
                    search_done.set()
                    return result

                agent.prepare_candidates = prepare
                trip = state(skill).model_copy(update={"consider_weather": True})
                plan = await asyncio.wait_for(agent.execute(trip), 2)
                self.assertEqual(entered, expected)
                self.assertTrue(translator.assert_search_done)
                self.assertTrue(plan.places)
                if skill == "ATTRACTION":
                    self.assertIn("weather unavailable", plan.notes)

    async def test_failure_cancels_weather_and_translation(self):
        entered = set()
        ready = asyncio.Event()
        cancelled = set()

        async def pending(name):
            entered.add(name)
            if len(entered) == 2:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(name)

        translator = FakeTranslator()
        async def request(text):
            return await pending("preferences")
        translator.request = request
        weather = Mock()
        async def lookup(*args):
            return await pending("weather")
        weather.lookup = lookup
        agent = AttractionAgent(FakeAmap(), FakeJev(), translator, weather)
        async def fail(*args, **kwargs):
            await ready.wait()
            raise ValueError("search failed")
        agent.prepare_candidates = fail
        with self.assertRaisesRegex(ValueError, "search failed"):
            await asyncio.wait_for(agent.execute(state("ATTRACTION").model_copy(
                update={"consider_weather": True})), 2)
        self.assertEqual(cancelled, {"preferences", "weather"})

    async def test_weather_disabled_skips_lookup(self):
        weather = Mock()
        weather.lookup = AsyncMock(side_effect=AssertionError("weather disabled"))
        agent = AttractionAgent(FakeAmap(), FakeJev(), FakeTranslator(), weather)
        await agent.execute(state("ATTRACTION").model_copy(update={"consider_weather": False}))
        weather.lookup.assert_not_awaited()
