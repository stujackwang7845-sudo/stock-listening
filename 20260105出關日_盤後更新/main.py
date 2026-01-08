
import sys
import qdarktheme
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from ui.main_window import MainWindow

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
    
    # 套用深色主題 (作為基礎)
    qdarktheme.setup_theme("dark", custom_colors={"primary": "#60A5FA"})
    
    # 載入自訂 QSS
    try:
        with open("ui/styles.qss", "r", encoding="utf-8") as f:
            app.setStyleSheet(app.styleSheet() + f.read())
    except Exception as e:
        print(f"Failed to load QSS: {e}")
    
    # 建立主視窗
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
