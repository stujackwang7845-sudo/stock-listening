
import json
import os
from typing import Dict, List, Optional
from datetime import datetime
from core.runtime import get_paths

class TagManager:
    """Tag 管理器 - 負責標籤的建立、編輯、刪除與持久化"""
    
    FILE_PATH = get_paths().tags_json
    
    # Material Design Icons 風格的 Unicode 符號集
    AVAILABLE_ICONS = {
        "star": "★",
        "bookmark": "🔖",
        "flag": "🚩",
        "label": "🏷️",
        "trending_up": "📈",
        "chart_line": "📊",
        "analytics": "📉",
        "alert": "⚠️",
        "warning": "⚡",
        "info": "ℹ️",
        "priority_high": "🔴",
        "lightbulb": "💡",
        "strategy": "🎯",
        "target": "🎲",
        "check_circle": "✓",
        "clock": "🕐",
        "circle": "●",
        "fire": "🔥",
        "rocket": "🚀",
        "trophy": "🏆"
    }
    
    def __init__(self):
        self.tags = {}  # {tag_id: tag_info}
        self.next_id = 1
        self._load()
    
    def _load(self):
        """從 JSON 檔案載入 Tag 配置"""
        if not os.path.exists(self.FILE_PATH):
            return
        
        try:
            with open(self.FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.tags = data.get("tags", {})
                self.next_id = data.get("next_id", 1)
                print(f"TagManager: 已載入 {len(self.tags)} 個標籤")
        except Exception as e:
            print(f"TagManager: 載入失敗 - {e}")
            self.tags = {}
            self.next_id = 1
    
    def save(self):
        """儲存 Tag 配置到 JSON 檔案"""
        try:
            data = {
                "tags": self.tags,
                "next_id": self.next_id
            }
            with open(self.FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print(f"TagManager: 已儲存 {len(self.tags)} 個標籤")
        except Exception as e:
            print(f"TagManager: 儲存失敗 - {e}")
    
    def create_tag(self, name: str, color: str, icon: str, description: str = "") -> str:
        """
        建立新標籤
        
        Args:
            name: 標籤名稱
            color: 十六進位顏色碼 (例如 #FF0000)
            icon: 圖案鍵值 (必須在 AVAILABLE_ICONS 中)
            description: 標籤敘述
            
        Returns:
            新建立的 tag_id
        """
        # 驗證圖案
        if icon not in self.AVAILABLE_ICONS:
            raise ValueError(f"Invalid icon: {icon}. Available: {list(self.AVAILABLE_ICONS.keys())}")
        
        # 產生新 ID
        tag_id = f"tag_{self.next_id:03d}"
        self.next_id += 1
        
        # 建立標籤
        self.tags[tag_id] = {
            "name": name,
            "color": color,
            "icon": icon,
            "description": description,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        self.save()
        print(f"TagManager: 已建立標籤 {tag_id} - {name}")
        return tag_id
    
    def update_tag(self, tag_id: str, name: Optional[str] = None, 
                   color: Optional[str] = None, icon: Optional[str] = None, 
                   description: Optional[str] = None):
        """
        更新標籤資訊
        
        Args:
            tag_id: 標籤ID
            name: 新名稱 (可選)
            color: 新顏色 (可選)
            icon: 新圖案 (可選)
            description: 新敘述 (可選)
        """
        if tag_id not in self.tags:
            raise ValueError(f"Tag not found: {tag_id}")
        
        if name is not None:
            self.tags[tag_id]["name"] = name
        if color is not None:
            self.tags[tag_id]["color"] = color
        if icon is not None:
            if icon not in self.AVAILABLE_ICONS:
                raise ValueError(f"Invalid icon: {icon}")
            self.tags[tag_id]["icon"] = icon
        if description is not None:
            self.tags[tag_id]["description"] = description
        
        self.tags[tag_id]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save()
        print(f"TagManager: 已更新標籤 {tag_id}")
    
    def delete_tag(self, tag_id: str):
        """
        刪除標籤
        
        Args:
            tag_id: 要刪除的標籤ID
        """
        if tag_id not in self.tags:
            raise ValueError(f"Tag not found: {tag_id}")
        
        del self.tags[tag_id]
        self.save()
        print(f"TagManager: 已刪除標籤 {tag_id}")
    
    def get_tag(self, tag_id: str) -> Optional[Dict]:
        """
        取得標籤資訊
        
        Args:
            tag_id: 標籤ID
            
        Returns:
            標籤資訊字典，若不存在則返回 None
        """
        return self.tags.get(tag_id)
    
    def get_all_tags(self) -> Dict[str, Dict]:
        """取得所有標籤"""
        return self.tags.copy()
    
    def get_tag_list(self) -> List[Dict]:
        """
        取得標籤列表（包含 ID）
        
        Returns:
            標籤列表，每個元素包含 id 和標籤資訊
        """
        return [
            {"id": tag_id, **tag_info}
            for tag_id, tag_info in self.tags.items()
        ]
    
    def tag_exists(self, tag_id: str) -> bool:
        """檢查標籤是否存在"""
        return tag_id in self.tags
    
    def get_icon_symbol(self, icon_key: str) -> str:
        """取得圖案的 Unicode 符號"""
        return self.AVAILABLE_ICONS.get(icon_key, "?")
    
    def get_available_icons(self) -> Dict[str, str]:
        """取得所有可用的圖案"""
        return self.AVAILABLE_ICONS.copy()
    
    def validate_tag_ids(self, tag_ids: List[str]) -> List[str]:
        """
        驗證並過濾標籤 ID 列表
        
        Args:
            tag_ids: 標籤 ID 列表
            
        Returns:
            只包含有效 ID 的列表
        """
        return [tid for tid in tag_ids if tid in self.tags]
