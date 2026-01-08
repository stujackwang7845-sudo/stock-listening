
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QTabWidget, 
                             QToolBar, QLabel, QStatusBar)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QIcon

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("台股注意/處置股監控系統")
        self.resize(1200, 800)
        
        # 初始化 UI
        self.init_ui()
        
    def init_ui(self):
        # 建立中央 Widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # 建立 TabWidget
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True) # 現代化風格
        
        # 建立四個 Tab 頁面
        from ui.dashboard import Dashboard
        from ui.details import DetailsTab
        
        self.tab1 = Dashboard()
        self.tab1.status_message_updated.connect(lambda msg: self.statusBar().showMessage(msg))
        self.tab2 = DetailsTab()
        self.tab3 = QWidget()
        self.tab4 = QWidget()
        
        # Connect Signal (Use grid_table for Dashboard)
        # Note: Dashboard mockup might load async, but widget exists immediately
        self.tab1.grid_table.doubleClicked.connect(self.on_dashboard_item_dblclick)
        
        self.tabs.addTab(self.tab1, "監控儀表板")
        self.tabs.addTab(self.tab2, "詳細條件")
        self.tabs.addTab(self.tab3, "歷史紀錄")
        self.tabs.addTab(self.tab4, "系統設定")
        
        # Setup placeholders for empty tabs
        self.setup_tab_placeholder(self.tab3, "歷史紀錄 (History)")
        self.setup_tab_placeholder(self.tab4, "系統設定 (Settings)")
        
        main_layout.addWidget(self.tabs)
        
        # 建立 Toolbar
        self.create_toolbar()
        
        # 建立 StatusBar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("系統就緒")

    def contextMenuEvent(self, event):
        super().contextMenuEvent(event)

    def on_dashboard_item_dblclick(self, index):
        # Get data from row
        row = index.row()
        
        # Layout Columns: 0=Code, 1=Name, 2=Source
        if row < 0: return

        try:
            code_item = self.tab1.grid_table.item(row, 0)
            name_item = self.tab1.grid_table.item(row, 1)
            source_item = self.tab1.grid_table.item(row, 2)
            
            code = code_item.text() if code_item else "-"
            name = name_item.text() if name_item else "-"
            source = source_item.text() if source_item else "-"
            
            data = {
                "status": "注意股", 
                "code": code,
                "name": name,
                "reason": "請查看矩陣日期詳細內容",
                "source": source
            }
            
            self.tab2.update_content(data)
            self.tabs.setCurrentIndex(1) # Switch to Details Tab
        except Exception as e:
            print(f"Error on double click: {e}")

    def setup_tab_placeholder(self, tab, text):
        layout = QVBoxLayout(tab)
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 24px; color: #888;")
        layout.addWidget(label)

    def create_toolbar(self):
        toolbar = QToolBar("主要工具列")
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # 重新整理 Action
        refresh_action = QAction("重新整理", self)
        refresh_action.setStatusTip("更新股票資料")
        refresh_action.triggered.connect(self.on_refresh)
        toolbar.addAction(refresh_action)
        
        toolbar.addSeparator()
        
        # 匯出 Action
        export_action = QAction("匯出報表", self)
        export_action.setStatusTip("匯出目前資料為 Excel/CSV")
        toolbar.addAction(export_action)

    def on_refresh(self):
        # call dashboard mock load
        if hasattr(self.tab1, "load_layout_mock"):
             self.tab1.load_layout_mock()
        self.statusBar().showMessage("資料已更新 (Mock)", 2000)
