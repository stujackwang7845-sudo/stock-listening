
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QTabWidget,
                             QToolBar, QLabel, QStatusBar, QStyle, QApplication)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
import os


class SharesRefreshWorker(QThread):
    """
    每次開啟軟體都整批重抓全市場「已發行股數」，寫入 stock_info 快取。

    [Fix 2026-08-23] 舊的「逐檔查 FinMind 季報股本、90天過期才重抓」機制曾
    讓 8033、4971 等股票在增資後股本數字卡在舊快取，算出來的周轉率張數門檻
    偏低。改成每次啟動都用 TWSE/TPEX 官方基本資料 API 整批刷新，兩次 API
    呼叫涵蓋全市場，跟原本逐檔查詢比並不會拖慢啟動速度。失敗就跳過，沿用
    快取裡舊資料，不影響應用程式啟動。
    """
    finished_ok = pyqtSignal(int)  # 更新到的檔數

    def run(self):
        try:
            from core.fetcher import StockFetcher
            fetcher = StockFetcher()
            shares_map = fetcher.fetch_all_shares_outstanding()
            if shares_map:
                fetcher.cache.save_stock_info_bulk(shares_map)
            self.finished_ok.emit(len(shares_map))
        except Exception as e:
            print(f"[SharesRefreshWorker] 股數整批更新失敗: {e}")
            self.finished_ok.emit(0)


