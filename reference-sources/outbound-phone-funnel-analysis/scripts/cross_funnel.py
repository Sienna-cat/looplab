# -*- coding: utf-8 -*-
"""全漏斗维度交叉遍历（新逻辑）。
修正三大盲区: ①不用"基数≤N"硬剔除(用覆盖率约束) ②IV只用达标组并报真实覆盖率
③前向选择带覆盖率约束 + 1-2维全遍历回看。
用法: 改 CONFIG 后 `python cross_funnel.py`。
"""
import os
import json
import itertools
import numpy as np
import pandas as pd

# ===== CONFIG =====
PKL = "output/enriched.pkl"          # 富集后数据(含维度列与 in_* 标记列)
OUT_JSON = "output/cross_funnel.json"
POOL = ["province", "city", "operator", "operator_type", "seg3", "seg4", "seg5",
        "city_tier", "era", "seg_year_band", "seg_launch_year", "region",
        "cluster", "econ_tier", "coastal", "lucky_grade"]
# 漏斗: (名称, 入口mask列或None=全量, 目标列, 该漏斗min_base)
FUNNELS = [
    ("全量→接通", None, "in_connect", 3000),
    ("接通→20秒", "in_connect", "in_talk20", 1500),
    ("20秒→意向", "in_talk20", "in_abm", 800),
    ("全量→意向", None, "in_abm", 3000),
    ("全量→成交", None, "in_order", 3000),
]
MIN_COV = 0.60        # 可落地覆盖率下限
STOP_GAIN = 0.003     # 前向选择停止增益
MAX_STEPS = 5
# ==================

df = pd.read_pickle(PKL)
POOL = [d for d in POOL if d in df.columns]
for d in POOL:
    df[d] = df[d].astype("category")
CARD = {d: int(df[d].nunique()) for d in POOL}


def make_iv(sub, Y, min_base):
    overall = sub[Y].mean()
    N = len(sub)

    def iv_of(cols):
        g = sub.groupby(list(cols), observed=True)[Y].agg(cnt="size", good="sum").reset_index()
        ok = g[g["cnt"] >= min_base]
        if len(ok) == 0:
            return 0.0, 0, 0.0, 0.0
        good = ok["good"].to_numpy(float)
        bad = (ok["cnt"] - ok["good"]).to_numpy(float)
        m = len(ok)
        gd = (good + 0.5) / (good.sum() + 0.5 * m)
        bd = (bad + 0.5) / (bad.sum() + 0.5 * m)
        iv = float(((gd - bd) * np.log(gd / bd)).sum())
        cov = float(ok["cnt"].sum() / N)
        lift_max = float((good / ok["cnt"].to_numpy(float)).max() / overall) if overall > 0 else 0.0
        return iv, m, cov, lift_max
    return iv_of, overall, N


def forward(iv_of):
    chosen, remaining, prev, path = [], list(POOL), 0.0, []
    for _ in range(MAX_STEPS):
        best = None
        for d in remaining:
            iv, m, cov, lf = iv_of(chosen + [d])
            cand = (iv, cov >= MIN_COV, d, m, cov)
            if best is None or (cand[1], cand[0]) > (best[1], best[0]):
                best = cand
        iv, okcov, d, m, cov = best
        gain = iv - prev
        chosen.append(d); remaining.remove(d)
        path.append({"step": len(chosen), "added": d, "iv": round(iv, 4),
                     "gain": round(gain, 4), "coverage": round(cov, 3), "cov_ok": okcov})
        prev = iv
        if gain < STOP_GAIN and len(chosen) >= 2:
            break
    return path


results = {}
for name, mask, Y, min_base in FUNNELS:
    if Y not in df.columns:
        continue
    sub = df if mask is None else df[df[mask]].copy()
    iv_of, overall, N = make_iv(sub, Y, min_base)
    print(f"\n{'='*70}\n[{name}] 基数{N:,} 目标率{overall*100:.3f}% min_base={min_base}\n{'='*70}")

    single = sorted(
        [{"dim": d, "card": CARD[d], **dict(zip(["iv", "n_groups", "coverage", "lift_max"], iv_of([d])))}
         for d in POOL], key=lambda x: -x["iv"])
    print("单维 Top6 (IV | 覆盖 | 组数):")
    for r in single[:6]:
        print(f"  {r['dim']:<16} IV{r['iv']:.4f} 覆盖{r['coverage']*100:5.1f}% 组{r['n_groups']:>5} 基数{r['card']}")

    pairs = []
    for a, b in itertools.combinations(POOL, 2):
        iv, m, cov, lf = iv_of([a, b])
        pairs.append({"dims": [a, b], "iv": round(iv, 4), "n_groups": m, "coverage": round(cov, 3)})
    land = sorted([p for p in pairs if p["coverage"] >= MIN_COV], key=lambda x: -x["iv"])
    print(f"两维·可落地(覆盖≥{int(MIN_COV*100)}%) Top5:")
    for r in land[:5]:
        print(f"  IV{r['iv']:.4f} 覆盖{r['coverage']*100:5.1f}% 组{r['n_groups']:>5}  {r['dims'][0]} × {r['dims'][1]}")

    fp = forward(iv_of)
    print("前向(覆盖率约束):")
    for p in fp:
        print(f"  step{p['step']} +{p['added']:<15} IV{p['iv']:.4f} 增益{p['gain']:+.4f} 覆盖{p['coverage']*100:.1f}%"
              + ("" if p["cov_ok"] else " [覆盖不足]"))

    cand = [(r["iv"], [r["dim"]]) for r in single if r["coverage"] >= MIN_COV]
    cand += [(r["iv"], r["dims"]) for r in land]
    rec = max(cand, key=lambda x: x[0]) if cand else (0, [])
    print(f"★ 推荐可落地组合: {' × '.join(rec[1])}  (IV{rec[0]:.4f})")
    results[name] = {"base": N, "overall": overall, "single": single,
                     "land_pairs_top": land[:15], "forward": fp, "rec_dims": rec[1], "rec_iv": rec[0]}

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=float)
print(f"\n已输出 {OUT_JSON}")
