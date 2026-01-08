
import sys
import qdarktheme
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

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
    # [Start Splash Screen]
    from PyQt6.QtWidgets import QSplashScreen
    from PyQt6.QtGui import QPixmap, QFont, QColor, QPainter
    from PyQt6.QtCore import Qt, QRect
    
    # 1. Load Original Image (No Scaling)
    original_pix = QPixmap("prison.png")
    
    # 2. Create Canvas (Image Left + Text Right)
    # Estimate Text Width
    padding_x = 30
    text_area_w = 320
    w = original_pix.width() + text_area_w + padding_x
    h = max(original_pix.height(), 120) 
    
    canvas = QPixmap(w, h)
    canvas.fill(QColor("#FFFFFF")) # White Background
    
    painter = QPainter(canvas)
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
    
    # 2. Status
    font_status = QFont()
    font_status.setPixelSize(16) 
    painter.setFont(font_status)
    painter.setPen(QColor("#666666"))
    painter.drawText(text_x, 85, "更新資料中... 請稍候")
    
    # 3. Author (Bottom Right)
    font_author = QFont()
    font_author.setPixelSize(12)
    painter.setFont(font_author)
    painter.setPen(QColor("#999999"))
    
    author_rect = QRect(w - 200, h - 30, 180, 20)
    painter.drawText(author_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "作者 : Rainbowsperm")
    
    painter.end()
        
    splash = QSplashScreen(canvas, Qt.WindowType.WindowStaysOnTopHint)
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
    
    # 建立主視窗 (Lazy Import to speed up splash display)
    from ui.main_window import MainWindow
    # [Optimization] Don't auto-start dashboard. Let Splash manage it.
    window = MainWindow(auto_start=False) 
    
    # Callback: Update Splash Message
    def update_splash(msg):
        # Update text on splash
        splash.showMessage(f"{msg}", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, QColor("#333333"))
        
    # Callback: Finish Splash and Show Window
    def on_loaded():
        window.show()
        splash.finish(window)
    
    # Start Preloading (Async)
    # The app.exec() below will keep the event loop running for the thread.
    window.preload_data(update_splash, on_loaded)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
