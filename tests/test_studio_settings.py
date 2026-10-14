"""SettingsDialog: editing a copy, profiles, the test buttons and the engine section."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtWidgets import QApplication, QLineEdit

    from _studio_fakes import FakeTranslator, dispose_widget, wait_until

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SettingsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def translation_config(self):
        from manga_extract.translation import AIConfig, TranslationConfig

        first = AIConfig(name="A", base_url="https://a.example/v1", api_key="key-a", model="model-a",
                         temperature=0.5, prompt="语气轻松", disable_thinking=True)
        second = AIConfig(name="B", base_url="https://b.example/v1", api_key="key-b", model="model-b")
        return TranslationConfig(mode="web", web_engine="youdao", target_lang="zh-TW",
                                 ai=first, ai_profiles=[first, second])

    def engine_config(self, **kw):
        from manga_extract.runtime import EngineConfig

        return EngineConfig(**kw)

    def dialog(self, config=None, engine=None, devices=("auto", "cpu", "dml"), builder=None):
        from manga_extract.studio.settings_dialog import SettingsDialog

        self.original = config or self.translation_config()
        dlg = SettingsDialog(None, self.original, engine or self.engine_config(), list(devices),
                             translator_builder=builder)
        dlg.show()
        self.addCleanup(dispose_widget, self.app, dlg)
        self.app.processEvents()
        return dlg


class FormTests(SettingsBase):
    def test_the_form_shows_the_current_settings(self):
        dlg = self.dialog()
        self.assertEqual(dlg.engine_box.currentText(), "有道")
        self.assertEqual(dlg.lang_box.currentText(), "繁體中文")
        self.assertEqual(dlg.profile_names(), ["A", "B"])
        self.assertEqual(dlg.profile_box.currentText(), "A")
        self.assertEqual(dlg.name_edit.text(), "A")
        self.assertEqual(dlg.base_edit.text(), "https://a.example/v1")
        self.assertEqual(dlg.key_edit.text(), "key-a")
        self.assertEqual(dlg.model_box.text(), "model-a")
        self.assertEqual(dlg.temp_edit.text(), "0.5")
        self.assertTrue(dlg.thinking_check.isChecked())
        self.assertEqual(dlg.prompt_edit.toPlainText(), "语气轻松")

    def test_the_api_key_is_hidden_until_revealed(self):
        dlg = self.dialog()
        field = dlg.key_edit.findChild(QLineEdit) or dlg.key_edit
        self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Password)

    def test_the_model_box_offers_presets_but_accepts_any_text(self):
        dlg = self.dialog()
        from manga_extract.studio.settings_text import AI_MODEL_PRESETS

        self.assertEqual([dlg.model_box.itemText(i) for i in range(dlg.model_box.count())], AI_MODEL_PRESETS)
        dlg.model_box.setText("my-own-model-7b")
        dlg.save_button.click()
        self.assertEqual(dlg.result_translation.ai.model, "my-own-model-7b")

    def test_the_active_profile_is_the_one_shown_when_it_is_not_the_first(self):
        config = self.translation_config()
        config.ai = config.ai_profiles[1]
        dlg = self.dialog(config)
        self.assertEqual(dlg.profile_box.currentText(), "B")
        self.assertEqual(dlg.model_box.text(), "model-b")


class ProfileTests(SettingsBase):
    def test_edits_are_kept_when_switching_between_profiles(self):
        dlg = self.dialog()
        dlg.base_edit.setText("https://changed-a.example/v1")
        dlg.thinking_check.setChecked(False)
        dlg.profile_box.setCurrentIndex(1)
        self.assertEqual(dlg.name_edit.text(), "B")
        self.assertEqual(dlg.key_edit.text(), "key-b")
        self.assertFalse(dlg.thinking_check.isChecked())
        dlg.key_edit.setText("key-b-new")
        dlg.profile_box.setCurrentIndex(0)
        self.assertEqual(dlg.base_edit.text(), "https://changed-a.example/v1")   # A kept its edit
        dlg.save_button.click()
        a, b = dlg.result_translation.ai_profiles
        self.assertEqual((a.base_url, a.disable_thinking, b.api_key),
                         ("https://changed-a.example/v1", False, "key-b-new"))

    def test_adding_copies_the_current_profile_without_its_key_and_selects_it(self):
        dlg = self.dialog()
        dlg.add_button.click()
        names = dlg.profile_names()
        self.assertEqual(len(names), 3)
        self.assertEqual(names[2], "新配置")                                   # a fresh unique name
        self.assertEqual(dlg.profile_box.currentText(), "新配置")
        self.assertEqual(dlg.key_edit.text(), "")                               # key is not copied
        self.assertEqual(dlg.model_box.text(), "model-a")                       # the rest is
        self.assertEqual(dlg.prompt_edit.toPlainText(), "语气轻松")
        dlg.add_button.click()
        self.assertEqual(dlg.profile_names()[3], "新配置 2")                     # still unique

    def test_the_last_profile_cannot_be_deleted(self):
        config = self.translation_config()
        config.ai_profiles = [config.ai]
        dlg = self.dialog(config)
        self.assertFalse(dlg.delete_button.isEnabled())
        dlg.delete_profile()
        self.assertEqual(len(dlg.config.ai_profiles), 1)

    def test_deleting_asks_first_and_moves_the_selection(self):
        dlg = self.dialog()
        with mock.patch("manga_extract.studio.settings_dialog.MessageBox") as box:
            box.return_value.exec.return_value = False
            dlg.delete_button.click()
            self.assertEqual(dlg.profile_names(), ["A", "B"])                    # declined
            box.return_value.exec.return_value = True
            dlg.delete_button.click()
        self.assertEqual(dlg.profile_names(), ["B"])
        self.assertEqual(dlg.name_edit.text(), "B")                              # neighbour is shown
        self.assertFalse(dlg.delete_button.isEnabled())

    def test_the_active_profile_stays_a_member_after_deleting(self):
        dlg = self.dialog()
        with mock.patch("manga_extract.studio.settings_dialog.MessageBox") as box:
            box.return_value.exec.return_value = True
            dlg.delete_button.click()
        dlg.save_button.click()
        result = dlg.result_translation
        self.assertTrue(any(p is result.ai for p in result.ai_profiles))

    def test_a_blank_name_gets_a_default_and_a_bad_temperature_falls_back(self):
        dlg = self.dialog()
        dlg.name_edit.setText("   ")
        dlg.temp_edit.setText("hot")
        dlg.save_button.click()
        first = dlg.result_translation.ai_profiles[0]
        self.assertEqual((first.name, first.temperature), ("配置 1", 0.3))

    def test_a_valid_temperature_is_kept(self):
        dlg = self.dialog()
        dlg.temp_edit.setText(" 0.75 ")
        dlg.save_button.click()
        self.assertEqual(dlg.result_translation.ai_profiles[0].temperature, 0.75)


class ResultTests(SettingsBase):
    def test_cancel_changes_nothing_even_after_editing(self):
        dlg = self.dialog()
        dlg.engine_box.setCurrentText("谷歌")
        dlg.key_edit.setText("something else")
        dlg.add_button.click()
        dlg.cancel_button.click()
        self.assertIsNone(dlg.result_translation)
        self.assertIsNone(dlg.result_engine)
        self.assertEqual(self.original.web_engine, "youdao")
        self.assertEqual(len(self.original.ai_profiles), 2)
        self.assertEqual(self.original.ai_profiles[0].api_key, "key-a")

    def test_saving_returns_a_copy_and_leaves_the_original_alone(self):
        dlg = self.dialog()
        dlg.engine_box.setCurrentText("MyMemory")
        dlg.lang_box.setCurrentText("English")
        dlg.save_button.click()
        result = dlg.result_translation
        self.assertIsNot(result, self.original)
        self.assertEqual((result.web_engine, result.target_lang), ("mymemory", "en"))
        self.assertEqual((self.original.web_engine, self.original.target_lang), ("youdao", "zh-TW"))

    def test_applying_changes_the_target_in_place_but_not_its_mode(self):
        from manga_extract.studio.settings_dialog import apply_translation_settings
        from manga_extract.translation import TranslationConfig

        target = TranslationConfig(mode="ai")                                    # the window's object
        dlg = self.dialog()
        dlg.engine_box.setCurrentText("谷歌")
        dlg.key_edit.setText("key-new")
        dlg.save_button.click()
        before = id(target)
        apply_translation_settings(target, dlg.result_translation)
        self.assertEqual(id(target), before)
        self.assertEqual((target.mode, target.web_engine), ("ai", "google"))     # mode is not a setting here
        self.assertEqual([p.name for p in target.ai_profiles], ["A", "B"])
        self.assertTrue(any(p is target.ai for p in target.ai_profiles))
        self.assertEqual(target.ai.api_key, "key-new")


class EngineSectionTests(SettingsBase):
    def test_devices_are_listed_with_their_labels_and_the_current_one_is_chosen(self):
        dlg = self.dialog(engine=self.engine_config(device="dml", threads=6, ocr_batch=8))
        self.assertEqual(dlg.device_box.currentIndex(), 2)
        self.assertIn("DirectML", dlg.device_box.currentText())
        self.assertEqual(dlg.threads_edit.text(), "6")
        self.assertEqual(dlg.batch_box.currentText(), "8")

    def test_a_saved_device_that_is_gone_falls_back_to_auto(self):
        dlg = self.dialog(engine=self.engine_config(device="openvino"), devices=("auto", "cpu"))
        self.assertEqual(dlg.device_box.currentIndex(), 0)

    def test_the_new_engine_settings_come_back_normalised(self):
        dlg = self.dialog()
        dlg.device_box.setCurrentIndex(1)                                        # cpu
        dlg.threads_edit.setText("")                                             # empty means auto
        dlg.batch_box.setCurrentText("16")
        dlg.save_button.click()
        engine = dlg.result_engine
        self.assertEqual((engine.device, engine.threads, engine.ocr_batch), ("cpu", 0, 16))

    def test_letters_cannot_be_typed_into_the_thread_box(self):
        from PySide6.QtTest import QTest

        dlg = self.dialog()
        dlg.threads_edit.clear()
        QTest.keyClicks(dlg.threads_edit, "a4b")
        self.assertEqual(dlg.threads_edit.text(), "4")


class TestButtonTests(SettingsBase):
    def test_the_web_test_reports_success_and_uses_the_chosen_engine_and_language(self):
        seen = []
        translator = FakeTranslator()
        dlg = self.dialog(builder=lambda cfg: (seen.append(cfg), translator)[1])
        dlg.engine_box.setCurrentText("谷歌")
        dlg.lang_box.setCurrentText("English")
        dlg.web_test_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.web_result.text().startswith("✓")))
        self.assertTrue(dlg.web_result.text().startswith("✓ 译:"))
        self.assertEqual((seen[0].mode, seen[0].web_engine, seen[0].target_lang), ("web", "google", "en"))

    def test_the_ai_test_uses_the_form_as_it_is_right_now(self):
        seen = []
        dlg = self.dialog(builder=lambda cfg: (seen.append(cfg), FakeTranslator())[1])
        dlg.key_edit.setText("typed-but-not-saved")
        dlg.model_box.setText("typed-model")
        dlg.ai_test_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.ai_result.text().startswith("✓")))
        self.assertEqual((seen[0].mode, seen[0].ai.api_key, seen[0].ai.model),
                         ("ai", "typed-but-not-saved", "typed-model"))
        self.assertEqual(self.original.ai.api_key, "key-a")                      # the config is untouched

    def test_a_failure_is_shown_in_words(self):
        dlg = self.dialog(builder=lambda cfg: FakeTranslator(fail=True))
        dlg.web_test_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.web_result.text().startswith("✗")))
        self.assertIn("boom", dlg.web_result.text())

    def test_a_builder_error_such_as_a_missing_key_is_shown_too(self):
        def build(cfg):
            raise RuntimeError("AI 翻译需要先填写 API Key")

        dlg = self.dialog(builder=build)
        dlg.ai_test_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.ai_result.text().startswith("✗")))
        self.assertIn("API Key", dlg.ai_result.text())

    def test_clicking_again_while_a_test_runs_does_not_start_another(self):
        gate, calls = threading.Event(), []
        dlg = self.dialog(builder=lambda cfg: (calls.append(1), FakeTranslator(gate=gate))[1])
        dlg.web_test_button.click()
        dlg.web_test_button.click()
        self.assertEqual(dlg.web_result.text(), "测试中…")
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: dlg.web_result.text().startswith("✓")))
        self.assertEqual(len(calls), 1)
        dlg.web_test_button.click()                                              # usable again afterwards
        self.assertTrue(wait_until(self.app, lambda: len(calls) == 2))

    def test_the_two_channels_run_independently(self):
        gate = threading.Event()
        dlg = self.dialog(builder=lambda cfg: FakeTranslator(gate=gate if cfg.mode == "web" else None))
        dlg.web_test_button.click()                                              # blocked on the gate
        dlg.ai_test_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.ai_result.text().startswith("✓")))
        self.assertEqual(dlg.web_result.text(), "测试中…")
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: dlg.web_result.text().startswith("✓")))

    def test_a_result_arriving_after_the_dialog_closed_is_harmless(self):
        gate = threading.Event()
        dlg = self.dialog(builder=lambda cfg: FakeTranslator(gate=gate))
        dlg.web_test_button.click()
        time.sleep(0.1)
        dlg.cancel_button.click()
        dispose_widget(self.app, dlg)
        gate.set()
        time.sleep(0.3)
        self.app.processEvents()                                                  # must not raise

    def test_testing_does_not_change_which_profile_is_active(self):
        dlg = self.dialog(builder=lambda cfg: FakeTranslator())
        dlg.profile_box.setCurrentIndex(1)
        dlg.ai_test_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.ai_result.text().startswith("✓")))
        self.assertEqual(dlg.name_edit.text(), "B")
        self.assertEqual(dlg.config.ai.name, "B")


class FakeSettings:
    """Stands in for SettingsDialog (the real one is modal)."""

    def __init__(self, translation=None, engine=None, accept=True):
        self.result_translation, self.result_engine, self.accept = translation, engine, accept

    def exec(self):
        return self.accept


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class MainWindowSettingsTests(SettingsBase):
    def make_window(self, dialog=None, factory=None):
        from _studio_fakes import FakeExtractor, dispose_window, make_engine, tempdir
        from manga_extract.studio.config import StudioConfig
        from manga_extract.studio.main_window import MainWindow

        self.tmp = tempdir(self)
        self.reloads = 0
        engine = make_engine(FakeExtractor())
        original_reload = engine.reload
        engine.reload = lambda: (setattr(self, "reloads", self.reloads + 1), original_reload())[1]
        self.addCleanup(engine.stop)
        self.tpath, self.epath = self.tmp / "translate.json", self.tmp / "engine.json"
        self.tconfig = self.translation_config()
        calls = []

        def make(parent, tconfig, econfig, devices):
            calls.append((tconfig, econfig, devices))
            return factory(parent, tconfig, econfig, devices) if factory else dialog

        self.calls = calls
        window = MainWindow(StudioConfig(), engine=engine, translation_config=self.tconfig,
                            translate_config_path=self.tpath, engine_config_path=self.epath,
                            settings_factory=make)
        self.addCleanup(dispose_window, self.app, window)
        self.addCleanup(window.overlay.shutdown)
        self.addCleanup(window.translator.stop)
        self.assertTrue(wait_until(self.app, lambda: window._engine_ready))
        self.window = window
        return window

    def edited(self, **changes):
        """What the dialog would return: the same base settings with some changes applied."""
        config = self.translation_config()                      # equals the window's starting config
        for key, value in changes.items():
            setattr(config, key, value)
        return config

    def test_saving_updates_the_windows_own_config_in_place_and_writes_the_file(self):
        from manga_extract.translation import TranslationConfig

        edited = self.edited(web_engine="mymemory", target_lang="en")
        edited.ai_profiles[0].name = "Renamed"
        window = self.make_window(FakeSettings(translation=edited))
        identity = id(window.translation_config)
        window.open_settings()
        self.assertEqual(id(window.translation_config), identity)
        self.assertEqual((self.tconfig.web_engine, self.tconfig.target_lang), ("mymemory", "en"))
        self.assertEqual(TranslationConfig.load(self.tpath).web_engine, "mymemory")
        self.assertIn("设置已保存", window.status_left.text())
        menu = [a.text() for a in window.canvas.bar.translate_button.menu().actions()]
        self.assertIn("AI · Renamed", menu)                                       # channels refreshed

    def test_cancelling_changes_and_writes_nothing(self):
        window = self.make_window(FakeSettings(translation=self.edited(web_engine="google"), accept=False))
        window.open_settings()
        self.assertEqual(self.tconfig.web_engine, "youdao")
        self.assertFalse(self.tpath.exists())
        self.assertFalse(self.epath.exists())
        self.assertEqual(self.reloads, 0)

    def test_the_dialog_receives_the_current_settings_and_the_available_devices(self):
        from manga_extract.runtime import EngineConfig

        window = self.make_window(FakeSettings(accept=False))
        EngineConfig(device="cpu", threads=3, ocr_batch=4).save(self.epath)
        window.open_settings()
        tconfig, econfig, devices = self.calls[0]
        self.assertIs(tconfig, self.tconfig)
        self.assertEqual((econfig.device, econfig.threads, econfig.ocr_batch), ("cpu", 3, 4))
        self.assertIn("auto", devices)
        self.assertIn("cpu", devices)

    def test_unchanged_engine_settings_do_not_reload_the_models(self):
        from manga_extract.runtime import EngineConfig

        window = self.make_window(FakeSettings(translation=self.edited(web_engine="google"),
                                               engine=EngineConfig()))             # same as the default
        window.open_settings()
        self.assertEqual(self.reloads, 0)
        self.assertFalse(self.epath.exists())

    def test_changed_engine_settings_are_saved_and_the_models_reload(self):
        from manga_extract.runtime import EngineConfig

        new = EngineConfig(device="cpu", threads=2, ocr_batch=8)
        window = self.make_window(FakeSettings(engine=new))
        window.open_settings()
        self.assertEqual(EngineConfig.load(self.epath), new)
        self.assertEqual(self.reloads, 1)
        self.assertFalse(window._engine_ready)
        self.assertFalse(window.engine_bar.isHidden())
        self.assertIn("重新加载", window.status_right.text())
        self.assertTrue(wait_until(self.app, lambda: window._engine_ready))         # and it comes back
        self.assertTrue(window.engine_bar.isHidden())

    def test_a_settings_file_that_cannot_be_written_is_reported_not_raised(self):
        from manga_extract.runtime import EngineConfig

        window = self.make_window(FakeSettings(translation=self.edited(web_engine="google"),
                                               engine=EngineConfig(device="cpu")))
        self.tpath.mkdir()                                                         # a folder, not a file
        self.epath.mkdir()
        window.open_settings()                                                     # must not raise
        self.assertEqual(self.tconfig.web_engine, "google")                        # still applied in memory
        self.assertEqual(self.reloads, 0)                                          # engine file failed: no reload

    def test_the_settings_button_opens_the_dialog(self):
        window = self.make_window(FakeSettings(accept=False))
        window.settings_button.click()
        self.assertEqual(len(self.calls), 1)

    def test_the_real_dialog_end_to_end_through_the_window(self):
        from manga_extract.runtime import EngineConfig
        from manga_extract.studio.settings_dialog import SettingsDialog
        from manga_extract.translation import TranslationConfig

        class Auto(SettingsDialog):
            def exec(self):                                  # drive it instead of blocking
                self.show()
                self.engine_box.setCurrentText("谷歌")
                self.name_edit.setText("My DeepSeek")
                self.model_box.setText("deepseek-v3")
                self.batch_box.setCurrentText("12")
                self.save_button.click()
                return True

        window = self.make_window(factory=lambda p, t, e, d: Auto(p, t, e, d))
        window.open_settings()
        saved = TranslationConfig.load(self.tpath)
        self.assertEqual(saved.web_engine, "google")
        self.assertEqual((saved.ai_profiles[0].name, saved.ai_profiles[0].model),
                         ("My DeepSeek", "deepseek-v3"))
        self.assertEqual(EngineConfig.load(self.epath).ocr_batch, 12)
        self.assertEqual(self.reloads, 1)


class SettingsTextTests(unittest.TestCase):
    def test_parsers_fall_back_quietly(self):
        from manga_extract.studio.settings_text import parse_temperature, parse_threads

        self.assertEqual(parse_temperature("0.8"), 0.8)
        self.assertEqual(parse_temperature(""), 0.3)
        self.assertEqual(parse_temperature("hot"), 0.3)
        self.assertEqual(parse_threads(" 4 "), 4)
        self.assertEqual(parse_threads("-3"), 0)
        self.assertEqual(parse_threads("x"), 0)


if __name__ == "__main__":
    unittest.main()
