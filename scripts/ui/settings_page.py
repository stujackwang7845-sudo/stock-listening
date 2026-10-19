"""
系統設定頁(2026-10-20)。

目前只有一個選項：「只顯示新制資料」(預設開)。設定存在資料目錄的 app_settings.json(core/app_settings.py)，
切換後立即通知各頁重新整理，不用重開程式。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QFrame, QLabel, QVBoxLayout, QWidget

from core import app_settings

_TOGGLE_STYLE = """
QCheckBox { font-size: 16px; font-weight: bold; color: #E0E0E0; spacing: 12px; }
QCheckBox::indicator { width: 46px; height: 24px; border-radius: 12px; }
QCheckBox::indicator:unchecked { background-color: #555555; border: 2px solid #777777; }
QCheckBox::indicator:checked { background-color: #2E7D32; border: 2px solid #4CAF50; }
"""


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("系統設定")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #FFFFFF;")
        layout.addWidget(title)

        box = QFrame()
        box.setStyleSheet("QFrame { background-color: #1E1E1E; border: 1px solid #333333; border-radius: 8px; }")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(16, 14, 16, 14)

        self.new_only = QCheckBox("只顯示新制資料（2026/08/10 起公告的處置）")
        self.new_only.setStyleSheet(_TOGGLE_STYLE)
        self.new_only.setChecked(app_settings.new_regime_only())
        self.new_only.toggled.connect(self._on_toggled)
        inner.addWidget(self.new_only)

        desc = QLabel(
            "開啟（預設）：歷史紀錄、處置統計、統計圖表、詳細條件的歷史處置，都只顯示新制資料；"
            "處置預測總覽與監控儀表板不能切換到 2026/08/10 以前的日期。\n"
            "關閉：顯示全部資料，包含舊制（處置 10 天、5 分／20 分撮合）。")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #9a9a9a; font-size: 13px; border: none; padding-left: 58px;")
        inner.addWidget(desc)

        self.state = QLabel()
        self.state.setStyleSheet("color: #4da6ff; font-size: 13px; border: none; padding-left: 58px;")
        inner.addWidget(self.state)
        self._show_state(self.new_only.isChecked())

        layout.addWidget(box)
        layout.addStretch()

    def _show_state(self, on):
        self.state.setText("目前：只顯示新制" if on else "目前：顯示全部資料（含舊制）")

    def _on_toggled(self, checked):
        try:
            app_settings.set_new_regime_only(checked)
        except Exception as e:
            self.state.setText(f"儲存失敗：{e}")
            return
        self._show_state(checked)
