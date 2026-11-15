import json
import threading
import time
import unittest
from unittest.mock import patch

from manga_extract.pipeline import clean_ocr_text
from manga_extract.studio.workers import TranslationWorker
from manga_extract.translation import (
    AIConfig,
    AITranslator,
    TranslationError,
    _NO_JSON_FORMAT_URLS,
    _NO_REASONING_EFFORT_URLS,
    _parse_json_array,
)


class CleanOcrTextTests(unittest.TestCase):
    def test_leading_colon_converted_to_ellipsis(self):
        self.assertEqual(clean_ocr_text("：ごめん"), "…ごめん")
        self.assertEqual(clean_ocr_text(":ごめん"), "…ごめん")
        self.assertEqual(clean_ocr_text("：：あ"), "…あ")

    def test_trailing_colon_converted_to_ellipsis(self):
        self.assertEqual(clean_ocr_text("そんな："), "そんな…")
        self.assertEqual(clean_ocr_text("そんな:"), "そんな…")

    def test_isolated_colon_becomes_ellipsis(self):
        self.assertEqual(clean_ocr_text("："), "…")
        self.assertEqual(clean_ocr_text(":"), "…")

    def test_leading_stray_commas_removed(self):
        self.assertEqual(clean_ocr_text("、佐々木さんに"), "佐々木さんに")
        self.assertEqual(clean_ocr_text(",そんな"), "那么…".replace("那么…", "そんな"))

    def test_normal_text_preserved(self):
        self.assertEqual(clean_ocr_text("あたし佐々木さんに、"), "あたし佐々木さんに、")
        self.assertEqual(clean_ocr_text(""), "")


class ParseJsonArrayRobustnessTests(unittest.TestCase):
    def test_standard_array(self):
        self.assertEqual(_parse_json_array('["line 1", "line 2"]'), ["line 1", "line 2"])

    def test_trailing_comma_array(self):
        self.assertEqual(_parse_json_array('["line 1", "line 2", ]'), ["line 1", "line 2"])

    def test_dict_with_list_value(self):
        payload = '{"translations": ["line 1", "line 2"]}'
        self.assertEqual(_parse_json_array(payload), ["line 1", "line 2"])

    def test_dict_with_numeric_keys(self):
        payload = '{"0": "line 1", "1": "line 2"}'
        self.assertEqual(_parse_json_array(payload), ["line 1", "line 2"])

    def test_markdown_code_block_stripping(self):
        raw = "```json\n[\"line 1\", \"line 2\"]\n```"
        self.assertEqual(_parse_json_array(raw), ["line 1", "line 2"])


class AISystemPromptContextTests(unittest.TestCase):
    def test_system_prompt_contains_context_and_format_rules(self):
        translator = AITranslator(AIConfig())
        prompt = translator.system_prompt()
        self.assertIn("上下文连贯与气泡拆分规则", prompt)
        self.assertIn("严禁合并或丢弃条目", prompt)
        self.assertIn("OCR 误识别", prompt)


class SpeedEnhancementsTests(unittest.TestCase):
    def test_ai_translator_concurrency_and_zero_delay(self):
        cfg = AIConfig()
        translator = AITranslator(cfg)
        self.assertEqual(translator.inter_page_delay, 0.0, "AI translator should have 0 delay")
        self.assertGreaterEqual(translator.max_concurrency, 3, "AI translator default concurrency >= 3")

    def test_ai_translator_response_format_and_reasoning_effort(self):
        calls = []

        def fake_post(url, payload, headers, timeout):
            calls.append(payload)
            return {"choices": [{"message": {"content": json.dumps(["ok"], ensure_ascii=False)}}]}

        cfg = AIConfig(disable_thinking=True, base_url="https://api.test.com/v1")
        translator = AITranslator(cfg)
        with patch("manga_extract.translation._http_post_json", side_effect=fake_post):
            res = translator.translate(["hello"])
            self.assertEqual(res, ["ok"])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("response_format"), {"type": "json_object"})
        self.assertEqual(calls[0].get("reasoning_effort"), "none")

    def test_ai_translator_fallback_on_unsupported_params(self):
        calls = []

        def fake_post_with_fallback(url, payload, headers, timeout):
            calls.append(payload)
            if "response_format" in payload:
                raise TranslationError("HTTP 400: response_format is not supported")
            return {"choices": [{"message": {"content": json.dumps(["fallback ok"], ensure_ascii=False)}}]}

        url = "https://api.legacy-proxy.com/v1"
        _NO_JSON_FORMAT_URLS.discard(url.rstrip("/") + "/chat/completions")
        cfg = AIConfig(base_url=url)
        translator = AITranslator(cfg)
        with patch("manga_extract.translation._http_post_json", side_effect=fake_post_with_fallback):
            res = translator.translate(["test"])
            self.assertEqual(res, ["fallback ok"])

        self.assertEqual(len(calls), 2)
        self.assertIn("response_format", calls[0])
        self.assertNotIn("response_format", calls[1])
        self.assertIn(url.rstrip("/") + "/chat/completions", _NO_JSON_FORMAT_URLS)

    def test_translation_worker_concurrent_execution(self):
        running = 0
        max_running = 0
        lock = threading.Lock()
        all_done = threading.Event()
        results = []

        class FakeConcurrentTranslator:
            max_concurrency = 3
            inter_page_delay = 0.0

            def translate(self, texts):
                nonlocal running, max_running
                with lock:
                    running += 1
                    if running > max_running:
                        max_running = running
                time.sleep(0.15)
                with lock:
                    running -= 1
                    results.append(texts[0])
                    if len(results) >= 3:
                        all_done.set()
                return [f"tr:{t}" for t in texts]

        worker = TranslationWorker()
        t = FakeConcurrentTranslator()
        worker.submit(1, "p1", [(0, "line1")], t)
        worker.submit(2, "p2", [(0, "line2")], t)
        worker.submit(3, "p3", [(0, "line3")], t)

        all_done.wait(timeout=3.0)
        self.assertEqual(len(results), 3)
        self.assertGreaterEqual(max_running, 2, "Multiple pages should run concurrently")
        worker.stop()


if __name__ == "__main__":
    unittest.main()
