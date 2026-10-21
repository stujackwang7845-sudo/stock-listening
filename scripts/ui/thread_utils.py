"""
背景執行緒(QThread)安全工具(2026-10-22)。

問題：`self.calc_worker = CalculationWorker(...)` 這種寫法，若舊的 worker 還在跑，就會失去最後一個參考而被回收，
Qt 直接 qFatal「QThread: Destroyed while thread is still running」→ 整個程式閃退(沒有 Python 錯誤訊息)。
例：儀表板連點兩檔股票，第一檔還在向 FinMind 補行情時就換成第二檔(2026-10-22 閃退)。

retire(worker)：要換掉 worker 之前呼叫；還在跑就先保管到跑完，跑完的自動清掉。
不依賴 finished 訊號(有些 worker 自訂了同名的 finished 訊號、例外時不會發)，每次呼叫時順便清掉已結束的。
"""
_alive = set()


def retire(worker):
    global _alive
    _alive = {w for w in _alive if _running(w)}
    if worker is not None and _running(worker):
        _alive.add(worker)


def _running(w):
    try:
        return w.isRunning()
    except RuntimeError:          # 底層 C++ 物件已刪除
        return False


def alive_count():
    """還在保管中的背景執行緒數(測試/除錯用)。"""
    return sum(1 for w in _alive if _running(w))
