import sys
import os
import traceback
import datetime
import threading
import qdarktheme
from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QIcon, QPixmap, QFont, QColor, QPainter
from PyQt6.QtCore import Qt, QRect, QObject, pyqtSignal

class SplashUpdater(QObject):
    update_signal = pyqtSignal(str)

# [2026-09-28 暫時加入，排查「按網頁更新後常常程式執行失敗」問題用]
# 不論哪個執行緒(含 QThread 背景工作，如 StatsWorker/ForecastWorker)發生未捕捉例外，
# 都自動把完整 traceback 存到 crash_log_auto.txt，不用再靠使用者截圖(常常截不到真正
# 的錯誤內容，只看到終端機捲軸吃掉的畫面)。確認根因後這段可以移除。
_ORIGINAL_EXCEPTHOOK = sys.excepthook

def _log_crash(exc_type, exc_value, exc_tb, thread_name="MainThread"):
    try:
        basedir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(os.path.dirname(basedir), "crash_log_auto.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"發生時間: {datetime.datetime.now().isoformat()}\n")
            f.write(f"執行緒: {thread_name}\n")
            f.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
            f.write(f"{'='*60}\n")
    except Exception:
        pass

def _global_excepthook(exc_type, exc_value, exc_tb):
    _log_crash(exc_type, exc_value, exc_tb, "MainThread")
    _ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_tb)

sys.excepthook = _global_excepthook

_ORIGINAL_THREADING_EXCEPTHOOK = threading.excepthook

def _threading_excepthook(args):
    _log_crash(args.exc_type, args.exc_value, args.exc_traceback,
               args.thread.name if args.thread else "UnknownThread")
    _ORIGINAL_THREADING_EXCEPTHOOK(args)

threading.excepthook = _threading_excepthook

def main():
    # Windows Taskbar Icon Fix
    import ctypes
    myappid = 'vibecoding.stock.disposition.1.0' 
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    # 初始化 QApplication
    app = QApplication(sys.argv)
    
    # Get Absolute Path
    basedir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(os.path.dirname(basedir), "data", "prison.png")
    
    # Set Icon
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    else:
        print(f"Warning: Icon not found at {icon_path}")
    
    # [Start Splash Screen]
    # 1. Load Original Image (No Scaling)
    if os.path.exists(icon_path):
        original_pix = QPixmap(icon_path)
    else:
        original_pix = QPixmap(64, 64)
        original_pix.fill(Qt.GlobalColor.gray)
    
    # 2. Create Canvas (Image Left + Text Right)
    padding_x = 30
    text_area_w = 320
    w = original_pix.width() + text_area_w + padding_x
    h = max(original_pix.height(), 120) 
    
    base_canvas = QPixmap(w, h)
    base_canvas.fill(QColor("#FFFFFF")) # White Background
    
    painter = QPainter(base_canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Draw Image (Left, Vertically Centered)
    img_x = 20
    img_y = (h - original_pix.height()) // 2
    painter.drawPixmap(img_x, img_y, original_pix)
    
    # Text Start X
    text_x = original_pix.width() + img_x + 20
    
    # 1. Title
    font_title = QFont()
    font_title.setPixelSize(32) 
    font_title.setBold(True)
    painter.setFont(font_title)
    painter.setPen(QColor("#333333"))
    painter.drawText(text_x, 50, "處置預測系統")
    
    # 2. Status Placeholder - NO DRAWING HERE (Dynamic)
    
    # 3. Author (Bottom Right)
    font_author = QFont()
    font_author.setPixelSize(12)
    painter.setFont(font_author)
    painter.setPen(QColor("#999999"))
    
    author_rect = QRect(w - 200, h - 30, 180, 20)
    painter.drawText(author_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "作者 : Rainbowsperm")
    
    painter.end()
        
    splash = QSplashScreen(base_canvas, Qt.WindowType.WindowStaysOnTopHint)
    splash.show()
    
    app.processEvents()
    
    # 套用深色主題 (作為基礎)
    qdarktheme.setup_theme("dark", custom_colors={"primary": "#60A5FA"})
    
    # 載入自訂 QSS
    try:
        qss_path = os.path.join(basedir, "ui/styles.qss")
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(app.styleSheet() + f.read())
    except Exception as e:
        print(f"Failed to load QSS: {e}")
    
    # 建立主視窗
    from ui.main_window import MainWindow
    window = MainWindow(auto_start=False) 
    
    # Callback: Update Splash Message
    def update_splash(msg):
        # Update text by repainting on base canvas copy
        new_pix = base_canvas.copy()
        p = QPainter(new_pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Re-create font locally
        f = QFont()
        f.setPixelSize(16)
        p.setFont(f)
        p.setPen(QColor("#666666"))
        
        # Draw dynamic text at correct middle position
        p.drawText(int(text_x), 85, str(msg))
        p.end()
        
        splash.setPixmap(new_pix)
        splash.repaint()
        app.processEvents()
        
    # Initial Text
    update_splash("系統啟動中...")

    # Display Splash for a fixed time or until loaded
    # Since we removed auto-fetch, just wait a brief moment or show immediately
    
    # Callback: Finish Splash and Show Window
    def on_loaded():
        window.show()
        splash.finish(window)
    
    # [Threading Fix] Use Signal to force update_splash on Main Thread
    updater = SplashUpdater()
    updater.update_signal.connect(update_splash)

    # Start Preloading (Async) - Uses Cache Only (allow_fetch=False in main_window)
    window.preload_data(updater.update_signal.emit, on_loaded)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
