# -*- coding: utf-8 -*-
"""双口径交叉验证分层名单 P0/P1/P2。
两套口径各定 S 级组合、各取 topN 号段; P0=交集(最稳), P1/P2=各自独有。
含分半稳定性检验。可放开 topN 约束放量(见 SCALE_GRID)。
用法: 改 CONFIG 后 `python tier_list.py`。
"""
import os
import numpy as np
import pandas as pd

# ===== CONFIG =====
PKL = "output/enriched.pkl"
SEG_COL = "seg7"            # 落地单位(前7位号段)
TARGET = "in_abm"
S_LIFT = 1.4
MIN_BASE = 3000
TOPN = 15000               # 每口径取的 S 级号段数
DIMS_A = ["city", "operator_type"]                                  # 口径A(主, 样本外最优)
DIMS_B = ["province", "operator_type", "seg_year_band", "city_tier"]  # 口径B(交叉验证)
SCALE_GRID = [10000, 15000, None]   # None=放开(全 S 级交集)
OUT_CSV = "output/tier_P0_intersection.csv"
# ==================

df = pd.read_pickle(PKL)
df[SEG_COL] = df[SEG_COL].astype(str)
oa = df[TARGET].mean()

seg = (df.groupby(SEG_COL, observed=True)
       .agg(**{c: (c, "first") for c in set(DIMS_A + DIMS_B)},
            号码数=(TARGET, "size"), 命中数=(TARGET, "sum")).reset_index())


def rank_series(dims):
    comb = df.groupby(dims, observed=True).agg(b=(TARGET, "size"), a=(TARGET, "sum")).reset_index()
    comb["c_lift"] = (comb["a"] / comb["b"]) / oa
    skeys = set(map(tuple, comb[(comb["b"] >= MIN_BASE) & (comb["c_lift"] >= S_LIFT)][dims].values))
    t = seg.merge(comb[dims + ["c_lift"]], on=dims, how="left")
    isS = np.array([tuple(x) in skeys for x in t[dims].astype(object).values])
    ts = t[isS].sort_values(["c_lift", "号码数"], ascending=False).reset_index(drop=True)
    rk = pd.Series(10**9, index=t[SEG_COL].values)
    rk.loc[ts[SEG_COL].values] = np.arange(1, len(ts) + 1)
    return rk, t.set_index(SEG_COL)["c_lift"]


rA, liftA = rank_series(DIMS_A)
rB, liftB = rank_series(DIMS_B)
seg = seg.set_index(SEG_COL)
seg["rA"], seg["rB"], seg["liftA"], seg["liftB"] = rA, rB, liftA, liftB

# 分半稳定性
rng = np.random.RandomState(7); h = rng.rand(len(df)) < 0.5
h1, h2 = df[h], df[~h]; o1, o2 = h1[TARGET].mean(), h2[TARGET].mean()
g1 = h1.groupby(SEG_COL, observed=True)[TARGET].agg(n="size", a="sum")
g2 = h2.groupby(SEG_COL, observed=True)[TARGET].agg(n="size", a="sum")


def stab(segset):
    s1, s2 = g1[g1.index.isin(segset)], g2[g2.index.isin(segset)]
    l1 = (s1["a"].sum() / s1["n"].sum()) / o1 if s1["n"].sum() else 0
    l2 = (s2["a"].sum() / s2["n"].sum()) / o2 if s2["n"].sum() else 0
    return l1, l2


nSA, nSB = int((seg["rA"] < 10**9).sum()), int((seg["rB"] < 10**9).sum())
print(f"口径A({'×'.join(DIMS_A)}) S级段{nSA:,} | 口径B S级段{nSB:,} | 整体率{oa*100:.3f}%\n")
print("P0 放大代价曲线:")
print(f"{'约束':<14}{'P0段':>7}{'号码':>10}{'lift':>7}{'两半差距':>9}")
for N in SCALE_GRID:
    nn = max(nSA, nSB) if N is None else N
    inter = seg[(seg["rA"] <= nn) & (seg["rB"] <= nn)]
    l1, l2 = stab(set(inter.index))
    lf = inter["命中数"].sum() / inter["号码数"].sum() / oa if len(inter) else 0
    print(f"{('放开' if N is None else 'top'+str(N)):<14}{len(inter):>7,}{int(inter['号码数'].sum()):>10,}{lf:>7.2f}{abs(l1-l2):>9.3f}")

# 导出 P0(默认 TOPN) + P1/P2
A = set(seg[seg["rA"] <= TOPN].index); B = set(seg[seg["rB"] <= TOPN].index)
seg["层级"] = "其他"
seg.loc[list(A - B), "层级"] = "P1·主力(A独有)"
seg.loc[list(B - A), "层级"] = "P2·补充(B独有)"
seg.loc[list(A & B), "层级"] = "P0·必打底仓(交集)"
p0 = seg[seg["层级"] == "P0·必打底仓(交集)"].copy()
p0["综合lift"] = p0[["liftA", "liftB"]].min(axis=1)
p0 = p0.sort_values("综合lift", ascending=False).reset_index()
p0.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
print(f"\nP0 {len(p0):,}段 已导出 {OUT_CSV}")
for t in ["P0·必打底仓(交集)", "P1·主力(A独有)", "P2·补充(B独有)"]:
    s = seg[seg["层级"] == t]
    if len(s):
        print(f"  {t:<20} {len(s):>6,}段  lift {s['命中数'].sum()/s['号码数'].sum()/oa:.2f}")
