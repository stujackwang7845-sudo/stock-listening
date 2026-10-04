"""
回復原始功能：移除注意條款下載功能，恢復正常運作
"""

with open('ui/dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 修改 on_clause_download_finished，移除會造成問題的 load_data_for_date 呼叫
new_method = '''    def on_clause_download_finished(self, success, msg):
        """下載完成後的處理"""
        # Re-enable buttons
        self.btn_clause_download.setEnabled(True)
        self.btn_official_disposal.setEnabled(True)
        self.refresh_date_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "下載完成", msg + "\\n\\n注意：條款已儲存到資料庫，但目前表格不會自動顯示。\\n請使用資料庫工具查詢 attention_clauses 資料表。")
            self.status_message_updated.emit("就緒")
        else:
            QMessageBox.critical(self, "下載失敗", msg)
            self.status_message_updated.emit("錯誤")

'''

# 替換 (2184-2202)
lines[2183:2202] = [new_method]

with open('ui/dashboard.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("✓ 已恢復功能")
print("✓ 移除了會造成表格空白的程式碼")
print("✓ 注意條款下載功能仍可使用，但資料儲存在資料庫中")
