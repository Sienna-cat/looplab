# -*- coding: utf-8 -*-
"""样本外验证: 对比 2/3/4/5维 在某目标上的泛化。防过拟合的金标准。
50/50 切分, train 定 S 级组合(lift≥阈值), test 验真实 lift。稀疏目标多 seed 取均值。
用法: 改 CONFIG 后 `python validate_oos.py`。
"""
import os
import numpy as np
import pandas as pd

# ===== CONFIG =====
PKL = "output/enriched.pkl"
TARGET = "in_abm"            # 目标列: in_abm(意向) 或 in_order(成交)
S_LIFT = 1.4                 # S 级 lift 阈值
MIN_BASE = 1500             # 半样本阈值(≈全样本阈值的一半)
SEEDS = [0]                 # 稀疏目标(如出单)改成 list(range(10)) 取均值
DIMSETS = [
    (["city", "operator_type"], "2维 城市×运营商"),
    (["city", "operator_type", "seg_year_band"], "3维 +放号时代"),
    (["province", "operator_type", "seg_year_band", "city_tier"], "4维 省份口径"),
    (["city", "operator_type", "era", "seg_year_band", "province"], "5维 激进"),
]
# ==================

df = pd.read_pickle(PKL)


def one_run(dims, seed):
    rng = np.random.RandomState(seed)
    m = rng.rand(len(df)) < 0.5
    tr, te = df[m], df[~m]
    oo_tr, oo_te = tr[TARGET].mean(), te[TARGET].mean()
    g = tr.groupby(dims, observed=True).agg(b=(TARGET, "size"), a=(TARGET, "sum")).reset_index()
    g = g[g["b"] >= MIN_BASE].copy()
    g["lift"] = (g["a"] / g["b"]) / oo_tr
    s = g[g["lift"] >= S_LIFT]
    skeys = set(map(tuple, s[dims].values))
    idx = [tuple(x) in skeys for x in te[dims].astype(object).values]
    sub = te[pd.Series(idx, index=te.index)]
    tr_l = (s["a"].sum() / s["b"].sum()) / oo_tr if len(s) else 0
    te_l = (sub[TARGET].mean() / oo_te) if len(sub) else 0
    return tr_l, te_l, len(s)


print(f"目标={TARGET}  整体率{df[TARGET].mean()*100:.4f}%  MIN_BASE={MIN_BASE}  seeds={len(SEEDS)}\n")
print(f"{'口径':<28}{'train_lift':>10}{'样本外lift':>11}{'衰减':>8}{'S级组数':>8}")
print("-" * 66)
rows = []
for dims, name in DIMSETS:
    rs = np.array([one_run(dims, s) for s in SEEDS])
    trl, tel, ns = rs.mean(axis=0)
    decay = (tel / trl - 1) * 100 if trl else 0
    rows.append((name, tel, decay))
    print(f"{name:<28}{trl:>10.2f}{tel:>11.2f}{decay:>7.1f}%{ns:>8.0f}")

best = max(rows, key=lambda x: x[1])
print(f"\n→ 样本外 lift 最高(最值钱、通常也最稳): 【{best[0]}】 ({best[1]:.2f})")
print("提示: 样本内 lift 高但样本外低 = 过拟合。优先选样本外高、衰减小者。")
