# -*- coding: utf-8 -*-
"""
维度一:纯手机号拆解 × 四段漏斗 流失轨迹分析(与维度二 DMP 同一套读法)
输入(明文手机号,单列无表头,逐层嵌套漏斗):全量 / 接通 / 接通20秒以上 / 意向ABM
默认目录:~/Desktop/0515-0530/手机号明文/(可用环境变量 SRC 覆盖)

从一个 11 位号码离线拆出:运营商、号段、号段世代(≈发号年代/发号时间段)、
号码类型(普通/虚拟MVNO/物联网)、归属地省、归属地市、靓号/规律号。

对每个属性,追踪它在 全量→接通→接通20s+→意向ABM 四环节的"相对全量指数"
(全量=参照基准100;>100 该环节更富集/流失慢,<100 更流失)+ 趋势,并给精确转化率
(接通率/20s+率/ABM率,= 该属性段自身计数比例,口径硬,非覆盖估算)。

口径与维度二一致:相对指数 = 该环节占比 ÷ 全量占比 ×100 = 该段转化率 ÷ 大盘转化率 ×100。

产物 output/:
  晓黄_号码维度_漏斗交叉.md
  晓黄_号码维度_漏斗交叉.xlsx
缓存:output/.phone_funnel_cache.json(置 REUSE=1 可复用,跳过重扫)
依赖:phone(归属地)、openpyxl(Excel)。号段→运营商用 operator_segments。
"""
import os
import sys
import csv
import glob
import json
from collections import defaultdict

csv.field_size_limit(10 ** 7)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from operator_segments import classify_operator
from phone import Phone

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "output")
SRC = os.environ.get("SRC", os.path.expanduser("~/Desktop/0515-0530/手机号明文"))
DEC_DIR = os.path.join(OUT_DIR, "decrypted")  # 回退数据源(每行一通外呼,带阶段列)
CACHE = os.path.join(OUT_DIR, ".phone_funnel_cache.json")
# 解密 CSV 列(0 基):意向等级/是否接通/是否接通20s/号码明文/运营商/省/市
DC_LVL, DC_CONN, DC_S20, DC_PHONE, DC_OP, DC_PROV, DC_CITY = 4, 5, 6, 7, 8, 9, 10

STAGES = ["全量", "接通", "接通20s+", "意向ABM"]
FILES = {
    "全量": "全量数据_手机号.txt",
    "接通": "接通数据_手机号.txt",
    "接通20s+": "接通20秒以上数据_手机号.txt",
    "意向ABM": "意向为ABM数据_手机号.txt",
}

GEN_LABEL = {
    "13": "13x · 最早(99–03)", "15": "15x · 06+", "18": "18x · 08+(3G)",
    "17": "17x · 13+(含虚拟)", "16": "16x · 17+(虚拟为主)",
    "19": "19x · 18+(新号)", "14": "14x · 上网/物联",
}
GEN_ORDER = ["13x · 最早(99–03)", "15x · 06+", "18x · 08+(3G)", "17x · 13+(含虚拟)",
             "16x · 17+(虚拟为主)", "19x · 18+(新号)", "14x · 上网/物联", "其他号段"]
VIRT3 = {"170", "171", "162", "165", "167"}
IOT3 = {"140", "141", "142", "143", "144"}
NTYPE_ORDER = ["普通号(三大+广电)", "虚拟号(MVNO)", "物联网卡", "异常/非标"]
LUCKY_ORDER = ["强靓号", "次靓号", "普通号"]
OP_ORDER = ["中国移动", "中国联通", "中国电信", "中国广电", "虚拟运营商", "其他/未知号段"]
# 重点速览纳入的维度(排除过细的 号段前3 / 归属地市)
MOVER_DIMS = ["运营商", "号段世代(发号年代)", "号码类型", "归属地省", "靓号/规律号"]
DIMS = ["运营商", "号段世代(发号年代)", "号码类型", "归属地省", "归属地市", "靓号/规律号", "号段(前3)"]

_PH = Phone()
_geo_cache = {}


def geo(ph):
    k = ph[:7]
    v = _geo_cache.get(k)
    if v is None:
        try:
            r = _PH.find(ph)
            v = (r.get("province") or "(未知)", r.get("city") or "(未知)") if r else ("(未知)", "(未知)")
        except Exception:
            v = ("(未知)", "(未知)")
        _geo_cache[k] = v
    return v


