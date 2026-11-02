"""路由描述中的地点专名不应阻断结构化偏好解析。"""
import unittest
from pydantic import ValidationError
from app.services.preferences import PreferencePatch


class PreferenceRoutingTests(unittest.TestCase):
    def test_normalizes_only_names_without_changing_lock_fields(self):
        patch = PreferencePatch(
            target_stops=4, lock_place_names=["石藤咖啡", "百新书局"],
            routing_english="Change the citywalk to 4 stops and keep 石藤咖啡 and 百新书局.",
        )
        self.assertEqual(patch.lock_place_names, ["石藤咖啡", "百新书局"])
        self.assertEqual(patch.target_stops, 4)
        self.assertEqual(patch.routing_english,
                         "Change the citywalk to 4 stops and keep a specified existing place and a specified existing place.")

    def test_unlock_names_are_preserved_too(self):
        patch = PreferencePatch(unlock_place_names=["石藤咖啡"],
                                routing_english="Unlock 石藤咖啡 in the current walk.")
        self.assertEqual(patch.unlock_place_names, ["石藤咖啡"])
        self.assertEqual(patch.routing_english, "Unlock a specified existing place in the current walk.")

    def test_unknown_chinese_intent_is_still_rejected(self):
        for value in ("去看看", "Keep 石藤咖啡 and 改去景点"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                PreferencePatch(lock_place_names=["石藤咖啡"], routing_english=value)

    def test_valid_english_is_unchanged(self):
        value = "Keep the specified cafe and bookstore and shorten the walk to four stops."
        self.assertEqual(PreferencePatch(routing_english=value).routing_english, value)


class ExtractionStagesTests(unittest.IsolatedAsyncioTestCase):
    def extractor(self, result=None, error=None):
        from unittest.mock import AsyncMock
        from app.services.preferences import PreferenceExtractor
        extractor = PreferenceExtractor.__new__(PreferenceExtractor)
        extractor.model = AsyncMock()
        extractor.model.ainvoke.return_value = result
        extractor.model.ainvoke.side_effect = error
        return extractor

    async def test_prompt_preserves_literal_json_and_user_message(self):
        from app.services.preferences import _PROMPT, _SYSTEM
        messages = _PROMPT.format_messages(input='{"message":"第三站换一个"}')
        self.assertEqual(messages[0].content, _SYSTEM)
        self.assertIn('"target_stops": null', messages[0].content)
        self.assertIn('第三站换一个', messages[1].content)

    async def test_call_error_keeps_original_cause(self):
        from app.services.preferences import PreferenceExtractionError
        cause = TimeoutError("timeout")
        with self.assertRaises(PreferenceExtractionError) as caught:
            await self.extractor(error=cause).extract("改成4个地点", None)
        self.assertEqual(caught.exception.stage, "model")
        self.assertIs(caught.exception.__cause__, cause)

    async def test_parse_error_is_distinct_from_validation(self):
        from app.services.preferences import PreferenceExtractionError
        cause = ValueError("invalid JSON")
        for parsed, error in ((None, cause), (None, None), ([], None)):
            with self.subTest(parsed=parsed, error=error):
                extractor = self.extractor({"raw": "original output", "parsed": parsed,
                                            "parsing_error": error})
                with self.assertRaises(PreferenceExtractionError) as caught:
                    await extractor.extract("改成4个地点", None)
                self.assertEqual(caught.exception.stage, "parse")
                self.assertIs(caught.exception.__cause__, error)

    async def test_validation_identifies_field_without_exposing_raw_output(self):
        from app.services.preferences import PreferenceExtractionError
        extractor = self.extractor({"raw": "original output", "parsing_error": None,
                                    "parsed": {"routing_english": "改成4个地点"}})
        with self.assertRaises(PreferenceExtractionError) as caught:
            await extractor.extract("改成4个地点", None)
        self.assertEqual(caught.exception.stage, "validate")
        self.assertIn("routing_english", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, ValidationError)
