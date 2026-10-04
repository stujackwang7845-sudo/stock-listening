import re

with open(r'e:\Vibe Coding\Stock\處置股\scripts\ui\history_page.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 替換 __init__ 裡的 matplotlib 初始代碼
init_pattern = r"""\s*self\.figure = Figure\(figsize=\(4, 2\.5\), dpi=80\)
\s*self\.canvas = FigureCanvasQTAgg\(self\.figure\)
\s*self\.layout\.addWidget\(self\.canvas\)
\s*self\.token = token
\s*self\.stock_id = stock_id
\s*self\.stock_name = stock_name
\s*self\.date_str = date_str
\s*self\.title_suffix = title_suffix
\s*self\.is_no_cache = False
\s*# Show loading text
\s*self\.text_ax = self\.figure\.add_subplot\(111\)
\s*if self\.strict_local:
\s*self\.text_ax\.text\(0\.5, 0\.5, "Loading DB\.\.\.", ha='center', va='center', fontsize=9, color='gray'\)
\s*else:
\s*self\.text_ax\.text\(0\.5, 0\.5, "Loading\.\.\.", ha='center', va='center', fontsize=9, color='gray'\)
\s*self\.text_ax\.axis\('off'\)
\s*self\.canvas\.draw\(\)"""

replacement = """        self.figure = None
        self.canvas = None
        
        self.token = token
        self.stock_id = stock_id
        self.stock_name = stock_name
        self.date_str = date_str
        self.title_suffix = title_suffix
        self.is_no_cache = False
        
        # Show loading text using QLabel instead of Matplotlib
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: gray; font-size: 12px;")
        if self.strict_local:
            self.status_label.setText("Loading DB...")
        else:
            self.status_label.setText("Loading...")
        self.layout.addWidget(self.status_label)"""

content = re.sub(init_pattern, replacement, content)

# 加入 _init_canvas
init_canvas_code = """
    def _init_canvas(self):
        if self.figure is None:
            self.figure = Figure(figsize=(4, 2.5), dpi=80)
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.layout.addWidget(self.canvas)
            self.status_label.hide()
        else:
            self.status_label.hide()
            self.canvas.show()
            self.figure.clear()
"""

content = content.replace("    def contextMenuEvent(self, event):", init_canvas_code + "\n    def contextMenuEvent(self, event):")

# 修改 contextMenuEvent
context_menu_pattern = r"""    def contextMenuEvent\(self, event\):
\s*menu = QMenu\(self\)
\s*reload_action = menu\.addAction\("重新下載資料\(Reload\)"\)
\s*action = menu\.exec\(event\.globalPos\(\)\)
\s*if action == reload_action:
\s*self\.text_ax\.clear\(\)
\s*self\.text_ax\.text\(0\.5, 0\.5, "Reloading\.\.\.", ha='center', va='center', fontsize=9, color='gray'\)
\s*self\.canvas\.draw\(\)
\s*self\._fetch_data\(force_refresh=True\)"""

context_menu_replacement = """    def contextMenuEvent(self, event):
        menu = QMenu(self)
        reload_action = menu.addAction("重新下載資料(Reload)")
        
        action = menu.exec(event.globalPos())
        if action == reload_action:
            if self.canvas:
                self.canvas.hide()
            self.status_label.setText("Reloading...")
            self.status_label.show()
            self._fetch_data(force_refresh=True)"""

content = re.sub(context_menu_pattern, context_menu_replacement, content)

# 替換 _fetch_data 裡所有的 self.figure.clear() 變成 self._init_canvas()
content = content.replace("self.figure.clear()", "self._init_canvas()")

# _process_next_chart 的 text_ax 修改
batch_pattern = r"""        try:
\s*chart\.text_ax\.clear\(\)
\s*chart\.text_ax\.text\(0\.5, 0\.5, "Downloading\.\.\.", ha='center', va='center', fontsize=9, color='gray'\)
\s*chart\.canvas\.draw\(\)
\s*except Exception:
\s*pass # Ignore drawing errors if any"""

batch_replacement = """        try:
            if chart.canvas:
                chart.canvas.hide()
            chart.status_label.setText("Downloading...")
            chart.status_label.show()
        except Exception:
            pass"""

content = re.sub(batch_pattern, batch_replacement, content)

with open(r'e:\Vibe Coding\Stock\處置股\scripts\ui\history_page.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patch applied to history_page.py")