def generation(ph):
    return GEN_LABEL.get(ph[:2], "其他号段")


def number_type(ph):
    p3 = ph[:3]
    if p3 in IOT3:
        return "物联网卡"
    if p3 in VIRT3:
        return "虚拟号(MVNO)"
    return "普通号(三大+广电)"


def _is_seq(s):
    asc = all(ord(s[i + 1]) - ord(s[i]) == 1 for i in range(len(s) - 1))
    desc = all(ord(s[i]) - ord(s[i + 1]) == 1 for i in range(len(s) - 1))
    return asc or desc


def lucky_level(ph):
    last4, last3, last5 = ph[7:], ph[8:], ph[6:]
    if len(set(last4)) == 1 or len(set(last5)) == 1 or _is_seq(last4):
        return "强靓号"
    if len(set(last3)) == 1:
        return "次靓号"
    if last4[0] == last4[1] and last4[2] == last4[3] and last4[0] != last4[2]:
        return "次靓号"
    if last4[0] == last4[2] and last4[1] == last4[3] and last4[0] != last4[1]:
        return "次靓号"
    return "普通号"


def pct(n, d):
    return (n / d * 100) if d else 0.0


def newrow():
    return [0, 0, 0, 0]


# ── 轨迹口径 ──────────────────────────────────────────────
def cell(data, dim, k):
    return data[dim].get(k, [0, 0, 0, 0])


def index_at(c, totals, si):
    """相对全量指数 = (该环节占比)/(全量占比)×100。"""
    if c[0] == 0 or totals[si] == 0 or totals[0] == 0:
        return 0.0
    return (c[si] / totals[si]) / (c[0] / totals[0]) * 100


def trend(ic, i2, i3):
    up = ic >= 100 and i2 >= ic and i3 >= i2
    down = ic <= 100 and i2 <= ic and i3 <= i2
    if up and i3 >= 115:
        return "↗ 持续聚集"
    if down and i3 <= 85:
        return "↘ 持续流失"
    if i3 >= 120:
        return "末端聚集🔼"
    if i3 <= 80:
        return "末端流失🔽"
    return "≈ 平稳"


def flag(t):
    return "🔼" if t >= 120 else ("🔽" if t and t < 80 else "")


def aggregate():
    for f in FILES.values():
        p = os.path.join(SRC, f)
        if not os.path.exists(p):
            print(f"✗ 缺文件: {p}")
            sys.exit(1)
    data = {d: defaultdict(newrow) for d in DIMS}
    totals = newrow()
    for si, stage in enumerate(STAGES):
        path = os.path.join(SRC, FILES[stage])
        n = 0
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                ph = line.strip()
                if len(ph) != 11 or not ph.isdigit():
                    if ph:
                        data["号码类型"]["异常/非标"][si] += 1
                        totals[si] += 1
                        n += 1
                    continue
                n += 1
                totals[si] += 1
                prov, city = geo(ph)
                data["运营商"][classify_operator(ph)][si] += 1
                data["号段世代(发号年代)"][generation(ph)][si] += 1
                data["号码类型"][number_type(ph)][si] += 1
                data["归属地省"][prov][si] += 1
                data["归属地市"][f"{prov}·{city}"][si] += 1
                data["靓号/规律号"][lucky_level(ph)][si] += 1
                data["号段(前3)"][ph[:3]][si] += 1
        print(f"  ✓ {stage}: {n:,} 行")
    return data, totals


def _bump_stages(data, totals, si_list, ph, op, prov, city):
    """对该行所属的每个阶段索引累加各维度。"""
    valid = len(ph) == 11 and ph.isdigit()
    gen = generation(ph) if valid else "其他号段"
    nt = number_type(ph) if valid else "异常/非标"
    luck = lucky_level(ph) if valid else None
    seg = ph[:3] if valid else None
    for si in si_list:
        totals[si] += 1
        data["运营商"][op][si] += 1
        data["号段世代(发号年代)"][gen][si] += 1
        data["号码类型"][nt][si] += 1
        data["归属地省"][prov][si] += 1
        data["归属地市"][city][si] += 1
        if luck:
            data["靓号/规律号"][luck][si] += 1
        if seg:
            data["号段(前3)"][seg][si] += 1


