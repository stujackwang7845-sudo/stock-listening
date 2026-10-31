"""策略回測頁的組合指標(core/strategy_backtest.metrics)。"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core import strategy_backtest as sb  # noqa: E402


class MetricsTest(unittest.TestCase):
    def test_equal_weight_portfolio(self):
        d1, d2, d3, d4 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)
        a = dict(entry=d1, exit=d3, closed=True, ret=0.2, path={d2: 0.1, d3: 0.2})
        b = dict(entry=d2, exit=None, closed=False, ret=-0.05, path={d3: -0.1, d4: -0.05})
        m = sb.metrics([a, b], [d1, d2, d3, d4])
        self.assertEqual(m["max_conc"], 2)               # d2 兩筆同時持有
        self.assertEqual(m["now_conc"], 1)               # 最後一天只剩 b
        self.assertEqual((m["n"], m["n_closed"], m["n_open"]), (2, 1, 1))
        eq = [v for _, v in m["equity"]]
        for x, y in zip(eq, [1.0, 1.05, 1.05, 1.075]):   # 每筆資金 1/2，不複利
            self.assertAlmostEqual(x, y)
        self.assertAlmostEqual(m["total"], 0.075)
        self.assertAlmostEqual(m["c_total"], 1.05 * 1.025 - 1)  # 複利：每天損益用當時淨值滾算
        self.assertAlmostEqual(m["mdd"], 0.0)
        self.assertAlmostEqual(m["win"], 0.5)

    def test_costs(self):
        self.assertAlmostEqual(sb._net_long(0.0), (1 - sb.FEE - sb.TAX) / (1 + sb.FEE) - 1)
        self.assertAlmostEqual(sb._net_short(0.0), -(2 * sb.FEE + sb.TAX))
        self.assertGreater(sb._net_short(0.1), 0.09)

    def test_empty(self):
        self.assertEqual(sb.metrics([], [date(2026, 10, 1)]), dict(n=0))


if __name__ == "__main__":
    unittest.main()
