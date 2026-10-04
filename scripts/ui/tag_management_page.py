
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QTextEdit, QLabel, QFrame, QColorDialog,
    QComboBox, QMessageBox, QSplitter, QGroupBox, QFormLayout, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
from core.tag_manager import TagManager
from core.history_manager import HistoryManager

class TagManagementPage(QWidget):
    """Tag 管理頁面 - 提供 Tag 的建立、編輯、刪除功能"""
    
    tag_updated = pyqtSignal()  # Tag 更新訊號（通知其他頁面重新載入）
    
    def __init__(self, tag_manager: TagManager, history_manager: HistoryManager):
        super().__init__()
        self.tag_manager = tag_manager
        self.history_manager = history_manager
        self.current_tag_id = None  # 當前選中的 Tag ID
        self.init_ui()
        self.load_tags()
    
    def init_ui(self):
        """初始化 UI"""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        
        self.main_scroll = QScrollArea()
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.main_widget_content = QWidget()
        main_layout = QHBoxLayout(self.main_widget_content)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        self.main_scroll.setWidget(self.main_widget_content)
        outer_layout.addWidget(self.main_scroll)
        
        # 使用 QSplitter 分割左右區域
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # === 左側：Tag 列表區域 ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        # 標題與新增按鈕
        header_layout = QHBoxLayout()
        title_label = QLabel("標籤列表")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #4da6ff;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        self.btn_new = QPushButton("➕ 新增標籤")
        self.btn_new.setStyleSheet("""
            QPushButton {
                background-color: #4da6ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #3d8fe0; }
        """)
        self.btn_new.clicked.connect(self.create_new_tag)
        header_layout.addWidget(self.btn_new)
        
        left_layout.addLayout(header_layout)
        
        # Tag 列表
        self.tag_list = QListWidget()
        self.tag_list.setStyleSheet("""
            QListWidget {
                background-color: #1E1E1E;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 5px;
                font-size: 14px;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #333;
                color: #E0E0E0;
            }
            QListWidget::item:selected {
                background-color: #4da6ff;
                color: white;
            }
            QListWidget::item:hover {
                background-color: #333;
            }
        """)
        self.tag_list.itemClicked.connect(self.on_tag_selected)
        left_layout.addWidget(self.tag_list)
        
        splitter.addWidget(left_panel)
        
        # === 右側：Tag 編輯區域 ===
        right_panel = QFrame()
        right_panel.setStyleSheet("""
            QFrame {
                background-color: #1E1E1E;
                border: 2px solid #4da6ff;
                border-radius: 8px;
            }
        """)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(15, 15, 15, 15)
        
        # 編輯區標題
        self.edit_title = QLabel("選擇或新增標籤")
        self.edit_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #4da6ff; margin-bottom: 10px;")
        right_layout.addWidget(self.edit_title)
        
        # 表單區域
        form_group = QGroupBox("標籤設定")
        form_group.setStyleSheet("""
            QGroupBox {
                color: #CCC;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        form_layout = QFormLayout(form_group)
        
        # 名稱輸入
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("輸入標籤名稱...")
        self.input_name.setStyleSheet("""
            QLineEdit {
                background-color: #252526;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 8px;
                font-size: 14px;
            }
            QLineEdit:focus { border: 1px solid #4da6ff; }
        """)
        form_layout.addRow("名稱:", self.input_name)
        
        # 顏色選擇
        color_layout = QHBoxLayout()
        self.color_preview = QLabel("     ")
        self.color_preview.setStyleSheet("""
            QLabel {
                background-color: #FF0000;
                border: 2px solid #555;
                border-radius: 4px;
                min-width: 40px;
                min-height: 30px;
            }
        """)
        self.current_color = "#FF0000"  # 預設紅色
        
        self.btn_choose_color = QPushButton("選擇顏色")
        self.btn_choose_color.setStyleSheet("""
            QPushButton {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #444; }
        """)
        self.btn_choose_color.clicked.connect(self.choose_color)
        
        color_layout.addWidget(self.color_preview)
        color_layout.addWidget(self.btn_choose_color)
        color_layout.addStretch()
        form_layout.addRow("顏色:", color_layout)
        
        # 圖案選擇
        self.icon_combo = QComboBox()
        self.icon_combo.setStyleSheet("""
            QComboBox {
                background-color: #252526;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px;
                font-size: 14px;
            }
            QComboBox:focus { border: 1px solid #4da6ff; }
            QComboBox::drop-down {
                border: none;
                background: #333;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid white;
            }
        """)
        
        # 載入圖案選項
        available_icons = self.tag_manager.get_available_icons()
        for icon_key, icon_symbol in available_icons.items():
            self.icon_combo.addItem(f"{icon_symbol}  {icon_key}", icon_key)
        
        form_layout.addRow("圖案:", self.icon_combo)
        
        # 敘述輸入
        self.input_description = QTextEdit()
        self.input_description.setPlaceholderText("輸入標籤敘述（可選）...")
        self.input_description.setMaximumHeight(100)
        self.input_description.setStyleSheet("""
            QTextEdit {
                background-color: #252526;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 8px;
                font-size: 14px;
            }
            QTextEdit:focus { border: 1px solid #4da6ff; }
        """)
        form_layout.addRow("敘述:", self.input_description)
        
        right_layout.addWidget(form_group)
        
        # 使用統計（顯示 Tag 被使用的次數）
        self.usage_label = QLabel()
        self.usage_label.setStyleSheet("color: #888; font-size: 12px; margin-top: 10px;")
        right_layout.addWidget(self.usage_label)
        
        # 按鈕區
        button_layout = QHBoxLayout()
        
        self.btn_save = QPushButton("💾 儲存")
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled {
                background-color: #555;
                color: #888;
            }
        """)
        self.btn_save.clicked.connect(self.save_tag)
        
        self.btn_delete = QPushButton("🗑️ 刪除")
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #da190b; }
            QPushButton:disabled {
                background-color: #555;
                color: #888;
            }
        """)
        self.btn_delete.clicked.connect(self.delete_tag)
        self.btn_delete.setEnabled(False)  # 初始狀態禁用
        
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #444; }
        """)
        self.btn_cancel.clicked.connect(self.cancel_edit)
        
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_delete)
        button_layout.addStretch()
        button_layout.addWidget(self.btn_cancel)
        
        right_layout.addLayout(button_layout)
        right_layout.addStretch()
        
        splitter.addWidget(right_panel)
        
        # 設定分割比例（左:右 = 1:2）
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        
        main_layout.addWidget(splitter)
    
    def load_tags(self):
        """載入所有 Tag 到列表"""
        self.tag_list.clear()
        tags = self.tag_manager.get_tag_list()
        
        for tag in tags:
            tag_id = tag["id"]
            name = tag["name"]
            color = tag["color"]
            icon_key = tag["icon"]
            icon_symbol = self.tag_manager.get_icon_symbol(icon_key)
            
            # 建立列表項目
            item = QListWidgetItem(f"{icon_symbol}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, tag_id)
            
            # 設定顏色（使用前景色顯示圖示顏色）
            item.setForeground(QBrush(QColor(color)))
            
            # 設定字體
            font = QFont()
            font.setPointSize(12)
            font.setBold(True)
            item.setFont(font)
            
            self.tag_list.addItem(item)
    
    def on_tag_selected(self, item: QListWidgetItem):
        """選中 Tag 時載入其資訊到編輯區"""
        tag_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_tag_id = tag_id
        
        tag_info = self.tag_manager.get_tag(tag_id)
        if not tag_info:
            return
        
        # 載入資料到表單
        self.input_name.setText(tag_info["name"])
        self.current_color = tag_info["color"]
        self.color_preview.setStyleSheet(f"""
            QLabel {{
                background-color: {self.current_color};
                border: 2px solid #555;
                border-radius: 4px;
                min-width: 40px;
                min-height: 30px;
            }}
        """)
        
        # 設定圖案選擇器
        icon_key = tag_info["icon"]
        index = self.icon_combo.findData(icon_key)
        if index >= 0:
            self.icon_combo.setCurrentIndex(index)
        
        self.input_description.setPlainText(tag_info.get("description", ""))
        
        # 顯示使用統計
        usage_count = self.history_manager.get_tag_usage_count(tag_id)
        self.usage_label.setText(f"📊 使用次數: {usage_count}")
        
        # 更新 UI 狀態
        self.edit_title.setText(f"編輯標籤: {tag_info['name']}")
        self.btn_delete.setEnabled(True)
    
    def create_new_tag(self):
        """新增 Tag 模式"""
        self.current_tag_id = None
        self.input_name.clear()
        self.input_description.clear()
        self.current_color = "#FF0000"
        self.color_preview.setStyleSheet(f"""
            QLabel {{
                background-color: {self.current_color};
                border: 2px solid #555;
                border-radius: 4px;
                min-width: 40px;
                min-height: 30px;
            }}
        """)
        self.icon_combo.setCurrentIndex(0)
        self.usage_label.clear()
        self.edit_title.setText("新增標籤")
        self.btn_delete.setEnabled(False)
        self.tag_list.clearSelection()
    
    def choose_color(self):
        """開啟顏色選擇器"""
        color = QColorDialog.getColor(QColor(self.current_color), self, "選擇標籤顏色")
        if color.isValid():
            self.current_color = color.name()
            self.color_preview.setStyleSheet(f"""
                QLabel {{
                    background-color: {self.current_color};
                    border: 2px solid #555;
                    border-radius: 4px;
                    min-width: 40px;
                    min-height: 30px;
                }}
            """)
    
    def save_tag(self):
        """儲存 Tag"""
        name = self.input_name.text().strip()
        if not name:
            QMessageBox.warning(self, "輸入錯誤", "請輸入標籤名稱！")
            return
        
        icon_key = self.icon_combo.currentData()
        description = self.input_description.toPlainText().strip()
        
        try:
            if self.current_tag_id:
                # 更新現有 Tag
                self.tag_manager.update_tag(
                    self.current_tag_id,
                    name=name,
                    color=self.current_color,
                    icon=icon_key,
                    description=description
                )
                QMessageBox.information(self, "成功", f"標籤 '{name}' 已更新！")
            else:
                # 建立新 Tag
                tag_id = self.tag_manager.create_tag(
                    name=name,
                    color=self.current_color,
                    icon=icon_key,
                    description=description
                )
                QMessageBox.information(self, "成功", f"標籤 '{name}' 已建立！")
                self.current_tag_id = tag_id
            
            # 重新載入列表
            self.load_tags()
            
            # 發送更新訊號
            self.tag_updated.emit()
            
        except Exception as e:
            QMessageBox.critical(self, "錯誤", f"儲存失敗: {e}")
    
    def delete_tag(self):
        """刪除 Tag"""
        if not self.current_tag_id:
            return
        
        tag_info = self.tag_manager.get_tag(self.current_tag_id)
        if not tag_info:
            return
        
        # 確認刪除
        usage_count = self.history_manager.get_tag_usage_count(self.current_tag_id)
        msg = f"確定要刪除標籤 '{tag_info['name']}' 嗎？"
        if usage_count > 0:
            msg += f"\n\n⚠️ 此標籤目前被使用在 {usage_count} 筆紀錄中，刪除後將從所有紀錄中移除。"
        
        reply = QMessageBox.question(
            self, "確認刪除", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # 從所有紀錄中移除此 Tag
                self.history_manager.remove_tag_from_all_records(self.current_tag_id)
                
                # 刪除 Tag
                self.tag_manager.delete_tag(self.current_tag_id)
                
                QMessageBox.information(self, "成功", f"標籤 '{tag_info['name']}' 已刪除！")
                
                # 重新載入列表
                self.load_tags()
                
                # 清空編輯區
                self.create_new_tag()
                
                # 發送更新訊號
                self.tag_updated.emit()
                
            except Exception as e:
                QMessageBox.critical(self, "錯誤", f"刪除失敗: {e}")
    
    def cancel_edit(self):
        """取消編輯"""
        self.create_new_tag()
