"""
桌面版使用者設定(2026-10-20)：存在資料目錄的 app_settings.json，沒有檔案就用預設值。

new_regime_only：只顯示新制(2026-08-10 起公告的處置)資料，預設開啟。
  開：歷史紀錄、處置統計、統計圖表、詳細條件的歷史處置、總覽/儀表板的日期切換都只到新制。
  關：顯示全部資料(含舊制)。
allow_manual_edit：處置統計的漲跌幅格子可以手動修改(存成手動覆蓋值，會蓋過系統計算值)，預設關閉。
  3441 出關日曾因格子一打字就存而誤存成 5.0(實際 +9.76%)，所以預設不能改。
"""
import json
import os

NEW_REGIME_START = "2026-08-10"     # 新制只適用 8/10(含)以後公告的處置(不回溯)
_DEFAULTS = {"new_regime_only": True, "allow_manual_edit": False}
_listeners = {}                     # key -> [fn(value)]


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


def get(key):
    return bool(load().get(key, _DEFAULTS.get(key)))


def set_value(key, value):
    data = load()
    data[key] = bool(value)
    save(data)
    for fn in list(_listeners.get(key, [])):
        try:
            fn(bool(value))
        except Exception as e:
            print(f"[app_settings] 通知畫面更新失敗({key})：{e}")


def on_change(fn, key="new_regime_only"):
    """註冊設定變更時要呼叫的函式(各頁面重新整理)。"""
    _listeners.setdefault(key, []).append(fn)


# 便利函式(既有呼叫端沿用)
def new_regime_only():
    return get("new_regime_only")


def set_new_regime_only(value):
    set_value("new_regime_only", value)


def allow_manual_edit():
    return get("allow_manual_edit")


def set_allow_manual_edit(value):
    set_value("allow_manual_edit", value)
