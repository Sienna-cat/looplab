# -*- coding: utf-8 -*-
"""96992 严格唯一 MD5 口径维度复核。"""

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

SRC_DIR = "/Users/a1/Desktop/96992_数据源2_明文_补齐"
ORDER_STD = os.path.join(ROOT, "output", "96992全链路", "96992_SQL出单_明文标准化.csv")
OUT_DIR = os.path.join(ROOT, "output", "96992全链路")
OUT_CSV = os.path.join(OUT_DIR, "96992_唯一MD5维度复核评分.csv")
OUT_MD = os.path.join(OUT_DIR, "96992_唯一MD5维度复核.md")
OUT_XLSX = os.path.join(OUT_DIR, "96992_唯一MD5维度复核.xlsx")
S_LIFT = 1.30


def norm_md5(v):
    s = str(v or "").strip().lower()
    return s if re.fullmatch(r"[0-9a-f]{32}", s) else ""


def valid_phone(v):
    s = str(v or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s if len(s) == 11 and s.isdigit() and s.startswith("1") else ""


def is_abm(level):
    lv = str(level or "").strip().upper()
    return 1 if lv.startswith(("A", "B", "M")) or "A级" in lv or "B级" in lv or "M级" in lv else 0


def generation(phone):
    if len(phone) != 11 or not phone.isdigit():
        return "其他号段"
    return {"13": "13x", "14": "14x", "15": "15x", "16": "16x", "17": "17x", "18": "18x", "19": "19x"}.get(phone[:2], "其他号段")


def homeloc(phone, cache):
    if not phone:
        return "(未知)"
    p7 = phone[:7]
    if p7 in cache:
        return cache[p7]
    prov = "(未知)"
    if _PHONE is not None:
        try:
            info = _PHONE.find(int(phone))
            if info:
                prov = info.get("province") or "(未知)"
        except Exception:
            pass
    cache[p7] = prov
    return prov


def ctx(phone, cache):
    ph = valid_phone(phone)
    prov = homeloc(ph, cache) if ph else "(未知)"
    return {
        "归属地省": prov,
        "运营商": classify_operator(ph) if ph else "需解密/非明文",
        "前3位号段": ph[:3] if ph else "未知号段",
        "号段世代": generation(ph),
        "城市群": PD.cluster(prov),
        "经济带分层": PD.econ_tier(prov),
    }


def dimsets():
    return [
        ("归属地省×运营商", ("归属地省", "运营商")),
        ("经济带分层×前3位号段", ("经济带分层", "前3位号段")),
        ("运营商×前3位号段", ("运营商", "前3位号段")),
        ("城市群×运营商", ("城市群", "运营商")),
        ("城市群×前3位号段", ("城市群", "前3位号段")),
        ("归属地省×运营商×号段世代", ("归属地省", "运营商", "号段世代")),
        ("归属地省×运营商×前3位号段", ("归属地省", "运营商", "前3位号段")),
        ("运营商×前3位号段×号段世代", ("运营商", "前3位号段", "号段世代")),
    ]


def side(md5, phone):
    return "tr" if hashlib.md5((md5 or phone or "missing").encode("utf-8")).digest()[0] < 128 else "te"


def new_counter():
    # unique all md5, unique abm md5, unique post, unique post with abm
    return [0, 0, 0, 0]


def bump(c, all_v=0, abm_v=0, post_v=0, abm_post_v=0):
    c[0] += all_v; c[1] += abm_v; c[2] += post_v; c[3] += abm_post_v


def load_post_set():
    s = set()
    with open(ORDER_STD, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            m = norm_md5(r.get("md5"))
            if m and str(r.get("是否出单", "0")).strip() in {"1", "是", "true", "True"}:
                s.add(m)
    return s


def iv_calc(groups, bidx, gidx, min_base):
    ok = [(c[bidx], c[gidx], c[bidx] - c[gidx]) for c in groups.values() if c[bidx] >= min_base]
    if not ok:
        return 0, 0, 0
    gs, bs = sum(x[1] for x in ok), sum(x[2] for x in ok)
    if gs <= 0 or bs <= 0:
        return 0, sum(x[0] for x in ok), len(ok)
    m, iv = len(ok), 0.0
    for _b, g, bad in ok:
        gd = (g + 0.5) / (gs + 0.5 * m)
        bd = (bad + 0.5) / (bs + 0.5 * m)
        iv += (gd - bd) * math.log(gd / bd)
    return iv, sum(x[0] for x in ok), len(ok)


def eval_dim(name, tr, te, bidx, gidx, min_base):
    tr_b, tr_g = sum(c[bidx] for c in tr.values()), sum(c[gidx] for c in tr.values())
    te_b, te_g = sum(c[bidx] for c in te.values()), sum(c[gidx] for c in te.values())
    if not tr_b or not te_b or not tr_g or not te_g:
        return None
    tr_rate, te_rate = tr_g / tr_b, te_g / te_b
    iv, cov_b, ngrp = iv_calc(tr, bidx, gidx, min_base)
    selected, st_b, st_g = set(), 0, 0
    for k, c in tr.items():
        b, g = c[bidx], c[gidx]
        if b >= min_base and g > 0 and (g / b) / tr_rate >= S_LIFT:
            selected.add(k); st_b += b; st_g += g
    if not selected:
        return None
    se_b = se_g = 0
    for k in selected:
        c = te.get(k)
        if c:
            se_b += c[bidx]; se_g += c[gidx]
    train_lift = (st_g / st_b) / tr_rate if st_b else 0
    test_lift = (se_g / se_b) / te_rate if se_b else 0
    decay = test_lift / train_lift - 1 if train_lift else 0
    test_cov = se_b / te_b if te_b else 0
    score = test_lift * (test_cov ** 0.5) * max(0.25, 1 + decay)
    return {"维度": name, "IV": iv, "IV覆盖率%": cov_b / tr_b * 100 if tr_b else 0, "达标组数": ngrp, "S组数": len(selected), "train_lift": train_lift, "test_lift": test_lift, "衰减%": decay * 100, "test覆盖率%": test_cov * 100, "综合分": score}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    posts = load_post_set()
    dims = dimsets()
    agg = {n: {"tr": defaultdict(new_counter), "te": defaultdict(new_counter), "cols": cols} for n, cols in dims}
    md5_meta = {}
    prov_cache, ctx_cache = {}, {}
    total = dup = 0

    for fp in sorted(p for p in glob.glob(os.path.join(SRC_DIR, "*_补齐.csv")) if "最终未匹配" not in os.path.basename(p)):
        n = 0
        with open(fp, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                total += 1; n += 1
                m = norm_md5(r.get("md5"))
                if not m:
                    continue
                ph = valid_phone(r.get("手机号明文"))
                abm = is_abm(r.get("意向等级"))
                rec = md5_meta.get(m)
                if rec is None:
                    ck = ph[:7] if ph else "unknown"
                    cctx = ctx_cache.get(ck)
                    if cctx is None:
                        cctx = ctx(ph, prov_cache); ctx_cache[ck] = cctx
                    sd = side(m, ph)
                    md5_meta[m] = [cctx, sd, bool(abm), ph]
                    for name, meta in agg.items():
                        key = "×".join(cctx[c] for c in meta["cols"])
                        bump(meta[sd][key], all_v=1, abm_v=abm)
                else:
                    dup += 1
                    if abm and not rec[2]:
                        rec[2] = True
                        cctx, sd = rec[0], rec[1]
                        for name, meta in agg.items():
                            key = "×".join(cctx[c] for c in meta["cols"])
                            bump(meta[sd][key], abm_v=1)
        print(f"  ✓ {os.path.basename(fp)} rows={n:,} total={total:,} unique={len(md5_meta):,}", flush=True)

    post_seen = abm_post = 0
    for m in posts:
        rec = md5_meta.get(m)
        if not rec:
            continue
        post_seen += 1
        cctx, sd, has_abm = rec[0], rec[1], rec[2]
        abm_post += int(has_abm)
        for name, meta in agg.items():
            key = "×".join(cctx[c] for c in meta["cols"])
            bump(meta[sd][key], post_v=1, abm_post_v=1 if has_abm else 0)

    rows = []
    for name, meta in agg.items():
        for funnel, bidx, gidx, mb in [("唯一MD5全量->出单", 0, 2, 2500), ("唯一MD5_ABM->出单", 1, 3, 80)]:
            r = eval_dim(name, meta["tr"], meta["te"], bidx, gidx, mb)
            if r:
                r["漏斗"] = funnel; rows.append(r)
    df = pd.DataFrame(rows).sort_values(["漏斗", "综合分"], ascending=[True, False])
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    stat = pd.DataFrame([{"总行数": total, "唯一MD5": len(md5_meta), "重复行": dup, "出单出现MD5": post_seen, "出单且ABM入口MD5": abm_post}])
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        stat.to_excel(w, sheet_name="口径核对", index=False)
        df.to_excel(w, sheet_name="唯一MD5评分", index=False)
    L = ["# 96992 唯一 MD5 维度复核\n", stat.to_markdown(index=False)]
    for funnel in ["唯一MD5全量->出单", "唯一MD5_ABM->出单"]:
        L.append(f"\n## {funnel}")
        L.append(df[df["漏斗"] == funnel].head(8).to_markdown(index=False))
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(stat.to_string(index=False), flush=True)
    print(f"wrote: {OUT_XLSX}", flush=True)
    print(f"wrote: {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
