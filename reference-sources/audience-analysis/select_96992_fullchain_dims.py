# -*- coding: utf-8 -*-
"""
96992 全链路维度筛选（按 outbound-phone-funnel-analysis 方法）

核心：
- 不预设只用二维；同时评估二维/三维候选。
- 目标看到出单：全量->出单、ABM->出单，同时保留全量->ABM 作为前转参考。
- 指标：IV + 达标覆盖率 + 50/50 样本外 lift + 衰减。

输出：
- output/96992全链路/96992_维度筛选_IV_CV_OOS.xlsx
- output/96992全链路/96992_维度筛选_IV_CV_OOS.md
- output/96992全链路/96992_维度筛选评分.csv
- output/96992全链路/96992_推荐维度_出单明细.csv
"""

import csv
import glob
import hashlib
import math
import os
import re
import sys
from collections import defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from operator_segments import classify_operator  # noqa
import phone_dims as PD  # noqa

try:
    from phone import Phone as _PhoneLib
    _PHONE = _PhoneLib()
except Exception:
    _PHONE = None

SRC_DIR = os.environ.get("SRC_DIR", "/Users/a1/Desktop/96992_数据源2_明文_补齐")
ORDER_STD = os.environ.get("ORDER_STD", os.path.join(ROOT, "output", "96992全链路", "96992_SQL出单_明文标准化.csv"))
OUT_DIR = os.path.join(ROOT, "output", "96992全链路")

OUT_XLSX = os.path.join(OUT_DIR, "96992_维度筛选_IV_CV_OOS.xlsx")
OUT_MD = os.path.join(OUT_DIR, "96992_维度筛选_IV_CV_OOS.md")
OUT_SCORE = os.path.join(OUT_DIR, "96992_维度筛选评分.csv")
OUT_REC_DETAIL = os.path.join(OUT_DIR, "96992_推荐维度_出单明细.csv")

S_LIFT = float(os.environ.get("S_LIFT", 1.30))
MIN_COV = float(os.environ.get("MIN_COV", 0.50))


def norm_md5(v):
    s = str(v or "").strip().lower()
    return s if re.fullmatch(r"[0-9a-f]{32}", s) else ""


