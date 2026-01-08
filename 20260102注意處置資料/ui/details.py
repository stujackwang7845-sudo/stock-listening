
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextBrowser, QFormLayout, QFrame
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

class DetailsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Title
        self.title_label = QLabel("請從儀表板選擇一檔股票查看詳細資訊")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #E0E0E0;")
        layout.addWidget(self.title_label)
        
        # Detail Content
        self.content_frame = QFrame()
        self.content_frame.setVisible(False)
        form_layout = QFormLayout(self.content_frame)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.lb_code = QLabel("-")
        self.lb_name = QLabel("-")
        self.lb_status = QLabel("-")
        self.lb_source = QLabel("-")
        
        form_layout.addRow("股票代號:", self.lb_code)
        form_layout.addRow("股票名稱:", self.lb_name)
        form_layout.addRow("目前狀態:", self.lb_status)
        form_layout.addRow("資料來源:", self.lb_source)
        
        layout.addWidget(self.content_frame)
        
        # Reason Text
        layout.addWidget(QLabel("觸發/處置原因:"))
        self.txt_reason = QTextBrowser()
        self.txt_reason.setStyleSheet("background-color: #2D2D2D; border: 1px solid #444; border-radius: 4px; padding: 10px;")
        layout.addWidget(self.txt_reason)
        
    def update_content(self, data):
        """
        data: dict containing 'code', 'name', 'status', 'source', 'reason', etc.
        """
        self.title_label.setText(f"{data['name']} ({data['code']}) 詳細資訊")
        
        self.lb_code.setText(data['code'])
        self.lb_name.setText(data['name'])
        self.lb_status.setText(data['status'])
        
        # Set status color
        if data['status'] == '處置股':
            self.lb_status.setStyleSheet("color: #FF4444; font-weight: bold;")
        elif data['status'] == '注意股':
            self.lb_status.setStyleSheet("color: #FFCC00; font-weight: bold;")
        else:
            self.lb_status.setStyleSheet("color: #E0E0E0;")
            
        self.lb_source.setText(data['source'])
        self.txt_reason.setPlainText(data['reason'])
        
        self.content_frame.setVisible(True)
