#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""99859媒体下单(2): 拆分、MD5解密、手机号维度富集、出单率归因与策略表生成。"""

from __future__ import annotations

import concurrent.futures as futures
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

sys.path.insert(0, "/Users/a1/Library/Python/3.9/lib/python/site-packages")
sys.path.insert(0, "/Users/a1/Desktop/地址采集画像分析/sh")

from operator_segments import classify_operator  # noqa: E402
from phone import Phone  # noqa: E402


SOURCE = Path("/Users/a1/Desktop/_99859媒体下单 (2).xlsx")
OUT_DIR = Path("/Users/a1/Desktop/99859媒体下单2_出单率归因分析")
DESKTOP = Path("/Users/a1/Desktop")
ENDPOINT = "https://phone-qery-tuia-urhchdtcec.cn-hangzhou.fcapp.run/query_phones"
BATCH_SIZE = 800
MAX_WORKERS = 5
TIMEOUT = 60
PERIODS = ("5月", "6月后")
MERCHANTS = (2623, 2690)


REGION = {
    "北京": "华北", "天津": "华北", "河北": "华北", "山西": "华北", "内蒙古": "华北",
    "辽宁": "东北", "吉林": "东北", "黑龙江": "东北",
    "上海": "华东", "江苏": "华东", "浙江": "华东", "安徽": "华东", "福建": "华东", "江西": "华东", "山东": "华东",
    "河南": "华中", "湖北": "华中", "湖南": "华中",
    "广东": "华南", "广西": "华南", "海南": "华南",
    "重庆": "西南", "四川": "西南", "贵州": "西南", "云南": "西南", "西藏": "西南",
    "陕西": "西北", "甘肃": "西北", "青海": "西北", "宁夏": "西北", "新疆": "西北",
}
COASTAL = {"辽宁", "河北", "天津", "山东", "江苏", "上海", "浙江", "福建", "广东", "广西", "海南"}
NORTH = {
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "山东", "河南", "陕西", "甘肃", "青海", "宁夏", "新疆",
}
CLUSTER = {
    "北京": "京津冀", "天津": "京津冀", "河北": "京津冀",
    "上海": "长三角", "江苏": "长三角", "浙江": "长三角", "安徽": "长三角",
    "广东": "珠三角", "重庆": "成渝", "四川": "成渝",
    "湖北": "长江中游", "湖南": "长江中游", "江西": "长江中游",
    "河南": "中原", "山东": "山东半岛", "福建": "海西",
}
ECON = {
    "北京": "T1高线", "上海": "T1高线", "天津": "T1高线", "江苏": "T1高线",
    "浙江": "T1高线", "广东": "T1高线", "福建": "T1高线",
    "山东": "T2中线", "重庆": "T2中线", "湖北": "T2中线", "辽宁": "T2中线",
    "内蒙古": "T2中线", "陕西": "T2中线", "湖南": "T2中线", "四川": "T2中线",
    "安徽": "T2中线", "河北": "T2中线", "江西": "T2中线", "河南": "T2中线",
}
GEN_LABEL = {
    "13": "13x · 最早(99-03)",
    "15": "15x · 06+",
    "18": "18x · 08+(3G)",
    "17": "17x · 13+(含虚拟)",
    "16": "16x · 17+(虚拟为主)",
    "19": "19x · 18+(新号)",
    "14": "14x · 上网/物联",
}
VIRT3 = {"170", "171", "162", "165", "167"}
IOT3 = {"140", "141", "142", "143", "144"}
FRONT_GENERALIZED = {
    ("广西", "中国电信"),
    ("贵州", "中国联通"),
    ("重庆", "中国移动"),
    ("江西", "中国电信"),
    ("江苏", "中国电信"),
    ("四川", "中国联通"),
    ("重庆", "中国联通"),
}


def is_md5(value: object) -> bool:
    s = str(value).strip()
    return bool(re.fullmatch(r"[0-9a-fA-F]{32}", s))


def is_phone(value: object) -> bool:
    s = str(value).strip()
    return bool(re.fullmatch(r"1\d{10}", s))


def pct(n: float, d: float) -> float:
    return float(n) / float(d) if d else 0.0


def pp(n: float) -> float:
    return n * 100.0


def read_source() -> pd.DataFrame:
    df = pd.read_excel(SOURCE)
    df["下单日期"] = pd.to_datetime(df["下单日期"], errors="coerce")
    df["是否出单"] = pd.to_numeric(df["是否出单"], errors="coerce").fillna(0).astype(int)
    df["是否签收"] = pd.to_numeric(df["是否签收"], errors="coerce").fillna(0).astype(int)
    df["商家ID"] = pd.to_numeric(df["商家ID"], errors="coerce").astype("Int64")
    may = (df["下单日期"] >= pd.Timestamp("2026-05-01")) & (df["下单日期"] <= pd.Timestamp("2026-06-01 23:59:59"))
    jun = df["下单日期"] >= pd.Timestamp("2026-06-02")
    df["月份段"] = np.select([may, jun], ["5月", "6月后"], default="其他")
    df["来源文件"] = df.apply(
        lambda r: split_filename(r["商家ID"], r["月份段"]) if r["月份段"] in PERIODS and pd.notna(r["商家ID"]) else "其他",
        axis=1,
    )
    return df