def aggregate_from_decrypted():
    """回退:从 output/decrypted/*_明文.csv 聚合(列已含运营商/省/市,无需 phone 查询)。"""
    files = sorted(glob.glob(os.path.join(DEC_DIR, "*_明文.csv")))
    if not files:
        return None, None
    data = {d: defaultdict(newrow) for d in DIMS}
    totals = newrow()
    n = 0
    for fp in files:
        with open(fp, encoding="utf-8-sig", newline="") as f:
            rd = csv.reader(f)
            next(rd, None)
            for row in rd:
                if len(row) <= DC_CITY:
                    continue
                n += 1
                si_list = [0]
                if row[DC_CONN].strip() == "是":
                    si_list.append(1)
                if row[DC_S20].strip() == "是":
                    si_list.append(2)
                if row[DC_LVL].strip().startswith(("A级", "B级", "M级")):
                    si_list.append(3)
                ph = row[DC_PHONE].strip()
                op = row[DC_OP].strip() or "未解密"
                prov = row[DC_PROV].strip() or "(未知)"
                city = f"{prov}·{row[DC_CITY].strip()}" if row[DC_CITY].strip() else "(未知)"
                _bump_stages(data, totals, si_list, ph, op, prov, city)
        print(f"  ✓ {os.path.basename(fp)} 累计 {n:,} 行")
    return data, totals


