"""
策略 A 放空進場日比較(2026-10-18 使用者疑問)：7 天處置，出關日收盤回補，比較
  A ：結束日前一天收盤空(現行，回測選定版)
  A'：結束日(處置最後一天)收盤空
排除：進場日收盤跌停(空不到)。成本：手續費×2＋證交稅，未含融券/借券費。
uv run python scratch/short_entry_day_compare_20261018.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategy_hunt_20261016 as h  # noqa: E402


def main():
    ev = h.load_events()
    daily = h.w.load_daily({e["code"] for e in ev})
    recs = []
    for e in ev:
        g = daily.get(e["code"])
        if g is None or e["n"] != 7:
            continue
        dts = [d.strftime("%Y-%m-%d") for d in g.index]
        if e["ps"] not in dts or e["pe"] not in dts:
            continue
        i1 = dts.index(e["pe"])
        if i1 + 1 >= len(dts):
            continue                                  # 還沒出關
        recs.append((e, dts, g["Close"].tolist(), i1))
    split = sorted(e["ps"] for e, *_ in recs)[len(recs) // 2]
    for name, off in (("A  結束日前一天收盤空", -1), ("A' 結束日收盤空", 0)):
        rs, h1, h2 = [], [], []
        for e, dts, c, i1 in recs:
            ie, ix = i1 + off, i1 + 1
            if c[ie] <= h.lim_dn(c[ie - 1]) + 1e-6:
                continue
            r = (c[ie] * (1 - h.FEE - h.TAX) - c[ix] * (1 + h.FEE)) / c[ie]
            rs.append(r)
            (h1 if e["ps"] < split else h2).append(r)
        print(f"{name}：{len(rs)} 筆，平均 {sum(rs)/len(rs)*100:+.2f}%，勝率 {sum(x > 0 for x in rs)/len(rs)*100:.0f}%，"
              f"最差 {min(rs)*100:+.1f}%，前半/後半 {sum(h1)/len(h1)*100:+.2f}% / {sum(h2)/len(h2)*100:+.2f}%")


if __name__ == "__main__":
    main()