class MainWindow(QMainWindow):
    def __init__(self, auto_start=True):
        super().__init__()
        
        self.auto_start = auto_start
        self.setWindowTitle("台股注意/處置股監控系統")
        
        # Absolute Path for Icon
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        icon_path = os.path.join(root_dir, "data", "prison.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
            
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
        from core.history_manager import HistoryManager
        from core.tag_manager import TagManager
        from ui.history_page import HistoryPage
        from ui.tag_management_page import TagManagementPage
        from ui.disposal_stats_page import DisposalStatsPage
        from ui.disposal_stats_overview import DisposalStatsOverviewWidget
        from ui.forecast_page import ForecastPage
        
        # Shared Manager
        self.history_manager = HistoryManager()
        self.tag_manager = TagManager()
        
        self.tab1 = Dashboard(self.history_manager, auto_start=self.auto_start)
        self.tab1.status_message_updated.connect(lambda msg: self.statusBar().showMessage(msg))
        self.tab2 = DetailsTab()
        self.tab3 = HistoryPage(self.history_manager, self.tag_manager)
        self.tab_tag_mgmt = TagManagementPage(self.tag_manager, self.history_manager)
        self.tab_disposal_stats = DisposalStatsPage(self.history_manager)
        self.tab_disposal_stats_chart = DisposalStatsOverviewWidget(auto_refresh=True)
        self.tab_forecast = ForecastPage()
        self.tab4 = QWidget()
        
        # Connect Signal (Use grid_table for Dashboard)
        # Note: Dashboard mockup might load async, but widget exists immediately
        self.tab1.grid_table.doubleClicked.connect(self.on_dashboard_item_dblclick)
        
        self.tabs.addTab(self.tab_forecast, "處置預測總覽")
        self.tabs.addTab(self.tab1, "監控儀表板")
        self.tabs.addTab(self.tab2, "詳細條件")
        self.tabs.addTab(self.tab3, "歷史紀錄")
        self.tabs.addTab(self.tab_tag_mgmt, "Tag管理")  # 新增 Tag 管理 Tab
        self.tabs.addTab(self.tab_disposal_stats, "處置統計")  # 新增處置統計 Tab
        self.tabs.addTab(self.tab_disposal_stats_chart, "統計圖表")
        self.tabs.addTab(self.tab4, "系統設定")
        
        # Connect Tag update signal to refresh history page
        self.tab_tag_mgmt.tag_updated.connect(self.tab3.load_items)
        
        # Setup placeholders for empty tabs (Only for tab4 now)
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
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        
        # Get Standard Icons
        style = QApplication.style() 
        
        # 重新整理 Action (完整更新)
        reload_icon = style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        if reload_icon.isNull():
             print("DEBUG: Reload Icon is Null!")
             
        refresh_action = QAction(reload_icon, "重新整理", self)
        refresh_action.setStatusTip("完整更新：下載今天+過去14天的資料")
        # refresh_action.setToolTip("完整更新\n下載範圍：今天 + 過去14天\n用途：補齊歷史資料、修正缺漏\n耗時：約 10-20 秒") # Removed as per user request to use Flyover Button instead
        refresh_action.triggered.connect(self.on_refresh)
        toolbar.addAction(refresh_action)
        
        # 快速更新 Action
        quick_icon = style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        quick_action = QAction(quick_icon, "快速更新", self)
        quick_action.setStatusTip("快速更新：只下載今天的資料（1天）")
        # quick_action.setToolTip("快速更新\n下載範圍：今天 (最新交易日)\n用途：盤後快速查看最新聽牌/處置狀態\n耗時：約 1-3 秒") # Removed
        quick_action.triggered.connect(self.on_quick_update)
        toolbar.addAction(quick_action)
        
        toolbar.addSeparator()
        
        # 匯出 Action
        save_icon = style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        export_action = QAction(save_icon, "匯出報表", self)
        export_action.setStatusTip("匯出目前資料為 Excel/CSV")
        toolbar.addAction(export_action)
        
        # Info Action
        info_icon = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        info_action = QAction(info_icon, "說明", self)
        info_action.setStatusTip("顯示更新功能說明")
        info_action.triggered.connect(self.show_update_help)
        toolbar.addAction(info_action)

    def on_refresh(self):
        # Trigger real refresh (完整更新)
        if hasattr(self.tab1, "start_worker"):
             self.tab1.start_worker(force_refresh=True)
        self.statusBar().showMessage("正在更新資料...", 2000)
    
    def on_quick_update(self):
        # Trigger quick update (快速更新)
        if hasattr(self.tab1, "start_quick_update"):
             self.tab1.start_quick_update()
        self.statusBar().showMessage("快速更新中...", 2000)

    def show_update_help(self):
        from PyQt6.QtWidgets import QMessageBox
        help_text = (
            "<h3>更新功能說明</h3>"
            "<ul>"
            "<li><b>🔄 重新整理 (工具列)</b>：<br>"
            "下載「今天 + 過去 14 天」的完整資料。<br>"
            "適用於：補齊漏掉的歷史資料、或修正資料不一致。<br>"
            "耗時：約 15-20 秒。"
            "</li><br>"
            
            "<li><b>▶ 快速更新 (工具列)</b>：<br>"
            "僅下載「今天」的即時/盤後資料。<br>"
            "適用於：盤中或盤後想快速看今天的聽牌/處置狀態。<br>"
            "耗時：約 1-3 秒。"
            "</li><br>"
            
            "<li><b>🟠 橘色重載按鈕 (日期旁)</b>：<br>"
            "僅重新下載「當前顯示日期」的完整資料。<br>"
            "適用於：只想更新那一天的資料 (例如重抓今天的處置公告)。<br>"
            "</li>"
            "</ul>"
        )
        QMessageBox.information(self, "更新功能說明", help_text)

    def preload_data(self, status_callback, done_callback):
        """
        Triggers Dashboard data loading for Splash Screen, then Forecast loading.
        """
        self.tab1.status_message_updated.connect(status_callback)
        self.tab_forecast.status_message_updated.connect(status_callback)
        
        # 智慧判斷是否強制從網路更新（晚上 7-11 點，且今天是交易日）
        from datetime import datetime
        from core.utils import DateUtils
        now = datetime.now()
        is_night_time = (19 <= now.hour < 23)
        is_today_trading = DateUtils.is_trading_day(now)
        
        # [Fix] 恢復智慧判斷，避免每次開啟程式都下載14天歷史資料導致卡頓
        force_refresh_flag = is_night_time and is_today_trading
        
        if force_refresh_flag:
            status_callback("正在執行夜間自動完整更新(14天)...")
        else:
            status_callback("正在載入快取與今日最新資料...")

        def on_forecast_done():
            self.tab_forecast.status_message_updated.disconnect(status_callback)
            self.tab_forecast.initial_load_finished.disconnect(on_forecast_done)

            # [Fix 2026-08-30] 儀表板+處置預測都載入完成後，順便自動刷新「歷史紀錄」
            # 「處置統計」「統計圖表」這三個分頁——原本這三個都要使用者每天手動點
            # 更新按鈕才會有最新資料，跟儀表板/處置預測開啟軟體就自動更新的體驗不
            # 一致。不阻塞 done_callback()(不讓使用者多等)，在背景各自跑完就好。
            #
            # [Fix 2026-09-01] 移除「歷史紀錄」的這段自動刷新：load_items() 會一次
            # 同步建立 50*3=150 個分時圖 widget(matplotlib canvas)，剛好卡在開軟體
            # 最忙的時間點(跟處置統計接下來要跑的大量背景運算同時發生)，實測會讓
            # 整個視窗直接崩潰關閉。改回倚賴 HistoryPage 內建的 showEvent 延遲載入
            # (使用者實際切到這個分頁時才建立圖表)——底層的 history_manager 資料
            # 本身已經由 dashboard 的 HistoryWorker 同步過了，只是畫面不會搶在使用者
            # 切過去之前就先渲染，避免跟其他背景工作搶資源導致崩潰。

            try:
                # 處置統計：抓最新處置公告寫入 disposal_history.db、補齊漲跌幅計算，
                # 完成後才觸發「統計圖表」重新整理(它讀的是同一份 disposal_history.db，
                # 錯開時間點跑，避免兩邊同時讀寫 SQLite 互相卡到)。
                # [Fix 2026-09-01] 這段要跑 60-90 秒，之前只更新處置統計分頁自己的
                # 狀態列，使用者若還沒切過去那個分頁就看不到，容易誤以為沒在更新。
                # 讓同一份進度文字也接到開啟軟體時的主畫面狀態列。
                self.tab_disposal_stats.auto_refresh_on_startup(
                    on_complete=lambda: self.tab_disposal_stats_chart.refresh(force_refresh=True),
                    progress_callback=status_callback
                )
            except Exception as e:
                print(f"[preload_data] 處置統計自動刷新失敗: {e}")

            done_callback()

        def start_forecast():
            status_callback("正在準備處置預測總覽...")
            self.tab_forecast.initial_load_finished.connect(on_forecast_done)
            self.tab_forecast.load_data()
            self.tab_forecast.is_loaded = True

        def on_dashboard_done():
            self.tab1.status_message_updated.disconnect(status_callback)
            self.tab1.initial_load_finished.disconnect(on_dashboard_done)

            # [Fix 2026-09-01] 聽牌(官方)名單原本只在晚上7-11點、且當天還沒有
            # agg_cache 時才會自動更新，白天開軟體遇到已有快取就會整段跳過，
            # 導致聽牌/一進聽表格顯示空白卻要手動按更新才會有資料。這裡不管
            # 快取或時段，一律先跑一次輕量的聽牌名單更新，完成後才載入處置
            # 預測總覽，確保 forecast_page 讀到的是當天最新聽牌名單。
            #
            # [Fix 2026-09-03] attention_clauses(累積注意次數，min_needed 計算的
            # 依據)原本只能靠「下載注意條款」按鈕手動觸發，實測發現整整兩週沒有
            # 新資料，導致聽牌/一進聽的累積次數計算嚴重低估。這裡排在聽牌名單
            # 更新之前，確保兩邊都刷新完成才載入處置預測總覽——forecast_page 讀到
            # 的 attention_clauses 才會是最新的，min_needed 才會正確。
            def start_forecast_after_clauses():
                try:
                    self.tab1.auto_refresh_listening_on_startup(on_complete=start_forecast)
                except Exception as e:
                    print(f"[preload_data] 聽牌自動刷新失敗: {e}")
                    start_forecast()

            try:
                self.tab1.auto_refresh_clauses_on_startup(on_complete=start_forecast_after_clauses)
            except Exception as e:
                print(f"[preload_data] 注意條款自動刷新失敗: {e}")
                start_forecast_after_clauses()

        self.tab1.initial_load_finished.connect(on_dashboard_done)

        # [Fix 2026-08-23] 先整批刷新全市場股數快取，確保每次開啟軟體時
        # 周轉率相關的張數門檻都是用最新股本算的，再啟動儀表板數據加載。
        def start_dashboard_load(updated_count):
            print(f"[preload_data] 股數快取已更新 {updated_count} 檔")
            self._shares_refresh_worker.finished_ok.disconnect(start_dashboard_load)
            self.tab1.start_worker(force_refresh=force_refresh_flag)

        status_callback("正在更新全市場股數資料...")
        self._shares_refresh_worker = SharesRefreshWorker()
        self._shares_refresh_worker.finished_ok.connect(start_dashboard_load)
        self._shares_refresh_worker.start()