def valid_phone(v):
    s = str(v or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s if len(s) == 11 and s.isdigit() and s.startswith("1") else ""


def fnum(v):
    try:
        return float(str(v or "0").strip())
    except Exception:
        return 0.0


def is_abm(level):
    lv = str(level or "").strip().upper()
    return 1 if lv.startswith(("A", "B", "M")) or "A级" in lv or "B级" in lv or "M级" in lv else 0


def is_conn(status, minutes):
    st = str(status or "").strip()
    return 1 if minutes > 0 or "已接" in st or "接听" in st or "接通" in st else 0


def stable_train_key(md5, phone):
    key = md5 or phone or "missing"
    return hashlib.md5(key.encode("utf-8")).digest()[0] < 128


def generation(phone):
    if len(phone) != 11 or not phone.isdigit():
        return "其他号段"
    return {
        "13": "13x", "14": "14x", "15": "15x", "16": "16x",
        "17": "17x", "18": "18x", "19": "19x",
    }.get(phone[:2], "其他号段")


def number_type(phone):
    p3 = phone[:3] if len(phone) >= 3 else ""
    if p3 in {"140", "141", "144", "146", "148"}:
        return "物联网卡"
    if p3 in {"170", "171"}:
        return "虚拟号(MVNO)"
    if len(phone) == 11 and phone.isdigit():
        return "普通号(三大+广电)"
    return "异常/非标"


def homeloc(phone, cache):
    if not phone:
        return "(未知)", "(未知)"
    p7 = phone[:7]
    if p7 in cache:
        return cache[p7]
    prov = city = "(未知)"
    if _PHONE is not None:
        try:
            info = _PHONE.find(int(phone))
            if info:
                prov = info.get("province") or "(未知)"
                city = info.get("city") or "(未知)"
        except Exception:
            pass
    cache[p7] = (prov, city)
    return prov, city


def load_post_set():
    if not os.path.exists(ORDER_STD):
        raise FileNotFoundError(f"缺少标准化SQL出单文件: {ORDER_STD}")
    order = post = sign = 0
    post_set = set()
    order_set = set()
    sign_set = set()
    with open(ORDER_STD, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        for r in rd:
            m = norm_md5(r.get("md5"))
            if not m:
                continue
            if str(r.get("是否下单", "0")).strip() in {"1", "是", "true", "True"}:
                order_set.add(m); order += 1
            if str(r.get("是否出单", "0")).strip() in {"1", "是", "true", "True"}:
                post_set.add(m); post += 1
            if str(r.get("是否签收", "0")).strip() in {"1", "是", "true", "True"}:
                sign_set.add(m); sign += 1
    print(f"✓ downstream: order={order:,}, post={post:,}, sign={sign:,}", flush=True)
    return order_set, post_set, sign_set


def make_dimsets():
    # 只保留可落地且与 outbound 方法一致的主轴：
    # 筛选看地域/运营商/号段；到出单额外看号段世代。
    return [
        ("归属地省×运营商", ("归属地省", "运营商")),
        ("归属地省×前3位号段", ("归属地省", "前3位号段")),
        ("运营商×前3位号段", ("运营商", "前3位号段")),
        ("城市群×运营商", ("城市群", "运营商")),
        ("城市群×前3位号段", ("城市群", "前3位号段")),
        ("经济带分层×运营商", ("经济带分层", "运营商")),
        ("经济带分层×前3位号段", ("经济带分层", "前3位号段")),
        ("归属地省×运营商×前3位号段", ("归属地省", "运营商", "前3位号段")),
        ("归属地省×运营商×号段世代", ("归属地省", "运营商", "号段世代")),
        ("城市群×运营商×前3位号段", ("城市群", "运营商", "前3位号段")),
        ("经济带分层×运营商×前3位号段", ("经济带分层", "运营商", "前3位号段")),
        ("运营商×前3位号段×号段世代", ("运营商", "前3位号段", "号段世代")),
    ]


def new_counter():
    # all, abm, post
    return [0, 0, 0]


def bump(c, abm, post):
    c[0] += 1
    c[1] += abm
    c[2] += post


def iv_calc(groups, base_idx, good_idx, min_base):
    ok = []
    for c in groups.values():
        base = c[base_idx]
        good = c[good_idx]
        if base >= min_base:
            ok.append((base, good, base - good))
    if not ok:
        return 0.0, 0.0, 0
    good_sum = sum(x[1] for x in ok)
    bad_sum = sum(x[2] for x in ok)
    m = len(ok)
    if good_sum <= 0 or bad_sum <= 0:
        return 0.0, sum(x[0] for x in ok), m
    iv = 0.0
    for _base, good, bad in ok:
        gd = (good + 0.5) / (good_sum + 0.5 * m)
        bd = (bad + 0.5) / (bad_sum + 0.5 * m)
        iv += (gd - bd) * math.log(gd / bd)
    return iv, sum(x[0] for x in ok), m


def eval_dim(name, tr, te, all_g, funnel_name, base_idx, good_idx, min_base):
    tr_base = sum(c[base_idx] for c in tr.values())
    tr_good = sum(c[good_idx] for c in tr.values())
    te_base = sum(c[base_idx] for c in te.values())
    te_good = sum(c[good_idx] for c in te.values())
    all_base = sum(c[base_idx] for c in all_g.values())
    if not tr_base or not te_base:
        return None
    base_tr_rate = tr_good / tr_base if tr_base else 0.0
    base_te_rate = te_good / te_base if te_base else 0.0
    if base_tr_rate <= 0 or base_te_rate <= 0:
        return None

    iv, cov_base, n_groups = iv_calc(tr, base_idx, good_idx, min_base)
    coverage = cov_base / tr_base if tr_base else 0.0

    selected = set()
    sel_tr_base = sel_tr_good = 0
    for k, c in tr.items():
        b = c[base_idx]
        g = c[good_idx]
        if b < min_base:
            continue
        lift = (g / b) / base_tr_rate if b else 0.0
        if lift >= S_LIFT and g > 0:
            selected.add(k)
            sel_tr_base += b
            sel_tr_good += g
    if not selected:
        return None

    sel_te_base = sel_te_good = 0
    for k in selected:
        c = te.get(k)
        if not c:
            continue
        sel_te_base += c[base_idx]
        sel_te_good += c[good_idx]
    train_lift = ((sel_tr_good / sel_tr_base) / base_tr_rate) if sel_tr_base else 0.0
    test_lift = ((sel_te_good / sel_te_base) / base_te_rate) if sel_te_base else 0.0
    decay = (test_lift / train_lift - 1.0) if train_lift else 0.0
    test_cov = sel_te_base / te_base if te_base else 0.0
    score = test_lift * (test_cov ** 0.5) * max(0.25, 1 + decay) * (1.0 if coverage >= MIN_COV else 0.55)
    return {
        "漏斗": funnel_name,
        "维度": name,
        "维度层级": f"{name.count('×') + 1}维",
        "IV": iv,
        "IV覆盖率%": coverage * 100,
        "达标组数": n_groups,
        "S组数": len(selected),
        "train_lift": train_lift,
        "test_lift": test_lift,
        "衰减%": decay * 100,
        "test覆盖率%": test_cov * 100,
        "综合分": score,
        "入口样本": all_base,
    }


def row_context(phone, prov_cache):
    ph = valid_phone(phone)
    prov, city = homeloc(ph, prov_cache) if ph else ("(未知)", "(未知)")
    op = classify_operator(ph) if ph else "需解密/非明文"
    return {
        "归属地省": prov,
        "运营商": op,
        "前3位号段": ph[:3] if ph else "未知号段",
        "号段世代": generation(ph),
        "城市群": PD.cluster(prov),
        "经济带分层": PD.econ_tier(prov),
        "号码类型": number_type(ph),
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    _order_set, post_set, _sign_set = load_post_set()
    dimsets = make_dimsets()
    agg = {
        name: {
            "all": defaultdict(new_counter),
            "tr": defaultdict(new_counter),
            "te": defaultdict(new_counter),
            "cols": cols,
        }
        for name, cols in dimsets
    }
    files = sorted(p for p in glob.glob(os.path.join(SRC_DIR, "*_补齐.csv")) if "最终未匹配" not in os.path.basename(p))
    prov_cache = {}
    ctx_cache = {}
    rows = abm_total = post_total = 0
    seen_post_md5 = set()

    for fp in files:
        n = 0
        with open(fp, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            for r in rd:
                rows += 1
                n += 1
                m = norm_md5(r.get("md5"))
                ph = valid_phone(r.get("手机号明文"))
                abm = is_abm(r.get("意向等级"))
                post = 1 if m in post_set and m not in seen_post_md5 else 0
                if post:
                    seen_post_md5.add(m)
                abm_total += abm
                post_total += post
                ctx_key = ph[:7] if ph else "unknown"
                ctx = ctx_cache.get(ctx_key)
                if ctx is None:
                    ctx = row_context(ph, prov_cache)
                    ctx_cache[ctx_key] = ctx
                side = "tr" if stable_train_key(m, ph) else "te"
                for name, meta in agg.items():
                    key = "×".join(ctx[c] for c in meta["cols"])
                    bump(meta["all"][key], abm, post)
                    bump(meta[side][key], abm, post)
        print(f"  ✓ {os.path.basename(fp)} rows={n:,} total={rows:,}", flush=True)

    funnels = [
        ("全量->ABM", 0, 1, 3000),
        ("全量->出单", 0, 2, 3000),
        ("ABM->出单", 1, 2, 80),
    ]
    score_rows = []
    for name, meta in agg.items():
        for funnel_name, base_idx, good_idx, min_base in funnels:
            r = eval_dim(name, meta["tr"], meta["te"], meta["all"], funnel_name, base_idx, good_idx, min_base)
            if r:
                score_rows.append(r)
    score_df = pd.DataFrame(score_rows).sort_values(["漏斗", "综合分"], ascending=[True, False])
    score_df.to_csv(OUT_SCORE, index=False, encoding="utf-8-sig")

    # 推荐出单维度：优先全量->出单，其次ABM->出单
    post_scores = score_df[score_df["漏斗"] == "全量->出单"].copy()
    best = post_scores.iloc[0] if len(post_scores) else score_df.iloc[0]
    best_dim = best["维度"]
    best_meta = agg[best_dim]
    rows_detail = []
    all_base = sum(c[0] for c in best_meta["all"].values())
    all_post = sum(c[2] for c in best_meta["all"].values())
    all_abm = sum(c[1] for c in best_meta["all"].values())
    for tag, c in best_meta["all"].items():
        n, abm, post = c
        rows_detail.append({
            "推荐维度": best_dim,
            "标签": tag,
            "全量": n,
            "ABM数": abm,
            "出单数": post,
            "ABM率%": (abm / n * 100) if n else 0,
            "出单率(全量)%": (post / n * 100) if n else 0,
            "出单率(ABM)%": (post / abm * 100) if abm else 0,
            "ABM指数": ((abm / n) / (all_abm / all_base) * 100) if n and all_base and all_abm else 0,
            "出单指数": ((post / n) / (all_post / all_base) * 100) if n and all_base and all_post else 0,
        })
    detail_df = pd.DataFrame(rows_detail).sort_values(["出单指数", "全量"], ascending=[False, False])
    detail_df.to_csv(OUT_REC_DETAIL, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        score_df.to_excel(w, sheet_name="维度评分_IV_OOS", index=False)
        score_df[score_df["漏斗"] == "全量->出单"].head(100).to_excel(w, sheet_name="全量到出单维度Top", index=False)
        score_df[score_df["漏斗"] == "ABM->出单"].head(100).to_excel(w, sheet_name="ABM到出单维度Top", index=False)
        score_df[score_df["漏斗"] == "全量->ABM"].head(100).to_excel(w, sheet_name="全量到ABM维度Top", index=False)
        detail_df.head(3000).to_excel(w, sheet_name="推荐维度出单明细", index=False)

    L = []
    A = L.append
    A("# 96992 全链路维度筛选（outbound 方法）\n")
    A(f"> 样本 `{rows:,}`；ABM `{abm_total:,}`；出单 `{post_total:,}`。")
    A("> 方法：IV 只算达标组并报告覆盖率；train 定 S 组，test 验 lift；二维/三维一起比较。")
    A(f"> 到出单推荐维度：`{best_dim}`（{best['维度层级']}，test_lift {best['test_lift']:.2f}，衰减 {best['衰减%']:+.1f}%）。")
    for funnel in ["全量->出单", "ABM->出单", "全量->ABM"]:
        A(f"\n## {funnel} 维度 Top10")
        A("| 排名 | 维度 | 层级 | IV | 覆盖率 | train_lift | test_lift | 衰减 | test覆盖 | 综合分 |")
        A("|---:|---|---|---:|---:|---:|---:|---:|---:|---:|")
        sub = score_df[score_df["漏斗"] == funnel].head(10)
        for i, (_, r) in enumerate(sub.iterrows(), 1):
            A(f"| {i} | {r['维度']} | {r['维度层级']} | {r['IV']:.4f} | {r['IV覆盖率%']:.1f}% | {r['train_lift']:.2f} | {r['test_lift']:.2f} | {r['衰减%']:+.1f}% | {r['test覆盖率%']:.1f}% | {r['综合分']:.3f} |")
    A("\n## 产物")
    for p in [OUT_XLSX, OUT_SCORE, OUT_REC_DETAIL]:
        A(f"- `{p}`")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"rows={rows:,}, abm={abm_total:,}, post={post_total:,}", flush=True)
    print(f"best_post_dim={best_dim}, test_lift={best['test_lift']:.3f}, decay={best['衰减%']:+.1f}%", flush=True)
    print(f"wrote: {OUT_XLSX}", flush=True)
    print(f"wrote: {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
