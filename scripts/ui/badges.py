"""
畫面共用的小圖示(2026-10-19)。

clause13_badge_html()：第十三款(當日沖銷占比過高)圖示——橘色實心圓＋粗體黑字 13。
14px 的文字 ⑬ 加粗會糊成一團(使用者反映)，改用程式畫的 1x/2x PNG(高解析度螢幕 Qt 自動用 @2x)。
儀表板日期欄、詳細條件「近期注意條款」共用。產生失敗就退回文字 ⑬。
"""
import os
import tempfile

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter

CLAUSE13_TEXT = "<span style='color: #FFA500; font-weight: bold;'>⑬</span>"
_cache = None


def clause13_badge_html(size=18):
    global _cache
    if _cache is not None:
        return _cache
    try:
        folder = os.path.join(tempfile.gettempdir(), "dispo_ui")
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, "clause13.png")
        for px, path in ((size, base), (size * 2, base.replace(".png", "@2x.png"))):
            img = QImage(px, px, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FF9800"))
            p.drawEllipse(QRectF(0.5, 0.5, px - 1, px - 1))
            f = QFont("Arial")
            f.setBold(True)
            f.setPixelSize(int(px * 0.56))
            p.setFont(f)
            p.setPen(QColor("#000000"))
            p.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, "13")
            p.end()
            img.save(path)
        _cache = f"<img src='{base.replace(chr(92), '/')}' width='{size}' height='{size}'>"
    except Exception as e:
        print(f"[badges] 第十三款圖示產生失敗，改用文字：{e}")
        _cache = CLAUSE13_TEXT
    return _cache
