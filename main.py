import sys
import qdarktheme
from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QIcon, QPixmap, QFont, QColor, QPainter
from PyQt6.QtCore import Qt, QRect, QObject, pyqtSignal

class SplashUpdater(QObject):
    update_signal = pyqtSignal(str)

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
    
    # Set Icon
    app.setWindowIcon(QIcon("prison.png"))
    
    # [Start Splash Screen]
    # 1. Load Original Image (No Scaling)
    original_pix = QPixmap("prison.png")
    
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
        with open("ui/styles.qss", "r", encoding="utf-8") as f:
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

    # Callback: Finish Splash and Show Window
    def on_loaded():
        window.show()
        splash.finish(window)
    
    # [Threading Fix] Use Signal to force update_splash on Main Thread
    updater = SplashUpdater()
    updater.update_signal.connect(update_splash)

    # Start Preloading (Async)
    window.preload_data(updater.update_signal.emit, on_loaded)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
