
from PyQt6.QtWidgets import QStyledItemDelegate
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen

class StatusDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw background if selected (handled typically by styles, but we can ensure transparency here)
        # if option.state & QStyle.State_Selected:
        #    painter.fillRect(option.rect, option.palette.highlight())

        status = index.data(Qt.ItemDataRole.DisplayRole)
        
        # Color Logic
        bg_color = QColor("#333333")
        text_color = QColor("#CCCCCC")
        
        if status == "處置股":
            bg_color = QColor("rgba(255, 68, 68, 0.2)") # Red accent
            text_color = QColor("#FF6666")
            border_color = QColor("#FF4444")
        elif status == "注意股":
            bg_color = QColor("rgba(255, 204, 0, 0.2)") # Yellow accent
            text_color = QColor("#FFCC00")
            border_color = QColor("#FFCC00")
        else:
            border_color = QColor("#555")

        # Draw Badge (Rounded Rect)
        rect = option.rect.adjusted(10, 6, -10, -6) # Padding
        
        path = QRectF(rect)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(path, 4, 4)
        
        # Draw Text
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, status)
        
        painter.restore()