def split_filename(merchant: int, period: str) -> str:
    if int(merchant) == 2623 and period == "5月":
        return "2623最新五月数据.csv"
    if int(merchant) == 2623 and period == "6月后":
        return "2623最新版六月后数据.csv"
    if int(merchant) == 2690 and period == "5月":
        return "2690最新版五月数据.csv"
    if int(merchant) == 2690 and period == "6月后":
        return "2690最新版六月后数据.csv"
    raise ValueError(f"unknown split: {merchant=} {period=}")


def load_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    cache_df = pd.read_csv(path, dtype=str)
    if not {"md5手机号", "手机号"}.issubset(cache_df.columns):
        return {}
    return dict(zip(cache_df["md5手机号"].str.lower(), cache_df["手机号"].fillna("")))


def parse_results(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("results", payload.get("data", payload))
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                phone = v.get("phone") or v.get("mobile") or v.get("手机号") or v.get("value")
            else:
                phone = v
            if is_phone(phone):
                out[str(k).lower()] = str(phone)
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("md5") or item.get("md5手机号") or item.get("key")
            phone = item.get("phone") or item.get("mobile") or item.get("手机号")
            if key and is_phone(phone):
                out[str(key).lower()] = str(phone)
    return out


def decrypt_batch(batch: list[str]) -> dict[str, str]:
    for attempt in range(1, 4):
        try:
            resp = requests.post(ENDPOINT, json={"phone_md5_list": batch}, timeout=TIMEOUT)
            resp.raise_for_status()
            return parse_results(resp.json())
        except Exception:
            if attempt == 3:
                return {}
            time.sleep(1.5 * attempt)
    return {}


def decrypt_md5(md5_values: list[str], out_dir: Path) -> dict[str, str]:
    cache_path = out_dir / "decrypt_cache.csv"
    mapping = load_cache(cache_path)
    missing = [m for m in md5_values if m not in mapping or not is_phone(mapping.get(m))]
    print(f"需要解密唯一MD5: {len(md5_values):,}; 缓存命中: {len(md5_values) - len(missing):,}; 待请求: {len(missing):,}", flush=True)
    if missing:
        batches = [missing[i:i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
        done = 0
        with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            future_map = {ex.submit(decrypt_batch, b): b for b in batches}
            for fut in futures.as_completed(future_map):
                result = fut.result()
                mapping.update(result)
                done += 1
                if done == 1 or done % 20 == 0 or done == len(batches):
                    print(f"解密进度: {done}/{len(batches)} 批, 已解出 {sum(1 for v in mapping.values() if is_phone(v)):,}", flush=True)
        pd.DataFrame(
            [{"md5手机号": k, "手机号": v} for k, v in sorted(mapping.items())]
        ).to_csv(cache_path, index=False, encoding="utf-8-sig")
    return mapping


def generation(ph: str) -> str:
    return GEN_LABEL.get(ph[:2], "其他号段") if is_phone(ph) else "其他号段"


def number_type(ph: str) -> str:
    if not is_phone(ph):
        return "异常/非标"
    p3 = ph[:3]
    if p3 in IOT3:
        return "物联网卡"
    if p3 in VIRT3:
        return "虚拟号(MVNO)"
    return "普通号(三大+广电)"


def lucky_level(ph: str) -> str:
    if not is_phone(ph):
        return "普通号"
    last4, last3, last5 = ph[7:], ph[8:], ph[6:]
    def seq(s: str) -> bool:
        return all(ord(s[i + 1]) - ord(s[i]) == 1 for i in range(len(s) - 1)) or all(
            ord(s[i]) - ord(s[i + 1]) == 1 for i in range(len(s) - 1)
        )
    if len(set(last4)) == 1 or len(set(last5)) == 1 or seq(last4):
        return "强靓号"
    if len(set(last3)) == 1:
        return "次靓号"
    if last4[0] == last4[1] and last4[2] == last4[3] and last4[0] != last4[2]:
        return "次靓号"
    if last4[0] == last4[2] and last4[1] == last4[3] and last4[0] != last4[1]:
        return "次靓号"
    return "普通号"


def enrich(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    ph = Phone()
    geo_cache: dict[str, tuple[str, str]] = {}

    def plain(v: object) -> str:
        s = str(v).strip()
        if is_phone(s):
            return s
        return mapping.get(s.lower(), "")

    def geo(phone: str) -> tuple[str, str]:
        if not is_phone(phone):
            return ("未知", "未知")
        key = phone[:7]
        if key not in geo_cache:
            try:
                r = ph.find(phone) or {}
                geo_cache[key] = (r.get("province") or "未知", r.get("city") or "未知")
            except Exception:
                geo_cache[key] = ("未知", "未知")
        return geo_cache[key]

    out = df.copy()
    out["手机号"] = out["md5手机号"].map(plain)
    out["明文手机号"] = out["手机号"]
    out["明文覆盖"] = out["手机号"].map(lambda x: 1 if is_phone(x) else 0)
    out["前3位号段"] = out["手机号"].map(lambda x: x[:3] if is_phone(x) else "未知")
    out["前7位归属段"] = out["手机号"].map(lambda x: x[:7] if is_phone(x) else "未知")
    out["运营商"] = out["手机号"].map(classify_operator)
    out["号段世代"] = out["手机号"].map(generation)
    out["号码类型"] = out["手机号"].map(number_type)
    out["靓号等级"] = out["手机号"].map(lucky_level)
    geos = out["手机号"].map(geo)
    out["省份"] = geos.map(lambda x: x[0])
    out["城市"] = geos.map(lambda x: x[1])
    out["省市"] = out["省份"] + "·" + out["城市"]
    out["大区"] = out["省份"].map(lambda x: REGION.get(x, "其他/未知"))
    out["沿海内地"] = out["省份"].map(lambda x: "沿海省" if x in COASTAL else ("内地省" if x in REGION else "未知"))
    out["南北方"] = out["省份"].map(lambda x: "北方" if x in NORTH else ("南方" if x in REGION else "未知"))
    out["经济层"] = out["省份"].map(lambda x: ECON.get(x, "T3下沉") if x in REGION else "未知")
    out["城市群"] = out["省份"].map(lambda x: CLUSTER.get(x, "其他城市群"))
    return out


def aggregate(data: pd.DataFrame, dims: list[str]) -> pd.DataFrame:
    group_cols = dims if dims else ["__all__"]
    tmp = data.copy()
    if not dims:
        tmp["__all__"] = "整体"
    g = (
        tmp.groupby(group_cols, dropna=False)
        .agg(
            下单量=("md5手机号", "size"),
            出单数=("是否出单", "sum"),
            签收数=("是否签收", "sum"),
            明文手机号数=("明文覆盖", "sum"),
        )
        .reset_index()
    )
    g["出单率"] = g["出单数"] / g["下单量"].replace(0, np.nan)
    g["签收率"] = g["签收数"] / g["出单数"].replace(0, np.nan)
    g["签收下单率"] = g["签收数"] / g["下单量"].replace(0, np.nan)
    g["明文覆盖率"] = g["明文手机号数"] / g["下单量"].replace(0, np.nan)
    return g.fillna(0)


def compare_wide(data: pd.DataFrame, dims: list[str]) -> pd.DataFrame:
    left = aggregate(data[data["月份段"] == "5月"], dims).rename(
        columns={c: f"5月_{c}" for c in ["下单量", "出单数", "签收数", "明文手机号数", "出单率", "签收率", "签收下单率", "明文覆盖率"]}
    )
    right = aggregate(data[data["月份段"] == "6月后"], dims).rename(
        columns={c: f"6月后_{c}" for c in ["下单量", "出单数", "签收数", "明文手机号数", "出单率", "签收率", "签收下单率", "明文覆盖率"]}
    )
    out = left.merge(right, on=dims if dims else ["__all__"], how="outer").fillna(0)
    total5 = out["5月_下单量"].sum()
    total6 = out["6月后_下单量"].sum()
    out["出单率变化_pp"] = (out["6月后_出单率"] - out["5月_出单率"]) * 100
    out["出单率变化率"] = np.where(out["5月_出单率"] > 0, out["6月后_出单率"] / out["5月_出单率"] - 1, 0)
    out["签收率变化_pp"] = (out["6月后_签收率"] - out["5月_签收率"]) * 100
    out["下单量变化"] = out["6月后_下单量"] - out["5月_下单量"]
    out["5月_下单占比"] = out["5月_下单量"] / total5 if total5 else 0
    out["6月后_下单占比"] = out["6月后_下单量"] / total6 if total6 else 0
    out["下单占比变化_pp"] = (out["6月后_下单占比"] - out["5月_下单占比"]) * 100
    return out


def attribution(comp: pd.DataFrame, dims: list[str], scope_cols: list[str] | None = None) -> pd.DataFrame:
    scope_cols = scope_cols or []
    rows = []
    if scope_cols:
        grouped = comp.groupby(scope_cols, dropna=False)
    else:
        grouped = [("整体", comp)]
    for scope_key, part in grouped:
        p = part.copy()
        total5 = p["5月_下单量"].sum()
        total6 = p["6月后_下单量"].sum()
        if not total5 or not total6:
            continue
        p["结构变化贡献_pp"] = (p["6月后_下单量"] / total6 - p["5月_下单量"] / total5) * p["5月_出单率"] * 100
        p["段内转化贡献_pp"] = (p["6月后_下单量"] / total6) * (p["6月后_出单率"] - p["5月_出单率"]) * 100
        p["总贡献_pp"] = p["结构变化贡献_pp"] + p["段内转化贡献_pp"]
        p["影响方向"] = np.where(p["总贡献_pp"] >= 0, "拉升", "拉低")
        p["主要原因"] = np.where(
            p["段内转化贡献_pp"].abs() >= p["结构变化贡献_pp"].abs(),
            "同维度出单率变化",
            "下单结构占比变化",
        )
        rows.append(p)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def information_value(data: pd.DataFrame, dims: list[str], min_base: int) -> tuple[float, float, int]:
    g = aggregate(data, dims)
    g = g[g["下单量"] >= min_base].copy()
    if g.empty:
        return 0.0, 0.0, 0
    good = g["出单数"] + 0.5
    bad = (g["下单量"] - g["出单数"]) + 0.5
    gd = good / good.sum()
    bd = bad / bad.sum()
    iv = ((gd - bd) * np.log(gd / bd)).sum()
    coverage = g["下单量"].sum() / len(data) if len(data) else 0.0
    return float(iv), float(coverage), int(len(g))


def oos_for(data: pd.DataFrame, dims: list[str], label: str, min_base: int, lift_threshold: float = 1.12) -> dict[str, object]:
    if len(data) < min_base * 4 or data["是否出单"].sum() == 0:
        return {}
    seeds = [11, 29, 47, 83, 101]
    values = []
    train_values = []
    coverages = []
    group_counts = []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        mask = rng.random(len(data)) < 0.5
        train = data.loc[mask]
        test = data.loc[~mask]
        base_train = train["是否出单"].mean()
        base_test = test["是否出单"].mean()
        if base_train == 0 or base_test == 0:
            continue
        g = aggregate(train, dims)
        g["train_lift"] = g["出单率"] / base_train
        s = g[(g["下单量"] >= min_base) & (g["train_lift"] >= lift_threshold)].copy()
        if s.empty:
            continue
        keys = set(tuple(row[d] for d in dims) for _, row in s.iterrows())
        test_key = test[dims].apply(lambda r: tuple(r.values.tolist()), axis=1)
        hit = test[test_key.isin(keys)]
        if hit.empty:
            continue
        values.append(hit["是否出单"].mean() / base_test)
        train_values.append((s["出单数"].sum() / s["下单量"].sum()) / base_train)
        coverages.append(len(hit) / len(test))
        group_counts.append(len(s))
    iv, iv_cov, iv_groups = information_value(data, dims, min_base)
    if not values:
        return {
            "口径": label,
            "维度": "×".join(dims),
            "min_base": min_base,
            "IV": iv,
            "IV覆盖率": iv_cov,
            "达标组数": iv_groups,
            "train_lift": 0,
            "样本外lift": 0,
            "衰减": 0,
            "测试覆盖率": 0,
            "CV": 0,
            "S组数": 0,
        }
    train_mean = float(np.mean(train_values))
    test_mean = float(np.mean(values))
    return {
        "口径": label,
        "维度": "×".join(dims),
        "min_base": min_base,
        "IV": iv,
        "IV覆盖率": iv_cov,
        "达标组数": iv_groups,
        "train_lift": train_mean,
        "样本外lift": test_mean,
        "衰减": test_mean / train_mean - 1 if train_mean else 0,
        "测试覆盖率": float(np.mean(coverages)),
        "CV": float(np.std(values)),
        "S组数": float(np.mean(group_counts)),
    }


def sample_out_validation(data: pd.DataFrame) -> pd.DataFrame:
    dims_list = [
        ["省份"],
        ["运营商"],
        ["省份", "运营商"],
        ["城市", "运营商"],
        ["城市群", "运营商"],
        ["省份", "运营商", "号段世代"],
        ["城市群", "前3位号段"],
        ["运营商", "前3位号段", "号段世代"],
    ]
    rows = []
    scopes = [("整体", data)]
    for m in MERCHANTS:
        scopes.append((str(m), data[data["商家ID"].astype(str) == str(m)]))
    for label, part in scopes:
        mb = 180 if label == "整体" else 100
        for dims in dims_list:
            row = oos_for(part.reset_index(drop=True), dims, label, mb)
            if row:
                rows.append(row)
    return pd.DataFrame(rows).sort_values(["口径", "样本外lift", "测试覆盖率"], ascending=[True, False, False])


def build_strategy(merchant_comp: pd.DataFrame, merchant_month: pd.DataFrame, overall_comp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    mbase = {}
    for _, r in merchant_month.iterrows():
        if r["月份段"] == "6月后":
            mbase[int(r["商家ID"])] = r["出单率"]
    strat = merchant_comp.copy()
    strat["前转泛化命中"] = strat.apply(lambda r: (r["省份"], r["运营商"]) in FRONT_GENERALIZED, axis=1)
    strat["商家6月后lift"] = strat.apply(lambda r: pct(r["6月后_出单率"], mbase.get(int(r["商家ID"]), 0)), axis=1)
    strat["稳定性"] = strat["6月后_出单率"] >= strat["5月_出单率"] * 0.8
    conditions = [
        (strat["6月后_下单量"] >= 200) & (strat["商家6月后lift"] >= 1.25) & strat["稳定性"] & strat["前转泛化命中"],
        (strat["6月后_下单量"] >= 200) & (strat["商家6月后lift"] >= 1.20) & strat["稳定性"],
        (strat["6月后_下单量"] >= 200) & (strat["商家6月后lift"] >= 1.10) & strat["稳定性"] & strat["前转泛化命中"],
        (strat["6月后_下单量"] >= 200) & (strat["商家6月后lift"] < 0.85),
    ]
    choices = ["S双验证扩量", "A出单强扩量", "B泛化小量", "D缩量剔除"]
    strat["策略层级"] = np.select(conditions, choices, default="C观察")
    strat["建议动作"] = strat["策略层级"].map({
        "S双验证扩量": "优先扩量，预算和名单放大",
        "A出单强扩量": "按出单表现扩量，保持日监控",
        "B泛化小量": "小流量泛化验证，达标再放大",
        "C观察": "保量观察，不作为主扩量规则",
        "D缩量剔除": "缩量或暂停，避免继续稀释",
    })
    strat = strat.sort_values(["商家ID", "策略层级", "商家6月后lift", "6月后_下单量"], ascending=[True, True, False, False])

    support = (
        strat[strat["策略层级"].isin(["S双验证扩量", "A出单强扩量", "B泛化小量"])]
        .groupby(["省份", "运营商"])["商家ID"]
        .apply(lambda x: ",".join(map(str, sorted(set(map(int, x))))))
        .rename("支持扩量商家")
        .reset_index()
    )
    bad = (
        strat[strat["策略层级"].eq("D缩量剔除")]
        .groupby(["省份", "运营商"])["商家ID"]
        .apply(lambda x: ",".join(map(str, sorted(set(map(int, x))))))
        .rename("缩量商家")
        .reset_index()
    )
    unified = overall_comp.merge(support, on=["省份", "运营商"], how="left").merge(bad, on=["省份", "运营商"], how="left")
    unified["支持扩量商家"] = unified["支持扩量商家"].fillna("")
    unified["缩量商家"] = unified["缩量商家"].fillna("")
    unified["支持商家数"] = unified["支持扩量商家"].map(lambda s: len([x for x in str(s).split(",") if x]))
    unified["缩量商家数"] = unified["缩量商家"].map(lambda s: len([x for x in str(s).split(",") if x]))
    base6 = data_rate = overall_comp["6月后_出单数"].sum() / overall_comp["6月后_下单量"].sum()
    unified["整体6月后lift"] = unified["6月后_出单率"] / base6 if data_rate else 0
    unified["稳定性"] = unified["6月后_出单率"] >= unified["5月_出单率"] * 0.8
    unified["前转泛化命中"] = unified.apply(lambda r: (r["省份"], r["运营商"]) in FRONT_GENERALIZED, axis=1)
    uconds = [
        (unified["6月后_下单量"] >= 300) & (unified["支持商家数"] >= 2) & (unified["整体6月后lift"] >= 1.12) & unified["稳定性"],
        (unified["6月后_下单量"] >= 300) & (unified["支持商家数"] >= 1) & unified["前转泛化命中"] & (unified["整体6月后lift"] >= 1.08),
        (unified["6月后_下单量"] >= 300) & (unified["支持商家数"] >= 1) & (unified["整体6月后lift"] >= 1.05),
        ((unified["缩量商家数"] >= 2) | ((unified["整体6月后lift"] < 0.85) & (unified["6月后_下单量"] >= 800))),
    ]
    uchoices = ["U0双商家统一扩量", "U1泛化验证扩量", "U2单商家/小量验证", "U4统一缩量剔除"]
    unified["统一策略层"] = np.select(uconds, uchoices, default="U3观察保量")
    unified["统一动作"] = unified["统一策略层"].map({
        "U0双商家统一扩量": "两个商家共同验证，作为统一放量主名单",
        "U1泛化验证扩量": "前转泛化也命中，跨商家小步扩量",
        "U2单商家/小量验证": "先跟随命中商家投放，另一个商家低量验证",
        "U3观察保量": "保量观察，等下一周期复核",
        "U4统一缩量剔除": "统一缩量或剔除",
    })
    unified = unified.sort_values(["统一策略层", "整体6月后lift", "6月后_下单量"], ascending=[True, False, False])
    return strat, unified


def save_csv(name: str, df: pd.DataFrame) -> Path:
    path = OUT_DIR / name
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def table_overview(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    by_file = aggregate(data, ["来源文件"])
    by_file = by_file[["来源文件", "下单量", "出单数", "签收数", "出单率", "签收率"]]
    total = pd.DataFrame([{
        "来源文件": "合计",
        "下单量": by_file["下单量"].sum(),
        "出单数": by_file["出单数"].sum(),
        "签收数": by_file["签收数"].sum(),
    }])
    total["出单率"] = total["出单数"] / total["下单量"]
    total["签收率"] = total["签收数"] / total["出单数"]
    by_file = pd.concat([by_file.sort_values("来源文件"), total], ignore_index=True)

    mcomp = compare_wide(data, ["商家ID"])
    table = pd.DataFrame({
        "商家ID": mcomp["商家ID"],
        "五月下单量": mcomp["5月_下单量"].astype(int),
        "五月出单数": mcomp["5月_出单数"].astype(int),
        "五月出单率": mcomp["5月_出单率"],
        "六月后下单量": mcomp["6月后_下单量"].astype(int),
        "六月后出单数": mcomp["6月后_出单数"].astype(int),
        "六月后出单率": mcomp["6月后_出单率"],
        "出单率变化率": mcomp["出单率变化率"],
        "五月出单后签收率": mcomp["5月_签收率"],
        "六月后出单后签收率": mcomp["6月后_签收率"],
    }).sort_values("商家ID")
    return by_file, table


def style_workbook(path: Path) -> None:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    green = PatternFill("solid", fgColor="D9EAD3")
    yellow = PatternFill("solid", fgColor="FFFF00")
    thin = Side(style="thin", color="333333")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for row in ws.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="center")
                cell.border = border
        for cell in ws[1]:
            cell.fill = green
            cell.font = Font(bold=True)
        for col_idx, column_cells in enumerate(ws.columns, start=1):
            max_len = min(max(len(str(c.value)) if c.value is not None else 0 for c in column_cells[:200]) + 2, 36)
            ws.column_dimensions[get_column_letter(col_idx)].width = max(max_len, 10)
        for row in ws.iter_rows(min_row=1, max_row=1):
            for c in row:
                if "变化率" in str(c.value):
                    c.fill = yellow
        if ws.title == "00_总体数据表":
            for row_idx in (1, 8):
                for c in ws[row_idx]:
                    c.fill = green
                    c.font = Font(bold=True)
            for c in ws[8]:
                if str(c.value) == "出单率变化率":
                    c.fill = yellow
    wb.save(path)


def write_excel(tables: dict[str, pd.DataFrame], overview1: pd.DataFrame, overview2: pd.DataFrame) -> Path:
    path = OUT_DIR / "99859最新版_完整分析表.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        overview1.to_excel(writer, sheet_name="00_总体数据表", index=False, startrow=0)
        overview2.to_excel(writer, sheet_name="00_总体数据表", index=False, startrow=7)
        for name, df in tables.items():
            sheet = name[:31]
            df.to_excel(writer, sheet_name=sheet, index=False)
    style_workbook(path)
    return path


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def top_markdown(df: pd.DataFrame, cols: list[str], n: int = 8) -> str:
    if df.empty:
        return "无\n"
    return markdown_table(df[cols].head(n))


def markdown_table(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return df.to_csv(index=False)


def write_report(
    overview1: pd.DataFrame,
    overview2: pd.DataFrame,
    overall_month: pd.DataFrame,
    merchant_month: pd.DataFrame,
    att_all: pd.DataFrame,
    att_merch: pd.DataFrame,
    oos: pd.DataFrame,
    strategy: pd.DataFrame,
    unified: pd.DataFrame,
    unmatched_count: int,
) -> Path:
    path = OUT_DIR / "99859最新版_结论与统一策略.md"
    overall_comp = compare_wide(DATA, [])
    r = overall_comp.iloc[0]
    neg = att_all.sort_values("总贡献_pp").head(10)
    pos = att_all.sort_values("总贡献_pp", ascending=False).head(10)
    u0 = unified[unified["统一策略层"].isin(["U0双商家统一扩量", "U1泛化验证扩量", "U2单商家/小量验证"])]
    u4 = unified[unified["统一策略层"].eq("U4统一缩量剔除")]

    lines = []
    A = lines.append
    A("# 99859媒体下单(2) 出单率变化归因与统一投放策略\n")
    A("## 一、总体结论\n")
    A(f"- 分析口径：仅看 `2026-05-01` 至 `2026-06-01` 的 5月段，以及 `2026-06-02` 至数据最大日期的 6月后段；商家ID为 2623、2690。")
    A(f"- 整体出单率从 **{fmt_pct(r['5月_出单率'])}** 降至 **{fmt_pct(r['6月后_出单率'])}**，变化 **{r['出单率变化_pp']:.2f}pp**，相对变化 **{r['出单率变化率']*100:.2f}%**。")
    A(f"- 2623：{fmt_pct(float(overview2.loc[overview2['商家ID'].astype(str)=='2623','五月出单率'].iloc[0]))} → {fmt_pct(float(overview2.loc[overview2['商家ID'].astype(str)=='2623','六月后出单率'].iloc[0]))}；2690：{fmt_pct(float(overview2.loc[overview2['商家ID'].astype(str)=='2690','五月出单率'].iloc[0]))} → {fmt_pct(float(overview2.loc[overview2['商家ID'].astype(str)=='2690','六月后出单率'].iloc[0]))}。两个商家同向下滑，说明不是单商家偶发。")
    A(f"- 明文解密未覆盖 md5 数：{unmatched_count:,}。分析维度表已保留 `明文覆盖率`，未解出的号码不会被错误归属到某个地域/运营商。")
    A("\n## 二、总体数据表\n")
    A(markdown_table(overview1))
    A("\n")
    A(markdown_table(overview2))
    A("\n\n## 三、运营商×地域变化原因\n")
    A("### 拉低整体出单率的主要组合\n")
    A(top_markdown(neg, ["省份", "运营商", "5月_下单量", "6月后_下单量", "5月_出单率", "6月后_出单率", "出单率变化_pp", "总贡献_pp", "主要原因"], 10))
    A("\n\n### 拉升或抵消下滑的主要组合\n")
    A(top_markdown(pos, ["省份", "运营商", "5月_下单量", "6月后_下单量", "5月_出单率", "6月后_出单率", "出单率变化_pp", "总贡献_pp", "主要原因"], 10))
    A("\n\n## 四、样本外验证\n")
    A("按 skill 要求，维度不只看样本内 IV；这里用 50/50 多 seed 样本外 lift 复核。优先选择样本外 lift 稳、覆盖率不塌的低维组合。")
    A(top_markdown(oos, ["口径", "维度", "IV", "IV覆盖率", "train_lift", "样本外lift", "衰减", "测试覆盖率", "CV"], 18))
    A("\n\n## 五、分商家策略\n")
    for m in MERCHANTS:
        sub = strategy[strategy["商家ID"].astype(str) == str(m)]
        A(f"\n### 商家 {m}")
        A(top_markdown(sub[sub["策略层级"].isin(["S双验证扩量", "A出单强扩量", "B泛化小量"])], ["省份", "运营商", "6月后_下单量", "6月后_出单率", "商家6月后lift", "前转泛化命中", "策略层级", "建议动作"], 12))
        shrink = sub[sub["策略层级"].eq("D缩量剔除")]
        if not shrink.empty:
            A("\n需要缩量/剔除：")
            A(top_markdown(shrink, ["省份", "运营商", "6月后_下单量", "6月后_出单率", "商家6月后lift", "策略层级"], 8))
    A("\n\n## 六、结合两个商家的统一策略\n")
    A("- 统一扩量只选 `省份×运营商` 这类低维、可落地组合；三维号段只作为风控复核，不作为直接大规模扩散主规则。")
    A("- 两个商家共同支持的组合优先进入 U0；只有单商家支持的进入 U2，小量验证另一个商家；两个商家都弱或整体 lift 低的进入 U4。")
    A("\n### 统一扩量/验证名单\n")
    A(top_markdown(u0, ["省份", "运营商", "6月后_下单量", "6月后_出单率", "整体6月后lift", "支持扩量商家", "统一策略层", "统一动作"], 20))
    if not u4.empty:
        A("\n### 统一缩量/剔除名单\n")
        A(top_markdown(u4, ["省份", "运营商", "6月后_下单量", "6月后_出单率", "整体6月后lift", "缩量商家", "统一策略层"], 15))
    A("\n\n## 七、文件说明\n")
    A("- `99859最新版_完整分析表.xlsx`：所有汇总表和策略表。")
    A("- `明细_富集后数据.csv`：解密并富集后的明细。")
    A("- `13_分商家策略_省份运营商.csv`、`14_统一策略_省份运营商.csv`：可直接用于投放调控。")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    global DATA
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = read_source()
    target = df[df["月份段"].isin(PERIODS) & df["商家ID"].isin(MERCHANTS)].copy()
    target["商家ID"] = target["商家ID"].astype(int)
    md5s = sorted({str(x).strip().lower() for x in target["md5手机号"] if is_md5(x)})
    mapping = decrypt_md5(md5s, OUT_DIR)
    DATA = enrich(target, mapping)
    unmatched = DATA[(DATA["md5手机号"].map(is_md5)) & (DATA["明文覆盖"] == 0)]
    if not unmatched.empty:
        unmatched[["md5手机号", "商家ID", "月份段", "来源文件"]].to_csv(OUT_DIR / "未解出MD5清单.csv", index=False, encoding="utf-8-sig")

    split_paths = []
    for m in MERCHANTS:
        for p in PERIODS:
            name = split_filename(m, p)
            part = DATA[(DATA["商家ID"] == m) & (DATA["月份段"] == p)].copy()
            part.to_csv(OUT_DIR / name, index=False, encoding="utf-8-sig")
            part.to_csv(DESKTOP / name, index=False, encoding="utf-8-sig")
            split_paths.append(str(OUT_DIR / name))

    overview1, overview2 = table_overview(DATA)
    data_overview = pd.DataFrame([{
        "源文件": str(SOURCE),
        "源文件总行数": len(df),
        "纳入分析行数": len(DATA),
        "源文件日期最小值": str(df["下单日期"].min().date()),
        "源文件日期最大值": str(df["下单日期"].max().date()),
        "明文覆盖率": DATA["明文覆盖"].mean(),
        "未解出MD5数": int(len(unmatched)),
    }])
    overall_month = aggregate(DATA, ["月份段"]).sort_values("月份段")
    merchant_month = aggregate(DATA, ["商家ID", "月份段"]).sort_values(["商家ID", "月份段"])
    merchant_op_prov_month = aggregate(DATA, ["商家ID", "运营商", "省份", "月份段"]).sort_values(["商家ID", "运营商", "省份", "月份段"])
    merchant_op_city_month = aggregate(DATA, ["商家ID", "运营商", "省份", "城市", "月份段"]).sort_values(["商家ID", "运营商", "省份", "城市", "月份段"])
    merchant_op_prov_comp = compare_wide(DATA, ["商家ID", "省份", "运营商"]).sort_values(["商家ID", "出单率变化_pp"])
    merchant_op_city_comp = compare_wide(DATA, ["商家ID", "省份", "城市", "运营商"]).sort_values(["商家ID", "出单率变化_pp"])
    overall_op_prov_comp = compare_wide(DATA, ["省份", "运营商"]).sort_values("出单率变化_pp")
    att_all = attribution(overall_op_prov_comp, ["省份", "运营商"]).sort_values("总贡献_pp")
    att_merch = attribution(merchant_op_prov_comp, ["商家ID", "省份", "运营商"], scope_cols=["商家ID"]).sort_values(["商家ID", "总贡献_pp"])
    oos = sample_out_validation(DATA)
    strategy, unified = build_strategy(merchant_op_prov_comp, merchant_month, overall_op_prov_comp)
    number_month = aggregate(DATA, ["月份段", "运营商", "号段世代", "号码类型", "经济层", "城市群"]).sort_values(["月份段", "运营商", "号段世代"])

    tables = {
        "00_数据概览": data_overview,
        "01_总体数据表_来源文件": overview1,
        "02_总体数据表_商家变化": overview2,
        "03_整体_月份表现": overall_month,
        "04_商家_月份表现": merchant_month,
        "05_商家_运营商省份_月份": merchant_op_prov_month,
        "06_商家_运营商城市_月份": merchant_op_city_month,
        "07_商家_运营商省份_对比": merchant_op_prov_comp,
        "08_商家_运营商城市_对比": merchant_op_city_comp,
        "09_整体_运营商省份_对比": overall_op_prov_comp,
        "10_归因_整体省份运营商": att_all,
        "11_归因_商家省份运营商": att_merch,
        "12_样本外验证": oos,
        "13_分商家策略_省份运营商": strategy,
        "14_统一策略_省份运营商": unified,
        "15_号码维度_月份": number_month,
    }
    for name, table in tables.items():
        save_csv(f"{name}.csv", table)
    DATA.to_csv(OUT_DIR / "明细_富集后数据.csv", index=False, encoding="utf-8-sig")
    xlsx = write_excel(tables, overview1, overview2)
    md = write_report(overview1, overview2, overall_month, merchant_month, att_all, att_merch, oos, strategy, unified, len(unmatched))
    summary = {
        "source": str(SOURCE),
        "out_dir": str(OUT_DIR),
        "rows_in_source": int(len(df)),
        "rows_analyzed": int(len(DATA)),
        "split_files": split_paths,
        "excel": str(xlsx),
        "report": str(md),
        "unmatched_md5_count": int(len(unmatched)),
        "overall_may_order_rate": float(overall_month.loc[overall_month["月份段"] == "5月", "出单率"].iloc[0]),
        "overall_jun_after_order_rate": float(overall_month.loc[overall_month["月份段"] == "6月后", "出单率"].iloc[0]),
        "unified_strategy_counts": unified["统一策略层"].value_counts().to_dict(),
        "merchant_strategy_counts": [
            {"商家ID": int(k[0]), "策略层级": str(k[1]), "数量": int(v)}
            for k, v in strategy.groupby(["商家ID", "策略层级"]).size().items()
        ],
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    DATA = pd.DataFrame()
    main()