def main():
    if os.environ.get("REUSE") == "1" and os.path.exists(CACHE):
        obj = json.load(open(CACHE, encoding="utf-8"))
        totals = obj["totals"]
        data = {d: obj["data"].get(d, {}) for d in DIMS}
        print("✓ 复用聚合缓存(REUSE=1)")
    elif all(os.path.exists(os.path.join(SRC, f)) for f in FILES.values()):
        print(f"✓ 数据源:明文手机号四段文件 {SRC}")
        data, totals = aggregate()
        json.dump({"totals": totals, "data": {d: dict(data[d]) for d in DIMS}},
                  open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        print("✓ 已写聚合缓存")
    elif glob.glob(os.path.join(DEC_DIR, "*_明文.csv")):
        print(f"⚠ 未找到明文四段文件,回退使用解密数据 {DEC_DIR}(按阶段列还原漏斗)")
        data, totals = aggregate_from_decrypted()
        json.dump({"totals": totals, "data": {d: dict(data[d]) for d in DIMS}},
                  open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        print("✓ 已写聚合缓存")
    else:
        print(f"✗ 既无明文四段文件({SRC}),也无解密数据({DEC_DIR})")
        sys.exit(1)
    write_md(data, totals)
    write_xlsx(data, totals)
    print(f"\n✓ 报告: {os.path.join(OUT_DIR, '晓黄_号码维度_漏斗交叉.md')}")
    print(f"✓ Excel: {os.path.join(OUT_DIR, '晓黄_号码维度_漏斗交叉.xlsx')}")


def _order_for(dim, d):
    if dim == "运营商":
        return [k for k in OP_ORDER if k in d] + [k for k in d if k not in OP_ORDER]
    if dim == "号段世代(发号年代)":
        return [k for k in GEN_ORDER if k in d]
    if dim == "号码类型":
        return [k for k in NTYPE_ORDER if k in d]
    if dim == "靓号/规律号":
        return [k for k in LUCKY_ORDER if k in d]
    return sorted(d, key=lambda k: -d[k][0])


def _movers(data, totals, min_full_share=0.01):
    rows = []
    for dim in MOVER_DIMS:
        for k, c in data[dim].items():
            if k in ("(未知)", "异常/非标") or c[0] == 0:
                continue
            if c[0] / totals[0] < min_full_share:
                continue
            ic = index_at(c, totals, 1)
            i2 = index_at(c, totals, 2)
            i3 = index_at(c, totals, 3)
            rows.append((dim, k, c, ic, i2, i3))
    rows.sort(key=lambda x: -x[5])
    return rows


# ---------- Markdown ----------
def write_md(data, totals):
    L = []
    A = L.append
    A("# 晓黄 号码维度 × 四段漏斗 流失轨迹分析(维度一:纯手机号拆解)\n")
    A(f"> 数据:0515–0530 明文手机号四段漏斗 · 全量 {totals[0]:,} → 接通 {totals[1]:,} "
      f"→ 20s+ {totals[2]:,} → 意向ABM {totals[3]:,}")
    A(f"> 大盘留存参照:接通率 **{pct(totals[1],totals[0]):.2f}%** · 20s+率 **{pct(totals[2],totals[0]):.2f}%** "
      f"· 全量→ABM率 **{pct(totals[3],totals[0]):.3f}%**")
    A("\n> **相对指数** = 该环节占比 ÷ 全量占比 ×100(全量=参照基准100)= 该段转化率 ÷ 大盘转化率 ×100。"
      ">100 富集/流失慢,<100 流失快。**趋势**:↗持续聚集 / ↘持续流失 / 末端聚集🔼 / 末端流失🔽 / ≈平稳。")
    A("> 号码维度为精确计数,转化率(接通率/20s+率/ABM率)口径硬,非覆盖估算。"
      "可拆:运营商 / 号段 / 号段世代(≈发号时间段) / 号码类型 / 归属地省·市 / 靓号规律。")

    # 重点速览
    movers = _movers(data, totals)
    A("\n## ★ 重点速览:意向端最聚集 Top12(看四段轨迹)\n")
    A("| 维度 | 属性 | 全量占比 | 接通指数 | 20s+指数 | ABM指数 | 趋势 | ABM率 |")
    A("|---|---|---:|---:|---:|---:|---|---:|")
    for dim, k, c, ic, i2, i3 in movers[:12]:
        A(f"| {dim} | {k} | {pct(c[0],totals[0]):.2f}% | {ic:.0f} | {i2:.0f} | "
          f"{i3:.0f}{flag(i3)} | {trend(ic,i2,i3)} | {pct(c[3],c[0]):.3f}% |")
    A("\n### 反向:意向端最流失 Top8(建议降权)\n")
    A("| 维度 | 属性 | 全量占比 | 接通指数 | 20s+指数 | ABM指数 | 趋势 | ABM率 |")
    A("|---|---|---:|---:|---:|---:|---|---:|")
    for dim, k, c, ic, i2, i3 in movers[::-1][:8]:
        A(f"| {dim} | {k} | {pct(c[0],totals[0]):.2f}% | {ic:.0f} | {i2:.0f} | "
          f"{i3:.0f}{flag(i3)} | {trend(ic,i2,i3)} | {pct(c[3],c[0]):.3f}% |")

    sec = "一二三四五六七"
    topn = {"归属地省": 20, "归属地市": 20, "号段(前3)": 20}
    for i, dim in enumerate(DIMS):
        d = data[dim]
        keys = _order_for(dim, d)
        n = topn.get(dim)
        if n:
            keys = [k for k in keys if k != "(未知)"][:n]
        A(f"\n## {sec[i]}、{dim} × 四段轨迹\n")
        A("| " + dim + " | 全量占比 | 接通占比 | 20s+占比 | ABM占比 | 接通指数 | 20s+指数 | ABM指数 | 趋势 | 接通率 | ABM率 |")
        A("|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|")
        for k in keys:
            c = d.get(k, [0, 0, 0, 0])
            ic = index_at(c, totals, 1)
            i2 = index_at(c, totals, 2)
            i3 = index_at(c, totals, 3)
            A(f"| {k} | {pct(c[0],totals[0]):.2f}% | {pct(c[1],totals[1]):.2f}% | "
              f"{pct(c[2],totals[2]):.2f}% | {pct(c[3],totals[3]):.2f}% | "
              f"{ic:.0f} | {i2:.0f} | {i3:.0f}{flag(i3)} | {trend(ic,i2,i3)} | "
              f"{pct(c[1],c[0]):.1f}% | {pct(c[3],c[0]):.3f}% |")

    A("\n---")
    A("> 同套读法见维度二 DMP:晓黄_DMP磁力万象_漏斗交叉.md。归属地省/城市线级两份可交叉验真。")
    with open(os.path.join(OUT_DIR, "晓黄_号码维度_漏斗交叉.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ---------- Excel ----------
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(color="FFFFFF", bold=True)
TAG_FILL = PatternFill("solid", fgColor="D9E1F2")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center")
IDX_RULE = lambda: ColorScaleRule(
    start_type="num", start_value=60, start_color="F8696B",
    mid_type="num", mid_value=100, mid_color="FFEB84",
    end_type="num", end_value=140, end_color="63BE7B")


def write_xlsx(data, totals):
    wb = Workbook()
    ws0 = wb.active
    ws0.title = "说明"
    ws0.sheet_view.showGridLines = False
    ws0.column_dimensions["A"].width = 118
    notes = [
        ("晓黄 号码维度 × 四段漏斗 流失轨迹(维度一)", 14, "1F4E78"),
        (f"全量 {totals[0]:,} → 接通 {totals[1]:,} → 20s+ {totals[2]:,} → 意向ABM {totals[3]:,}", 10, "808080"),
        ("", 10, "000000"),
        ("轨迹 = 同一号码属性在 全量→接通→20s+→ABM 四环节的『相对全量指数』(全量=100)。", 11, "000000"),
        ("相对指数 = 该环节占比 ÷ 全量占比 ×100 = 该段转化率 ÷ 大盘转化率 ×100。>100 富集/流失慢;<100 流失快。", 11, "000000"),
        ("三个指数列(接通/20s+/ABM 指数)热力着色:绿=聚集 / 红=流失。横向读一行看出在哪环节掉的。", 11, "000000"),
        ("号码维度为精确计数,转化率口径硬(非覆盖估算)。与维度二 DMP 同读法,可对照。", 10, "808080"),
    ]
    for i, (t, s, c) in enumerate(notes, 1):
        ws0.cell(i, 1, t).font = Font(size=s, bold=(s >= 12), color=c)

    # 重点速览
    movers = _movers(data, totals)
    ws = wb.create_sheet("重点速览")
    ws.sheet_view.showGridLines = False
    widths = [20, 24, 11, 11, 11, 11, 14, 10]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    heads = ["维度", "属性", "全量占比", "接通指数", "20s+指数", "ABM指数", "趋势", "ABM率"]
    for j, h in enumerate(heads, 1):
        cc = ws.cell(1, j, h); cc.fill = HEAD_FILL; cc.font = HEAD_FONT; cc.alignment = CENTER; cc.border = BORDER
    r = 2
    for dim, k, c, ic, i2, i3 in movers[:25]:
        vals = [dim, k, c[0] / totals[0], ic, i2, i3, trend(ic, i2, i3), pct(c[3], c[0]) / 100]
        for j, v in enumerate(vals, 1):
            cc = ws.cell(r, j, v); cc.border = BORDER
            if j == 3:
                cc.number_format = "0.00%"
            elif j in (4, 5, 6):
                cc.number_format = "0"; cc.alignment = CENTER
            elif j == 8:
                cc.number_format = "0.000%"
        r += 1
    if r > 2:
        ws.conditional_formatting.add(f"D2:F{r-1}", IDX_RULE())
    ws.freeze_panes = "C2"

    topn = {"归属地省": 25, "归属地市": 30, "号段(前3)": 30}
    for dim in DIMS:
        d = data[dim]
        keys = _order_for(dim, d)
        n = topn.get(dim)
        if n:
            keys = [k for k in keys if k != "(未知)"][:n]
        ws = wb.create_sheet(dim.replace("/", "·")[:28])
        ws.sheet_view.showGridLines = False
        ws.column_dimensions["A"].width = 22
        heads = [dim, "全量占比", "接通占比", "20s+占比", "ABM占比",
                 "接通指数", "20s+指数", "ABM指数", "趋势", "接通率", "ABM率"]
        for j, h in enumerate(heads, 1):
            cc = ws.cell(1, j, h); cc.fill = HEAD_FILL; cc.font = HEAD_FONT; cc.alignment = CENTER; cc.border = BORDER
            ws.column_dimensions[get_column_letter(j)].width = 11 if j > 1 else 22
        r = 2
        for k in keys:
            c = d.get(k, [0, 0, 0, 0])
            ic = index_at(c, totals, 1)
            i2 = index_at(c, totals, 2)
            i3 = index_at(c, totals, 3)
            vals = [k, c[0] / totals[0], pct(c[1], totals[1]) / 100, pct(c[2], totals[2]) / 100,
                    pct(c[3], totals[3]) / 100, ic, i2, i3, trend(ic, i2, i3),
                    pct(c[1], c[0]) / 100, pct(c[3], c[0]) / 100]
            for j, v in enumerate(vals, 1):
                cc = ws.cell(r, j, v); cc.border = BORDER
                if j in (2, 3, 4, 5, 10):
                    cc.number_format = "0.00%"
                elif j == 11:
                    cc.number_format = "0.000%"
                elif j in (6, 7, 8):
                    cc.number_format = "0"; cc.alignment = CENTER
            r += 1
        if r > 2:
            ws.conditional_formatting.add(f"F2:H{r-1}", IDX_RULE())
        ws.freeze_panes = "B2"
    wb.save(os.path.join(OUT_DIR, "晓黄_号码维度_漏斗交叉.xlsx"))


if __name__ == "__main__":
    main()
