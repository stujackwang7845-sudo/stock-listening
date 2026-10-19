"""
桌面版使用者設定(2026-10-20)：存在資料目錄的 app_settings.json，沒有檔案就用預設值。

new_regime_only：只顯示新制(2026-08-10 起公告的處置)資料，預設開啟。
  開：歷史紀錄、處置統計、統計圖表、詳細條件的歷史處置、總覽/儀表板的日期切換都只到新制。
  關：顯示全部資料(含舊制)。
"""
import json
import os

NEW_REGIME_START = "2026-08-10"     # 新制只適用 8/10(含)以後公告的處置(不回溯)
_DEFAULTS = {"new_regime_only": True}
_listeners = []


def _path():
    from core.runtime import get_paths
    return os.path.join(os.path.dirname(get_paths().disposal_db), "app_settings.json")


def load():
    data = dict(_DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            data.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[app_settings] 讀取失敗，用預設值：{e}")
    return data


def save(data):
    p = _path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def new_regime_only():
    return bool(load().get("new_regime_only", True))


def set_new_regime_only(value):
    data = load()
    data["new_regime_only"] = bool(value)
    save(data)
    for fn in list(_listeners):
        try:
            fn(bool(value))
        except Exception as e:
            print(f"[app_settings] 通知畫面更新失敗：{e}")


def on_change(fn):
    """註冊設定變更時要呼叫的函式(各頁面重新整理)。"""
    _listeners.append(fn)
