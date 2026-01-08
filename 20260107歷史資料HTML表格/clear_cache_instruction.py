"""
清除快取並測試主程式
"""
print("請執行以下步驟:")
print("1. 刪除快取檔案: data/cache.db")
print("2. 重新執行主程式: uv run python main.py")
print("3. 檢查「盤後處置公告 - 上櫃」區塊是否只顯示: 4304, 5351, 8299")
print("   (不應包含 82992 群聯二)")
print("\n如果仍有問題,請確認:")
print("- parser.parse_tpex_disposition 是否使用新版本")
print("- DEBUG輸出是否顯示正確的過濾結果")
