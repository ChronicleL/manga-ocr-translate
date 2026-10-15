"""多语言 OCR 路由、翻译 source_lang、EngineConfig 新字段。不加载真实模型。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Dict, List
from unittest import mock


# ---------------------------------------------------------------------------
# Pipeline 多识别器路由
# ---------------------------------------------------------------------------
class MultiRecognizerPipelineTests(unittest.TestCase):
    def _make_extractor(self, recognizers):
        from manga_extract.pipeline import MangaExtractor

        class FakeDetector:
            conf_thresh = 0.4
            box_score_thresh = 0.6

            def detect(self, image):
                from manga_extract.detector import PageDetection
                return PageDetection(
                    blocks=[
                        SimpleNamespace(xyxy=[0, 0, 100, 100], language="ja", confidence=0.9),
                        SimpleNamespace(xyxy=[100, 0, 200, 100], language="eng", confidence=0.9),
                        SimpleNamespace(xyxy=[200, 0, 300, 100], language="unknown", confidence=0.9),
                    ],
                    lines=[],
                    line_scores=[],
                    mask=None,
                    width=300,
                    height=100,
                )

        detector = FakeDetector()
        extractor = MangaExtractor(detector=detector, recognizers=recognizers)
        return extractor

    def test_eng_blocks_route_to_eng_recognizer(self):
        calls: Dict[str, List] = {"default": [], "eng": []}

        class FakeRec:
            def __init__(self, key):
                self._key = key

            def recognize_many(self, images, on_progress=None):
                calls[self._key].extend(images)
                return ["text"] * len(images)

        recognizers = {"default": FakeRec("default"), "eng": FakeRec("eng")}

        import numpy as np
        from manga_extract.pipeline import MangaExtractor, _DEFAULT_LANG, _recognize_all

        # Patch crop_polygon_region to return a fake crop
        with mock.patch("manga_extract.pipeline.crop_polygon_region",
                        return_value=np.zeros((10, 10, 3), dtype=np.uint8)):
            # group_text_lines needs to be patched too since we have no real detection data
            with mock.patch("manga_extract.pipeline.group_text_lines") as mock_group:
                mock_group.return_value = [
                    SimpleNamespace(xyxy=[0, 0, 100, 100], language="ja", vertical=False,
                                    lines=[[[0, 0], [100, 0], [100, 100], [0, 100]]]),
                    SimpleNamespace(xyxy=[100, 0, 200, 100], language="eng", vertical=False,
                                    lines=[[[100, 0], [200, 0], [200, 100], [100, 100]]]),
                    SimpleNamespace(xyxy=[200, 0, 300, 100], language="unknown", vertical=False,
                                    lines=[[[200, 0], [300, 0], [300, 100], [200, 100]]]),
                ]
                extractor = self._make_extractor(recognizers)
                result = extractor.extract_page(np.zeros((100, 300, 3), dtype=np.uint8))

        # ja and unknown → default; eng → eng
        self.assertEqual(len(calls["default"]), 2)   # ja + unknown
        self.assertEqual(len(calls["eng"]), 1)        # eng only
        self.assertEqual(len(result.blocks), 3)

    def test_fallback_to_default_when_no_eng_recognizer(self):
        """If only default recognizer exists, eng blocks also go to default."""
        calls = {"default": []}

        class FakeRec:
            def recognize_many(self, images, on_progress=None):
                calls["default"].extend(images)
                return ["text"] * len(images)

        recognizers = {"default": FakeRec()}
        import numpy as np

        with mock.patch("manga_extract.pipeline.crop_polygon_region",
                        return_value=np.zeros((10, 10, 3), dtype=np.uint8)):
            with mock.patch("manga_extract.pipeline.group_text_lines") as mock_group:
                mock_group.return_value = [
                    SimpleNamespace(xyxy=[0, 0, 100, 100], language="eng", vertical=False,
                                    lines=[[[0, 0], [100, 0], [100, 100], [0, 100]]]),
                ]
                from manga_extract.pipeline import MangaExtractor
                extractor = MangaExtractor(
                    detector=SimpleNamespace(
                        conf_thresh=0.4, box_score_thresh=0.6,
                        detect=lambda img: SimpleNamespace(
                            blocks=[], lines=[], line_scores=[], mask=None,
                            width=100, height=100)),
                    recognizers=recognizers,
                )
                result = extractor.extract_page(np.zeros((100, 100, 3), dtype=np.uint8))

        self.assertEqual(len(calls["default"]), 1)

    def test_legacy_recognizer_param_wrapped_as_default(self):
        from manga_extract.pipeline import MangaExtractor, _DEFAULT_LANG

        class Rec:
            pass

        rec = Rec()
        ext = MangaExtractor(recognizer=rec)
        self.assertIs(ext._recognizers[_DEFAULT_LANG], rec)
        self.assertIs(ext._recognizer, rec)   # backward-compat alias

    def test_recognizers_param_takes_precedence(self):
        from manga_extract.pipeline import MangaExtractor, _DEFAULT_LANG

        class RecA:
            pass

        class RecB:
            pass

        ra, rb = RecA(), RecB()
        ext = MangaExtractor(recognizer=ra, recognizers={"default": rb})
        self.assertIs(ext._recognizers[_DEFAULT_LANG], rb)


# ---------------------------------------------------------------------------
# TranslationConfig.source_lang 往返 + 各 translator 参数
# ---------------------------------------------------------------------------
class TranslationSourceLangTests(unittest.TestCase):
    def test_default_source_lang_is_ja(self):
        from manga_extract.translation import TranslationConfig
        config = TranslationConfig()
        self.assertEqual(config.source_lang, "ja")

    def test_source_lang_round_trips_through_dict(self):
        from manga_extract.translation import TranslationConfig
        config = TranslationConfig(source_lang="ko")
        restored = TranslationConfig.from_dict(config.to_dict())
        self.assertEqual(restored.source_lang, "ko")

    def test_old_config_without_source_lang_defaults_to_ja(self):
        from manga_extract.translation import TranslationConfig
        config = TranslationConfig.from_dict({"mode": "web", "target_lang": "zh-CN"})
        self.assertEqual(config.source_lang, "ja")

    def test_google_uses_source_lang_sl_param(self):
        from manga_extract.translation import GoogleTranslator
        captured = {}

        def fake_get(url, timeout):
            captured["url"] = url
            return ["译"]

        with mock.patch("manga_extract.translation._http_get_json", side_effect=fake_get):
            t = GoogleTranslator(target_lang="zh-CN", source_lang="ko")
            t.translate(["테스트"])

        self.assertIn("sl=ko", captured["url"])
        self.assertNotIn("sl=ja", captured["url"])

    def test_youdao_uses_source_lang_from_param(self):
        from manga_extract.translation import YoudaoTranslator
        captured = {}

        def fake_get(url, timeout):
            captured["url"] = url
            return {"errorCode": "0", "translation": ["译"]}

        with mock.patch("manga_extract.translation._http_get_json", side_effect=fake_get):
            t = YoudaoTranslator(target_lang="zh-CN", source_lang="ko")
            t.translate(["테스트"])

        self.assertIn("from=ko", captured["url"])

    def test_mymemory_uses_source_lang_in_langpair(self):
        from manga_extract.translation import MyMemoryTranslator
        captured = {}

        def fake_get(url, timeout):
            captured["url"] = url
            return {"responseData": {"translatedText": "译"}}

        with mock.patch("manga_extract.translation._http_get_json", side_effect=fake_get):
            t = MyMemoryTranslator(target_lang="zh-CN", source_lang="ko")
            t.translate(["테스트"])

        self.assertIn("ko%7C", captured["url"])   # "ko|" URL-encoded

    def test_ai_prompt_reflects_source_lang(self):
        from manga_extract.translation import AIConfig, AITranslator
        t = AITranslator(AIConfig(), target_lang="zh-CN", source_lang="ko")
        prompt = t.system_prompt()
        self.assertIn("韩文", prompt)
        self.assertNotIn("日文", prompt)

    def test_build_translator_passes_source_lang_to_google(self):
        from manga_extract.translation import GoogleTranslator, TranslationConfig
        config = TranslationConfig(mode="web", web_engine="google", source_lang="ko")
        translator = config.build_translator()
        self.assertIsInstance(translator, GoogleTranslator)
        self.assertEqual(translator.source_lang, "ko")


# ---------------------------------------------------------------------------
# EngineConfig 新字段
# ---------------------------------------------------------------------------
class EngineConfigMultilangTests(unittest.TestCase):
    def test_new_fields_have_sensible_defaults(self):
        from manga_extract.runtime import EngineConfig
        config = EngineConfig()
        self.assertEqual(config.primary_lang, "ja")
        self.assertEqual(config.ko_ocr_model, "")
        self.assertTrue(config.eng_ocr_enabled)
        self.assertEqual(config.eng_ocr_model, "microsoft/trocr-base-printed")

    def test_new_fields_round_trip(self):
        from manga_extract.runtime import EngineConfig
        config = EngineConfig(primary_lang="ko", ko_ocr_model="some/model",
                              eng_ocr_enabled=False, eng_ocr_model="other/model")
        restored = EngineConfig.from_dict(config.to_dict())
        self.assertEqual(restored.primary_lang, "ko")
        self.assertEqual(restored.ko_ocr_model, "some/model")
        self.assertFalse(restored.eng_ocr_enabled)
        self.assertEqual(restored.eng_ocr_model, "other/model")

    def test_old_engine_config_without_lang_fields_loads_cleanly(self):
        from manga_extract.runtime import EngineConfig
        old_data = {"device": "cpu", "threads": 4, "ocr_batch": 2}
        config = EngineConfig.from_dict(old_data)
        self.assertEqual(config.primary_lang, "ja")   # default
        self.assertTrue(config.eng_ocr_enabled)

    def test_invalid_primary_lang_normalizes_to_ja(self):
        from manga_extract.runtime import EngineConfig
        config = EngineConfig(primary_lang="zh").normalized()
        self.assertEqual(config.primary_lang, "ja")


if __name__ == "__main__":
    unittest.main()
