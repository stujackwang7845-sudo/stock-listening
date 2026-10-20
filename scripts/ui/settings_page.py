"""
系統設定頁(2026-10-20)。

設定存在資料目錄的 app_settings.json(core/app_settings.py)，切換後立即通知各頁，不用重開程式。
  1. 只顯示新制資料(預設開)
  2. 允許手動修改處置統計的漲跌幅(預設關)
"""
from PyQt6.QtWidgets import QCheckBox, QFrame, QLabel, QVBoxLayout, QWidget

from core import app_settings

_TOGGLE_STYLE = """
QCheckBox { font-size: 16px; font-weight: bold; color: #E0E0E0; spacing: 12px; }
QCheckBox::indicator { width: 46px; height: 24px; border-radius: 12px; }
QCheckBox::indicator:unchecked { background-color: #555555; border: 2px solid #777777; }
QCheckBox::indicator:checked { background-color: #2E7D32; border: 2px solid #4CAF50; }
"""

_OPTIONS = [
    ("new_regime_only", "只顯示新制資料（2026/08/10 起公告的處置）",
     "開啟（預設）：歷史紀錄、處置統計、統計圖表、詳細條件的歷史處置，都只顯示新制資料；"
     "處置預測總覽與監控儀表板不能切換到 2026/08/10 以前的日期。\n"
     "關閉：顯示全部資料，包含舊制（處置 10 天、5 分／20 分撮合）。",
     ("目前：只顯示新制", "目前：顯示全部資料（含舊制）")),
    ("allow_manual_edit", "允許手動修改處置統計的漲跌幅",
     "關閉（預設）：處置統計表格不能編輯，畫面一律顯示系統依官方行情算出的數字。\n"
     "開啟：可以雙擊格子（或按 F2）修改，存檔前會再確認一次；存下的手動值會永遠蓋過系統計算值，"
     "詳細條件頁也會顯示這個值。",
     ("目前：可以手動修改", "目前：不能修改（顯示系統計算值）")),
]


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("系統設定")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #FFFFFF;")
        layout.addWidget(title)

        self.toggles = {}
        self.states = {}
        for key, label, desc_text, state_text in _OPTIONS:
            box = QFrame()
            box.setStyleSheet("QFrame { background-color: #1E1E1E; border: 1px solid #333333; border-radius: 8px; }")
            inner = QVBoxLayout(box)
            inner.setContentsMargins(16, 14, 16, 14)

            cb = QCheckBox(label)
            cb.setStyleSheet(_TOGGLE_STYLE)
            cb.setChecked(app_settings.get(key))
            cb.toggled.connect(lambda checked, k=key: self._on_toggled(k, checked))
            inner.addWidget(cb)

            desc = QLabel(desc_text)
            desc.setWordWrap(True)
            desc.setStyleSheet("color: #9a9a9a; font-size: 13px; border: none; padding-left: 58px;")
            inner.addWidget(desc)

            state = QLabel()
            state.setStyleSheet("color: #4da6ff; font-size: 13px; border: none; padding-left: 58px;")
            inner.addWidget(state)

            self.toggles[key] = cb
            self.states[key] = (state, state_text)
            self._show_state(key, cb.isChecked())
            layout.addWidget(box)
        layout.addStretch()

        # 舊名稱(既有程式/測試沿用)
        self.new_only = self.toggles["new_regime_only"]
        self.state = self.states["new_regime_only"][0]

    def _show_state(self, key, on):
        label, (on_txt, off_txt) = self.states[key]
        label.setText(on_txt if on else off_txt)

    def _on_toggled(self, key, checked):
        try:
            app_settings.set_value(key, checked)
        except Exception as e:
            self.states[key][0].setText(f"儲存失敗：{e}")
            return
        self._show_state(key, checked)
