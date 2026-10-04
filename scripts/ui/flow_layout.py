from PyQt6.QtWidgets import QLayout, QSizePolicy
from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer

class FlowLayout(QLayout):
    """
    A layout that arranges widgets horizontally and wraps them to the next line 
    when there is insufficient horizontal space.
    """
    def __init__(self, parent=None, margin=-1, hSpacing=-1, vSpacing=-1):
        super().__init__(parent)
        self.itemList = []
        
        # Set margins if provided, else rely on defaults
        if margin >= 0:
            self.setContentsMargins(margin, margin, margin, margin)
            
        self.m_hSpace = hSpacing
        self.m_vSpace = vSpacing

    def __del__(self):
        try:
            item = self.takeAt(0)
            while item:
                item = self.takeAt(0)
        except RuntimeError:
            pass

    def _update_parent_height(self):
        try:
            pw = self.parentWidget()
            if pw:
                pw.setMinimumHeight(self.heightForWidth(pw.width()))
            # 強制手動刷新內部子元件大小（修正上層佈局優化導致的0x0隱形Bug）
            self.setGeometry(self.geometry())
        except RuntimeError:
            # Layout might be destroyed
            pass

    def addItem(self, item):
        self.itemList.append(item)
        self.invalidate()
        QTimer.singleShot(0, self._update_parent_height)

    def horizontalSpacing(self):
        if self.m_hSpace >= 0:
            return self.m_hSpace
        return self.smartSpacing(QStyle.PixelMetric.PM_LayoutHorizontalSpacing)

    def verticalSpacing(self):
        if self.m_vSpace >= 0:
            return self.m_vSpace
        return self.smartSpacing(QStyle.PixelMetric.PM_LayoutVerticalSpacing)

    def count(self):
        return len(self.itemList)

    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            item = self.itemList.pop(index)
            QTimer.singleShot(0, self._update_parent_height)
            return item
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self._doLayout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        height = self._doLayout(rect, False)
        
        # When inside QTableWidget's cell, custom layout height isn't automatically requested
        # We explicitly enforce the calculated height on the parent widget's minimumHeight
        pw = self.parentWidget()
        if pw and pw.minimumHeight() != height:
            pw.setMinimumHeight(height)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.itemList:
            size = size.expandedTo(item.minimumSize())
        margin, _, _, _ = self.getContentsMargins()
        size += QSize(2 * margin, 2 * margin)
        return size

    def _doLayout(self, rect, testOnly):
        x = rect.x()
        y = rect.y()
        lineHeight = 0

        # Adjust space defaults if simple fixed spacing
        spacing_x = self.m_hSpace if self.m_hSpace >= 0 else 4
        spacing_y = self.m_vSpace if self.m_vSpace >= 0 else 4

        for item in self.itemList:
            wid = item.widget()
            spaceX = spacing_x
            spaceY = spacing_y
            
            # If expanding, might still have issues, but for tags it should be fixed or minimum
            nextX = x + item.sizeHint().width() + spaceX
            
            if nextX - spaceX > rect.right() and lineHeight > 0:
                x = rect.x()
                y = y + lineHeight + spaceY
                nextX = x + item.sizeHint().width() + spaceX
                lineHeight = 0

            if not testOnly:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = nextX
            lineHeight = max(lineHeight, item.sizeHint().height())

        return y + lineHeight - rect.y()

    def smartSpacing(self, pm):
        parent = self.parent()
        if not parent:
            return -1
        elif parent.isWidgetType():
            from PyQt6.QtWidgets import QStyle
            return parent.style().pixelMetric(pm, None, parent)
        else:
            return parent.spacing()
