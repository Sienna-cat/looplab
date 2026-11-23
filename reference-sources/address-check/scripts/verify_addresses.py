#!/usr/bin/env python3
"""verify_addresses.py — 中文外呼/采集地址机械化清洗。

只做规则可机械化的部分（对应 SKILL.md 的 R1/R4/R6/R7）：
- R1 省份补"省"字
- R4 删除常见的无关备注 / 掩码符号
- R6 折叠重复数字串（启发式）
- R7 详细地址回写省+市前缀

不做语义判断（R2 错填地级市、R3 同音/形近错别字、R5 缺失县/区），
这些会以 `需人工复核` 标记保留，由模型/人工后处理。

用法:
  python3 verify_addresses.py <input.xlsx> [--sheet 工作表1] [--output <out.xlsx>]

依赖: openpyxl
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.stderr.write("缺少依赖 openpyxl，请先 `pip install openpyxl`\n")
    sys.exit(2)


# ---- R1: 省份补"省"字 ---------------------------------------------------
PROVINCE_SUFFIX_MAP = {
    "广西": "广西省",
    "宁夏": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
    "西藏": "西藏自治区",
    "内蒙古": "内蒙古自治区",
    "北京": "北京市",
    "上海": "上海市",
    "天津": "天津市",
    "重庆": "重庆市",
    "香港": "香港特别行政区",
    "澳门": "澳门特别行政区",
}
PROVINCE_FULL_NAMES = {
    "北京市", "上海市", "天津市", "重庆市",
    "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "海南省",
    "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省", "台湾省",
    "广西省", "广西壮族自治区",
    "宁夏回族自治区", "新疆维吾尔自治区", "西藏自治区", "内蒙古自治区",
    "香港特别行政区", "澳门特别行政区",
}


def normalize_province(prov: str | None) -> str:
    if not prov:
        return ""
    p = str(prov).strip()
    if p in PROVINCE_FULL_NAMES:
        return p
    return PROVINCE_SUFFIX_MAP.get(p, p if p.endswith(("省", "市", "区")) else p + "省")


# ---- R4: 删除无关备注 / 掩码 -----------------------------------------------
# 在中/英文逗号、句号、分号、破折号后出现的"说明性"片段。
NOTE_SEPARATOR_RE = re.compile(r"[，,;；。]\s*(?:收货时|联系|请打|电话|快递|备注|说明|注[:：]).*$")
TAILING_NOTE_RE = re.compile(r"(?:收货时直接联系|联系电话|请提前联系|请打电话).*$")
MASK_RE = re.compile(r"[*×xX]{2,}")


def strip_notes_and_masks(addr: str) -> str:
    s = addr or ""
    s = NOTE_SEPARATOR_RE.sub("", s)
    s = TAILING_NOTE_RE.sub("", s)
    s = MASK_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ，,。.;；")
    return s


# ---- R6: 折叠重复数字串 ---------------------------------------------------
# 把 4 位以上、形如 "2222" / "44402402" 的串折叠成最短"前缀"，
# 当且仅当串里只有一种数字或明显出现重复块。
def fold_repeats(addr: str) -> str:
    def _fold_match(m: re.Match) -> str:
        s = m.group(0)
        # 全相同数字: 2222 -> 2
        if len(set(s)) == 1:
            return s[0]
        # 重复块: 402402 -> 402, 1234123412 -> 1234
        for k in range(1, len(s) // 2 + 1):
            block = s[:k]
            if block * (len(s) // k) + block[: len(s) % k] == s:
                return block
        return s

    return re.sub(r"\d{4,}", _fold_match, addr or "")


# ---- R7: 详细地址回写 省+市 -----------------------------------------------
# 别名表：判断"省/市是否已在地址里"时，把规范化后的全称
# 与可能的简称视为等价，避免重复前缀（如"广西省" + 已含"广西..."）。
PROVINCE_ALIASES: dict[str, tuple[str, ...]] = {
    "广西省": ("广西", "广西壮族自治区"),
    "广西壮族自治区": ("广西", "广西省"),
    "宁夏回族自治区": ("宁夏",),
    "新疆维吾尔自治区": ("新疆",),
    "西藏自治区": ("西藏",),
    "内蒙古自治区": ("内蒙古",),
}


def _already_contains(addr: str, name: str) -> bool:
    if not name:
        return True
    if name in addr:
        return True
    for alias in PROVINCE_ALIASES.get(name, ()):
        if alias in addr:
            return True
    return False


def ensure_province_city_prefix(addr: str, province: str, city: str) -> str:
    s = (addr or "").strip()
    if not s:
        return s
    if city and not _already_contains(s, city):
        s = city + s
    if province and not _already_contains(s, province):
        s = province + s
    return s


# ---- 主流程 -----------------------------------------------------------------

INPUT_COLS = ("原始采集省", "原始采集市", "原始采集新地址")
OUTPUT_COLS = ("人工校核省", "人工校核市", "人工校核后地址", "需人工复核", "适用规则")


def process_row(prov: str, city: str, addr: str) -> tuple[str, str, str, str, str]:
    rules: list[str] = []
    new_prov = normalize_province(prov)
    if new_prov != (prov or ""):
        rules.append("R1")

    # 城市字段不做语义改动
    new_city = (city or "").strip()

    new_addr = strip_notes_and_masks(addr)
    if new_addr != (addr or ""):
        rules.append("R4")

    folded = fold_repeats(new_addr)
    if folded != new_addr:
        rules.append("R6")
        new_addr = folded

    final_addr = ensure_province_city_prefix(new_addr, new_prov, new_city)
    if final_addr != new_addr:
        rules.append("R7")
        new_addr = final_addr

    needs_review = "是" if (
        not new_city  # 缺市，需要 R2/R5
        or any(c.isdigit() and addr.count(c) >= 4 for c in (addr or ""))  # 仍可能含冗余
        or "*" in (addr or "")  # 原始有掩码，可能丢了行政区
    ) else "否"
    return new_prov, new_city, new_addr, needs_review, ",".join(rules) or "—"


def main() -> int:
    ap = argparse.ArgumentParser(description="中文外呼地址机械化清洗")
    ap.add_argument("input", type=Path, help="输入 Excel 路径")
    ap.add_argument("--sheet", default=None, help="工作表名（默认第一个）")
    ap.add_argument("--output", type=Path, default=None, help="输出 Excel 路径")
    args = ap.parse_args()

    if not args.input.exists():
        sys.stderr.write(f"找不到文件: {args.input}\n")
        return 1

    wb = openpyxl.load_workbook(args.input, data_only=True)
    ws = wb[args.sheet] if args.sheet else wb[wb.sheetnames[0]]

    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    try:
        idx_prov = header.index(INPUT_COLS[0])
        idx_city = header.index(INPUT_COLS[1])
        idx_addr = header.index(INPUT_COLS[2])
    except ValueError:
        sys.stderr.write(
            f"表头必须包含: {INPUT_COLS}; 实际: {header}\n"
        )
        return 1

    out_wb = openpyxl.Workbook()
    out_ws = out_wb.active
    out_ws.title = ws.title
    out_ws.append(list(INPUT_COLS) + list(OUTPUT_COLS))

    auto_count = 0
    review_count = 0
    total = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        prov = row[idx_prov] or ""
        city = row[idx_city] or ""
        addr = row[idx_addr] or ""
        new_prov, new_city, new_addr, needs_review, rules = process_row(
            str(prov), str(city), str(addr)
        )
        out_ws.append([prov, city, addr, new_prov, new_city, new_addr, needs_review, rules])
        total += 1
        if needs_review == "是":
            review_count += 1
        else:
            auto_count += 1

    out_path = args.output or args.input.with_name(args.input.stem + "-校核.xlsx")
    out_wb.save(out_path)

    print(f"✓ 完成: 共 {total} 行，自动清洗 {auto_count}，需人工复核 {review_count}")
    print(f"  输出: {out_path}")
    print("  说明: 仅完成机械化规则 R1/R4/R6/R7；R2/R3/R5 的语义校核需后续由模型/人工处理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
