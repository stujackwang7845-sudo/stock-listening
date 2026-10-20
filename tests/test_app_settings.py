"""系統設定「只顯示新制」(2026-10-20)：預設開、存檔、通知。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from core import app_settings  # noqa: E402


class AppSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = mock.patch.object(app_settings, "_path", lambda: os.path.join(self.tmp, "app_settings.json"))
        self.p.start()
        app_settings._listeners.clear()

    def tearDown(self):
        self.p.stop()
        app_settings._listeners.clear()

    def test_default_on_without_file(self):
        self.assertTrue(app_settings.new_regime_only())

    def test_save_and_notify(self):
        got = []
        app_settings.on_change(got.append)
        app_settings.set_new_regime_only(False)
        self.assertFalse(app_settings.new_regime_only())
        self.assertEqual(got, [False])
        app_settings.set_new_regime_only(True)
        self.assertTrue(app_settings.new_regime_only())

    def test_manual_edit_default_off_and_independent(self):
        self.assertFalse(app_settings.allow_manual_edit())
        got_regime, got_edit = [], []
        app_settings.on_change(got_regime.append)
        app_settings.on_change(got_edit.append, key="allow_manual_edit")
        app_settings.set_allow_manual_edit(True)
        self.assertTrue(app_settings.allow_manual_edit())
        self.assertTrue(app_settings.new_regime_only())          # 另一個設定不受影響
        self.assertEqual((got_regime, got_edit), ([], [True]))   # 只通知對應的設定

    def test_broken_file_falls_back_to_default(self):
        with open(os.path.join(self.tmp, "app_settings.json"), "w", encoding="utf-8") as f:
            f.write("{壞掉")
        self.assertTrue(app_settings.new_regime_only())


if __name__ == "__main__":
    unittest.main()
