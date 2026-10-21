"""背景執行緒保管(2026-10-22 閃退「QThread: Destroyed while thread is still running」)。"""
import gc
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from PyQt6.QtCore import QCoreApplication, QThread  # noqa: E402

from ui import thread_utils  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])


class Slow(QThread):
    def run(self):
        time.sleep(0.3)


class RetireTest(unittest.TestCase):
    def test_replaced_running_worker_is_kept_until_done(self):
        holder = type("H", (), {})()
        for _ in range(3):                      # 連點：start 後立刻換掉
            thread_utils.retire(getattr(holder, "w", None))
            holder.w = Slow()
            holder.w.start()
        gc.collect()
        self.assertEqual(thread_utils.alive_count(), 2)        # 被換掉的兩個還在跑、被保管
        holder.w.wait(2000)
        for w in list(thread_utils._alive):
            w.wait(2000)
        thread_utils.retire(None)                               # 順便清掉跑完的
        self.assertEqual(thread_utils.alive_count(), 0)
        self.assertEqual(len(thread_utils._alive), 0)

    def test_retire_none_and_finished_are_noop(self):
        thread_utils.retire(None)
        w = Slow()
        w.start()
        w.wait(2000)
        thread_utils.retire(w)
        self.assertEqual(thread_utils.alive_count(), 0)


if __name__ == "__main__":
    unittest.main()
