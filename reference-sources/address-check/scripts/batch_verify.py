#!/usr/bin/env python3
"""端到端校核脚本：Excel/CSV → 校核后 Excel + 中间证据 JSON。

接受两种输入模式:
  1. 纯地址三列模式（原始采集省/原始采集市/原始采集新地址）。
  2. 通话记录模式（任意列名，需要传 --addr-col --transcript-col）。
     此时会自动从 ASR 对话里抽 ME/AI 片段，做客户原声 vs 坐席复述对照。

规则覆盖:
  R1 省份补"省"后缀（mechanical）
  R2 县级市/县错填为地级市 → 用 divisions.py 全国库自动回填
  R3 错别字纠正 → examples.md 字典 + phonetic.py 拼音/方音模糊匹配兜底
  R4 删除无关备注/掩码（mechanical）
  R5 缺市/区补全 → divisions.py
  R6 折叠冗余重复数字（mechanical）
  R7 详细地址回写省+市（mechanical）
  R8 仅含 POI/路名/小区且 R5 兜不住时，调用高德 geocode 反查 (可选)
     需要 --amap-key 或 export AMAP_KEY；无 key 时自动跳过该阶段

用法:
    python3 batch_verify.py <input.xlsx>
        --addr-col 采集新地址 --prov-col 省 --city-col 市 \
        [--transcript-col 通话记录] [--id-col 通话记录id] [--name-col 采集姓名] \
        [--sheet 数据] [--output <out.xlsx>] [--evidence <out.json>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from divisions import (
    aliases_of_county,
    counties_of_prefecture,
    counties_of_province,
    has_street_data,
    is_county,
    is_prefecture,
    lookup_county,
    lookup_township,
    province_of_prefecture,
    townships_of_county,
)
from phonetic import best_match, normalized_phon
from poi_geocode import AmapClient, from_env as amap_from_env
from verify_addresses import (
    PROVINCE_ALIASES,
    fold_repeats,
    normalize_province,
    strip_notes_and_masks,
)


# 替代 verify_addresses.ensure_province_city_prefix——把已有的 prov/city 前缀
# 全部剥离，然后用校核后的 prov+city 重新拼回去，避免出现
# "绵阳市四川省江油市..." 这类前缀错位。
def rebuild_prefix(addr: str, province: str, city: str,
                   old_city: str = "") -> tuple[str, bool]:
    s = (addr or "").strip()
    if not s:
        return s, False
    original = s
    # 反复剥离开头的 prov / new_city / old_city / 它们的别名
    candidates = [province] + list(PROVINCE_ALIASES.get(province, ()))
    if city and city != province:
        candidates.append(city)
    if old_city and old_city not in candidates:
        candidates.append(old_city)
    changed = True
    while changed:
        changed = False
        for name in candidates:
            if name and s.startswith(name):
                s = s[len(name):]
                changed = True
                break
    # 直辖市 / 省直辖县级 (city == province) 时只写一次
    if city and city != province:
        rebuilt = (province or "") + (city or "") + s
    else:
        rebuilt = (province or "") + s
    return rebuilt, rebuilt != original

try:
    import openpyxl
    from openpyxl.styles import PatternFill
except ImportError as e:  # pragma: no cover
    raise SystemExit("缺少依赖 openpyxl，请 pip3 install --user openpyxl") from e


def _norm_cell(v: Any) -> Any:
    """把 calamine 返回的整数型 float 归一成 int（避免 id/手机号变成 '123.0'）。"""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def read_sheet(path: Path, sheet: str | None) -> tuple[str, list, list[list]]:
    """读 xlsx，返回 (sheet_title, headers, body_rows)。

    优先用 python-calamine（Rust，比 openpyxl 快 5–20×）；未安装时回退 openpyxl
    read_only。两条路径输出一致：headers 是首行，body_rows 是其余行（list[list]）。
    """
    try:
        from python_calamine import CalamineWorkbook
        wb = CalamineWorkbook.from_path(str(path))
        name = sheet if sheet else wb.sheet_names[0]
        rows = wb.get_sheet_by_name(name).to_python()
        if not rows:
            return (name, [], [])
        headers = [_norm_cell(c) for c in rows[0]]
        body = [[_norm_cell(c) for c in r] for r in rows[1:]]
        return (name, headers, body)
    except ImportError:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        rows_iter = ws.iter_rows(values_only=True)
        headers = list(next(rows_iter))
        body = [list(r) for r in rows_iter]
        title = ws.title
        wb.close()
        return (title, headers, body)


# ---- examples.md 高频错别字字典 (R3 兜底) ---------------------------------
# 与 examples.md 同步；批处理首先字典命中，未命中时再走 phonetic 模糊匹配。
TYPO_DICT: dict[str, str] = {
    "东风市": "东方市",
    "五都镇": "武都镇",
    "云凤村": "永放村",
    "浦口村": "埠口村",
    "每年御景湾": "浚县御金湾",
    "红石镇": "洪绪镇",
    "红桥镇": "巩昌镇",
    "王坡江大安小区": "黄坡镇",
    "崇宁镇": "崇凝镇",
    "雨花区": "雨花台区",
    "新口镇": "辛口镇",
    "指林镇": "子陵镇",
    "检池镇": "涧池镇",
    "宜川县": "伊川县",
    "小法医街道": "小法仪街道",
    "连州镇": "廉州镇",
    "板山镇": "颜厝镇",
    "射阳区": "射阳县",
    "泥荡镇": "泥凼镇",
    "卢林镇": "儒林镇",
    "漳浦镇": "张浦镇",
    "海城镇": "海澄镇",
    "窖庄镇": "绛帐镇",
    "西安街道": "西海岸街道",
    "波乐乡": "播乐乡",
    "蔡吴乡": "蔡锷乡",
    "曲县": "杞县",
    "镇贤县": "进贤县",
    # 历史名 → 现名 (建制变更后 GB/T 2260 旧名不再存在)
    "济阳县": "济阳区",   # 2018 撤县设区
    "横县": "横州市",     # 2021 撤县设市
    "夹江区": "夹江县",   # 用户简化误填，乐山市辖夹江县
    # 重听 ASR 确认的同音误识（恩施州语境下"华亭/华庭" 实为"花坪"）
    "华亭镇": "花坪镇",
    "华庭镇": "花坪镇",
}


# 条件字典：仅在 hint_province 命中时才替换（避免误伤其他省的同名县）
PROV_TYPO_DICT: dict[str, dict[str, str]] = {
    # 桂林市辖兴安县；ASR 常误识为"新安县"（但河南洛阳也有真正的新安县，仅限广西替换）
    "广西壮族自治区": {"新安县": "兴安县"},
    "广西省": {"新安县": "兴安县"},
    "广西": {"新安县": "兴安县"},
    # 陕西咸阳"彬县" 2018 撤县改市为"彬州市"，且 ASR 常把"彬"识为"宾"
    # (黑龙江哈尔滨有真正的"宾县"，故仅限陕西替换)
    "陕西省": {"宾县": "彬州市", "彬县": "彬州市"},
    "陕西": {"宾县": "彬州市", "彬县": "彬州市"},
    # 山东德州"陵县" 2014 撤县改区为"陵城区" (其他省无同名县，可全局，但保守限定山东)
    "山东省": {"陵县": "陵城区"},
    "山东": {"陵县": "陵城区"},
}


# 启动时合并 R9 自动学习字典 (data/learned_typo.json) ——
# 该文件由 audio_revisit.py 在录音重听后增量写入。批跑/重跑都会自动合并。
def _merge_learned_typo() -> None:
    learned_file = Path(__file__).parent.parent / "data" / "learned_typo.json"
    if not learned_file.exists():
        return
    try:
        data = json.loads(learned_file.read_text(encoding="utf-8"))
    except Exception:
        return
    for bad, good in (data.get("global") or {}).items():
        TYPO_DICT.setdefault(bad, good)
    for prov, mapping in (data.get("prov_aware") or {}).items():
        bucket = PROV_TYPO_DICT.setdefault(prov, {})
        for bad, good in (mapping or {}).items():
            bucket.setdefault(bad, good)


_merge_learned_typo()


# ASR 通话记录行格式: 'AI: ...' / 'ME: ...'
ADDR_TRIGGER = re.compile(
    r"(省|市|区|县|镇|乡|村|组|路|街|号|栋|楼|单元|室|户|社区|小区|大道|新村)"
)
AI_REPEAT = re.compile(
    r"(您?说的是|对吧|确认下|您是在|具体的?(?:街道|门牌|区)|准确送达|核实)"
)


def split_turns(call_text: str) -> list[tuple[str, str]]:
    if not call_text:
        return []
    turns: list[tuple[str, str]] = []
    cur_spk: str | None = None
    buf: list[str] = []
    for ln in call_text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = re.match(r"^(AI|ME)[::](.*)$", ln)
        if m:
            if cur_spk and buf:
                turns.append((cur_spk, " ".join(buf).strip()))
            cur_spk = m.group(1)
            buf = [m.group(2).strip()] if m.group(2).strip() else []
        else:
            buf.append(ln)
    if cur_spk and buf:
        turns.append((cur_spk, " ".join(buf).strip()))
    return turns


def extract_evidence(call_text: str) -> dict[str, list[str]]:
    me: list[str] = []
    ai: list[str] = []
    for spk, txt in split_turns(call_text):
        clean = re.sub(r"<[^>]+>", "", txt).strip()
        if not clean:
            continue
        if spk == "ME" and ADDR_TRIGGER.search(clean):
            me.append(clean)
        elif spk == "AI" and (ADDR_TRIGGER.search(clean) or AI_REPEAT.search(clean)):
            ai.append(clean)
    return {"me": me, "ai": ai}


# ---- 核心校核流程 -----------------------------------------------------------


_PROV_PREFIX_PAT = re.compile(
    r"^(?:北京市|上海市|天津市|重庆市|"
    r"河北省|山西省|辽宁省|吉林省|黑龙江省|江苏省|浙江省|安徽省|福建省|江西省|山东省|"
    r"河南省|湖北省|湖南省|广东省|海南省|四川省|贵州省|云南省|陕西省|甘肃省|青海省|台湾省|"
    r"广西壮族自治区|广西省|广西|"
    r"宁夏回族自治区|宁夏|新疆维吾尔自治区|新疆|西藏自治区|西藏|内蒙古自治区|内蒙古|"
    r"香港特别行政区|澳门特别行政区)"
)


def detect_county_in_addr(addr: str) -> str | None:
    """从详细地址里识别 county-level token，仅当 token 在全国库里时返回。

    流程：
      1. 剥离开头的省名（_PROV_PREFIX_PAT）
      2. 进一步尝试剥离 prefecture（地级市/自治州）—— 处理"恩施土家族苗族自治州利川市"等三段式
      3. 从头匹配第一个 X{县,市,区,旗} 完整 token；优先长名（含民族自治后缀）
      4. 退一步：在地址前段（前 20 字）找完整命中

    避免把"古宁县"误扫成子串"宁县"——只接受紧贴省/州名或地址开头的完整 token。
    """
    if not addr:
        return None
    s = addr
    m = _PROV_PREFIX_PAT.match(s)
    if m:
        s = s[m.end():]
    # 尝试剥离 prefecture（自治州/地级市），让 county 浮到字符串开头
    for L in range(min(20, len(s)), 1, -1):
        head = s[:L]
        if (head.endswith(("市", "州", "盟", "地区")) or head.endswith("自治州")) and is_prefecture(head):
            s = s[L:]
            break
    # 从头匹配第一个 X{县/市/区/旗} token；优先长名（含民族自治后缀）
    m2 = re.match(r"^([\u4e00-\u9fff]{2,10}(?:县|市|区|旗))", s)
    if m2 and is_county(m2.group(1)):
        return m2.group(1)
    # 退一步：在地址前段（前 20 字）找完整命中，优先长名
    window = s[:20]
    for L in range(10, 1, -1):
        for i in range(len(window) - L + 1):
            cand = window[i : i + L]
            if cand.endswith(("县", "市", "区", "旗")) and is_county(cand):
                return cand
    return None


def apply_typo_dict(addr: str) -> tuple[str, list[tuple[str, str]]]:
    diffs: list[tuple[str, str]] = []
    out = addr
    for bad, good in TYPO_DICT.items():
        if bad in out:
            out = out.replace(bad, good)
            diffs.append((bad, good))
    return out, diffs


def _strip_fake_county_tokens(
    addr: str, allowed: set[str]
) -> tuple[str, list[str]]:
    """扫 addr 中形如 X{县/市/区/旗} 的 token，把伪 county 移除。

    判定规则（保留所有以下情况，否则视为伪 token 移除）:
      - token 在 allowed 集合 (R5 已确定的 city / area / prov)
      - is_county(token) → 合法 county 级单位
      - is_prefecture(token) → 合法地级市/直辖市

    返回 (new_addr, removed_tokens)。注意：使用从每个 '县/市/区/旗' 后缀位置
    向前回退 2-4 字的取词方式，避免贪婪 regex 把"邯郸市武安县"误当作一整段。
    """
    if not addr:
        return addr, []
    removed: list[str] = []
    out = addr
    # 用集合记录已经判过的 token 位置，避免重复处理
    handled_ends: set[int] = set()
    for m in re.finditer(r"[县市区旗]", addr):
        end = m.end()
        if end in handled_ends:
            continue
        # 优先找最长能匹配 is_county/is_prefecture/allowed 的 token；
        # 若无，取最短 valid token 作为伪候选
        best_keep: str | None = None
        best_fake: str | None = None
        for L in range(3, 6):  # token 总长 3-5（前缀 2-4 + 后缀 1）
            start = end - L
            if start < 0:
                continue
            seg = addr[start:end]
            inner = seg[:-1]
            # inner 不能含 县/市/区/旗/省/州 (避免跨边界)
            if any(c in "县市区旗省州" for c in inner):
                continue
            if seg in allowed or is_county(seg) or is_prefecture(seg):
                best_keep = seg  # 取最长合法
            elif best_fake is None:
                best_fake = seg  # 取最短伪 token
        if best_keep:
            handled_ends.add(end)
            continue
        if best_fake:
            handled_ends.add(end)
            # 替换为空字符串
            if best_fake in out:
                out = out.replace(best_fake, "", 1)
                removed.append(best_fake)
    return out, removed


def apply_r5_township(
    addr: str,
    prov: str,
    city: str,
    skip_county_check: bool = False,
    bad_county: str = "",
) -> tuple[str, list[str], list[str]]:
    """R5: 当 addr 缺区/县但含街道/镇/乡时，反查所属区/县并补全。

    - skip_county_check: True 时跳过"addr 已含 county"的提前返回。配合 bad_county
      使用，用于"target_county 在 hint 下不自洽"的救济场景（强制用合法镇反推真区县）。
    - bad_county: 救济场景下要剥离的错误 county 字面（如菏泽数据里"金东区"实为牡丹区）。

    返回 (new_addr, rules_triggered, notes)
    """
    if not has_street_data() or not city or not is_prefecture(city):
        return addr, [], []
    if not skip_county_check and detect_county_in_addr(addr):
        return addr, [], []
    body = addr
    m = _PROV_PREFIX_PAT.match(body)
    if m:
        body = body[m.end():]
    if body.startswith(city):
        body = body[len(city):]
    # 救济模式：先把 bad_county 从 body 剥除，避免它被当成合法 county
    if skip_county_check and bad_county and bad_county in body:
        body = body.replace(bad_county, "", 1)
    # 收集 body 里所有 X{镇/街道/乡} candidate token。
    # 用非贪婪 + 末尾 lookahead 找到每个镇/街道/乡名的"左边界"——
    # 直接 finditer 长度 1-4 + (镇/街道/乡)，从末尾的镇字起回退收集前 1-4 字。
    candidates: list[str] = []
    for tail_m in re.finditer(r"(镇|街道|乡)", body):
        end = tail_m.end()
        tail_len = len(tail_m.group(1))
        for L in range(3, 7):  # token 总长 3-6（前缀 2-5 字 + 后缀）
            start = end - L
            if start < 0:
                continue
            seg = body[start:end]
            inner = seg[:-tail_len]
            # 拒绝 inner 里非末位出现 县/区/市/旗 —— 形如 "青春县狮子" 跨过县名
            # "旧县" 这种 inner 末位是"县" 不在拒绝范围（"旧县乡"是合法乡名）
            bad = False
            for i, ch in enumerate(inner):
                if ch in "县区市旗" and i < len(inner) - 1:
                    bad = True
                    break
            if bad:
                continue
            if seg not in candidates:
                candidates.append(seg)
    # 尝试每个候选（优先短 → 长），首个命中即返回
    for twname in sorted(candidates, key=len):
        hits = lookup_township(twname, hint_prefecture=city, hint_province=prov)
        if len(hits) == 1:
            area, _, _ = hits[0]
            if area in addr and not (skip_county_check and area != bad_county):
                # 救济模式: addr 里已有 area 也允许重写 (因为 bad_county 占位)
                return addr, [], []
            # 救济模式: 在 bad_county 已剥的前提下，直接重组前缀+area+body
            if skip_county_check and bad_county:
                cleaned = addr.replace(bad_county, "", 1)
                # 在 city 后插入正 area
                if city in cleaned:
                    new_addr = cleaned.replace(city, city + area, 1)
                else:
                    new_addr = (prov or "") + city + area + body
                return new_addr, ["R5"], [f"R5 救济: {twname}→{area}; 剥除伪 county={bad_county}"]
            # 先扫 addr 内是否有"伪 county" token（形如 X{县/市/区/旗} 但全国库无
            # 且不是 city/area/prov 本身）；典型场景：用户在区县列误填"武安县"，
            # 实际详细地址在涉县——R5 必须清理伪 token，否则会出现"涉县武安县"叠加。
            removed = _strip_fake_county_tokens(addr, allowed={city, area, prov})
            cleaned = removed[0]
            new_addr = (
                cleaned.replace(city, city + area, 1)
                if city in cleaned
                else (city + area + body)
            )
            r5_notes = [f"R5: {twname}→{area}"]
            if removed[1]:
                r5_notes.append(f"R5: 清理伪 county {removed[1]} (与正解 {area} 矛盾)")
            return new_addr, ["R5"], r5_notes
    # 多候选：记日志，按短→长第一个 >1 命中
    for twname in sorted(candidates, key=len):
        hits = lookup_township(twname, hint_prefecture=city, hint_province=prov)
        if len(hits) > 1:
            cands = [a for a, _, _ in hits]
            return addr, [], [f"R5 候选>1: {twname}→{cands[:4]}"]
    return addr, [], []


def apply_r3_township_phonetic(
    addr: str,
    target_county: str | None,
    new_prov: str,
    new_city: str,
    min_score: float = 0.65,
    gap: float = 0.05,
) -> tuple[str, list[str]]:
    """R3 镇/街道级 phonetic 兜底：

    当 target_county 已锁定，addr 里存在 X{镇/街道/乡} 但该名称**不在** target_county 的
    辖镇/街道列表里，且辖镇里恰有**唯一**一个 phon_score ≥ min_score 的候选时，
    自动替换为该候选。典型场景：万宁市辖"万城镇"，ASR 误识为"万盛镇"（平翘舌/前后鼻音不分）。

    防护策略 (避免误伤真实重名地名)：
      ① token 是 candidate 的真超集 (如 "李庄古镇" ⊃ "李庄镇")：跳过——用户带了额外特征字
      ② candidate 是 token 的真超集 且 addr 在 token 之前正好已有差额字符 (如
        "禹城市|中街道" 替换"中街道"→"市中街道" 会产生双"市")：跳过
      ③ 完全同音(score≥0.999)直接改；非完全同音时若 token 本身是全国合法镇名，跳过——
        用户可能填错了 hint，不能根据 hint 改 town
      ④ "X镇镇/X乡乡" 这种后缀叠字：先 trim 一字再判断

    返回 (new_addr, diffs)，diffs 元素形如 "万盛镇→万城镇 (phon=0.67)"。
    """
    if not target_county or not has_street_data():
        return addr, []
    # 找 prefecture
    if new_city and new_city != new_prov:
        prefecture = new_city
    else:
        hits = lookup_county(target_county, new_prov or None)
        if not hits:
            return addr, []
        prefecture = hits[0][0]
    towns = list(townships_of_county(prefecture, target_county))
    if not towns:
        return addr, []
    from phonetic import phon_score  # 延迟导入避免循环

    diffs: list[str] = []
    out = addr
    # 防护④: 后缀叠字 (三都镇镇 → 三都镇)
    out = re.sub(r"(镇)\1", r"\1", out)
    out = re.sub(r"(乡)\1", r"\1", out)
    out = re.sub(r"(街道)\1", r"\1", out)
    if out != addr:
        diffs.append(f"trim 后缀叠字")

    seen: set[str] = set()
    # 镇/乡 前缀至少 1 字 (3 字 token) 起；街道至少 2 字 (4 字 token) 起 ——
    # 街道 1 字前缀（如"桥街道"）太短，phonetic 不可靠
    tail_min_total = {"镇": 3, "乡": 3, "街道": 4}
    for tail_m in re.finditer(r"(镇|街道|乡)", out):
        end = tail_m.end()
        tail_str = tail_m.group(1)
        tail_len = len(tail_str)
        min_total = tail_min_total[tail_str]
        # 收集所有合法长度的 token (不跨边界)
        tokens_at_pos: list[str] = []
        for L in range(min_total, 7):
            start = end - L
            if start < 0:
                continue
            seg = out[start:end]
            inner = seg[:-tail_len]
            if any(c in "县区市旗" for c in inner):
                continue
            tokens_at_pos.append(seg)
        if not tokens_at_pos:
            continue
        # 若任意长度 token 已在 towns 里 → 该位置合法，跳过
        if any(t in towns for t in tokens_at_pos):
            continue
        # 对每个长度的 token 计算最佳辖镇候选，跨长度取整体 score 最高的 (token, best, score)
        best_overall: tuple[str, str, float, list[tuple[str, float]]] | None = None  # (token, best, score, ranked)
        for tok in tokens_at_pos:
            ranked_local = sorted(((t, phon_score(tok, t)) for t in towns), key=lambda x: -x[1])
            if not ranked_local:
                continue
            cand, sc = ranked_local[0]
            if best_overall is None or sc > best_overall[2]:
                best_overall = (tok, cand, sc, ranked_local)
        if best_overall is None:
            continue
        token, best, score, ranked = best_overall
        if token in seen:
            continue
        seen.add(token)
        if score < min_score:
            continue
        if len(ranked) >= 2 and ranked[1][1] >= score - gap:
            continue
        # 防护①: best 是 token 的子序列 (如 "李庄镇" 是 "李庄古镇" 的子序列；用户带了额外特征字)
        def _is_subseq(short: str, long: str) -> bool:
            i = 0
            for c in long:
                if i < len(short) and short[i] == c:
                    i += 1
            return i == len(short)
        if best != token and _is_subseq(best, token):
            continue
        # 防护②: best ⊃ token (连续) 且 addr 中 token 前已有差额字符 (避免 "中街道"→"市中街道" 双"市")
        if best != token and token in best:
            extra = best.replace(token, "", 1)
            pos = out.find(token)
            if pos >= 0 and pos >= len(extra) and out[pos - len(extra) : pos] == extra:
                continue
        # 防护③: 非完全同音时，若 token 是全国合法镇名 → 跳过 (用户可能填错 hint)
        if score < 0.999 and lookup_township(token):
            continue
        out = out.replace(token, best, 1)
        diffs.append(f"{token}→{best} (phon={score:.2f})")
    return out, diffs


def phonetic_fix_in_prefecture(addr: str, prefecture: str) -> list[dict[str, Any]]:
    """从 addr 里截取"X 县/区/市"片段，与 prefecture 辖下名称做拼音/方音匹配。

    返回 [{span, candidates: [(name, score)]}, ...]，仅返回 score ≥ 0.85 的候选。
    实现细节: 先把 pool 中真实出现的县名占位为 \\x00，避免它们的尾部子串被误当成 ASR 误识来匹配。
    """
    pool = counties_of_prefecture(prefecture)
    if not pool:
        return []
    masked = addr
    # 按长度倒序，长名字优先占位（"融水苗族自治县" 先于 "融水县"）
    for real in sorted(pool, key=len, reverse=True):
        if real in masked:
            masked = masked.replace(real, "\x00" * len(real))
    out: list[dict[str, Any]] = []
    for m in re.finditer(r"([\u4e00-\u9fff]{2,5}(?:县|区|市))", masked):
        span = m.group(1)
        if span in pool:
            continue
        hits = best_match(span, pool, min_score=0.85)
        if hits:
            out.append({"span": span, "candidates": hits[:3]})
    return out


def process_row(
    prov: str,
    city: str,
    addr: str,
    evidence: dict[str, list[str]] | None = None,
    district: str = "",
) -> dict[str, Any]:
    rules: list[str] = []
    notes: list[str] = []

    new_prov = normalize_province(prov or "")
    if new_prov != (prov or ""):
        rules.append("R1")

    new_addr = strip_notes_and_masks(addr or "")
    if new_addr != (addr or ""):
        rules.append("R4")

    folded = fold_repeats(new_addr)
    if folded != new_addr:
        rules.append("R6")
        new_addr = folded

    # 处理外部传入的 "区县" 列：如果填了且 addr 主体里没有它，合并进 addr 主体。
    # 注意：区县列可能包含多级（如"余干县内红镇"）或县级市（如"溧阳市"）；
    # 同时也可能是错别字（如"南山县" 实际应为"蓝山县"），由后续 R3 兜底处理。
    district_clean = (district or "").strip()
    if district_clean and district_clean not in new_addr:
        # 不重复插入 prov/city 前缀；直接拼在 addr 主体最前
        body = new_addr
        m = _PROV_PREFIX_PAT.match(body)
        prefix = body[:m.end()] if m else ""
        rest = body[m.end():] if m else body
        if (city or "") and rest.startswith(city):
            prefix = prefix + city
            rest = rest[len(city):]
        new_addr = prefix + district_clean + rest
        notes.append(f"区县列→addr: +{district_clean}")

    # R3 字典命中
    typo_fixed, typo_diffs = apply_typo_dict(new_addr)
    if typo_diffs:
        rules.append("R3")
        new_addr = typo_fixed
    # R3 prov-aware 条件字典：避免同名县跨省误伤；同步替换 district_clean
    # 防重复保护：若 good 已在 addr 字面 (如用户 city 列已填"永城市"),
    # 把 bad 直接删除而不是替换 (避免出现"永城市永城市"重复堆栈)
    if new_prov:
        cond_map = PROV_TYPO_DICT.get(new_prov, {})
        for bad, good in cond_map.items():
            hit = False
            if bad in new_addr:
                if good in new_addr.replace(bad, "", 1):
                    new_addr = new_addr.replace(bad, "", 1)
                    notes.append(f"R3 prov-conditional: 删除冗余 {bad} (good={good} 已在 addr/city)")
                else:
                    new_addr = new_addr.replace(bad, good)
                    notes.append(f"R3 prov-conditional: {bad}→{good} (in {new_prov})")
                hit = True
            if district_clean and bad in district_clean:
                district_clean = district_clean.replace(bad, good)
                hit = True
            if hit:
                if "R3" not in rules:
                    rules.append("R3")

    # R2 county→prefecture 自动判定
    new_city = (city or "").strip()
    target_county: str | None = None
    # 优先级：district 列（独立输入）> city 字段 > 从 addr 抽取
    if district_clean and is_county(district_clean):
        target_county = district_clean
    elif is_county(new_city):
        target_county = new_city
    elif not is_prefecture(new_city):
        target_county = detect_county_in_addr(new_addr)

    if target_county:
        candidates = lookup_county(target_county, hint_province=new_prov or None)
        if len(candidates) == 1:
            pref, prov_full = candidates[0]
            # 省直辖县级（如海南省的万宁市/东方市）：pref 实际是省名，此时 city 留空，
            # 让 addr 直接 "海南省 + 万宁市..." 即可。
            if pref == prov_full:
                if new_city and new_city != prov_full:
                    new_city = ""
                    rules.append("R2")
                elif not new_city:
                    pass  # city 本就空，不动
            elif new_city != pref:
                new_city = pref
                rules.append("R2")
            if not new_prov:
                new_prov = prov_full
                rules.append("R1")
        elif len(candidates) > 1:
            notes.append(f"county={target_county} 候选: {candidates}")
        else:
            # 严格 hint 下查不到：可能 hint_prov 与 county 不自洽，或县名不存在
            full = lookup_county(target_county)  # 不带 hint 再查一次
            if full:
                notes.append(
                    f"county={target_county} hint='{new_prov}' 不匹配；全局候选: {full}"
                )
            else:
                notes.append(f"county={target_county} 在全国库无记录")

    # R7 回写省/市前缀（统一剥离 + 重拼，保证顺序为 省+市+剩余）
    # 把"用户原填 city"（仅当它是 prefecture-level 错填，如"武汉市"应剥离）也带进 candidates；
    # 若 old_city 本身是 county（如"平泉市/安陆市"），它应保留在 addr 主体作为合法 county，不剥离。
    old_city_strip = (city or "").strip()
    old_city_for_strip = old_city_strip if (old_city_strip and is_prefecture(old_city_strip)) else ""
    final_addr, prefix_changed = rebuild_prefix(new_addr, new_prov, new_city, old_city=old_city_for_strip)
    if prefix_changed:
        rules.append("R7")
        new_addr = final_addr

    # R3 兜底①: 在地级市辖下做拼音模糊匹配
    phon_candidates: list[dict[str, Any]] = []
    if new_city and is_prefecture(new_city):
        phon_candidates = phonetic_fix_in_prefecture(new_addr, new_city)
        for ph in phon_candidates:
            if len(ph["candidates"]) == 1 and ph["candidates"][0][1] >= 0.999:
                bad, (good, _) = ph["span"], ph["candidates"][0]
                if bad in new_addr:
                    new_addr = new_addr.replace(bad, good)
                    if "R3" not in rules:
                        rules.append("R3")
                    notes.append(f"phon: {bad}→{good}")
                    # 如果 target_county 等于被修的字面，同步更新
                    # (典型: target_county='象山区' 被改成'相山区')
                    if target_county == bad:
                        target_county = good
                    # R3 phon 后若 new_city + new_target_county 唯一锁定到另一 prov,
                    # 且当前 new_prov 与之不自洽 → 修正 prov（用户 prov 错填）
                    # 典型: 用户填"陕西省大同市红原县"，phon 改成"浑源县"后 lookup 唯一锁山西大同
                    if target_county == good:
                        new_hits = lookup_county(good, hint_prefecture=new_city)
                        if len(new_hits) == 1:
                            real_prov = new_hits[0][1]
                            if new_prov and new_prov != real_prov:
                                old_prov = new_prov
                                new_prov = real_prov
                                notes.append(f"R2 prov 反推: {old_prov}→{real_prov} (依据 {new_city}+{good})")
                                if "R2" not in rules:
                                    rules.append("R2")

    # R5: 当地级市已知但 addr 里没有区/县，用 X 镇/街道/乡 反查 area 并补全
    r5_addr, r5_rules, r5_notes = apply_r5_township(new_addr, new_prov, new_city)
    if r5_rules:
        new_addr = r5_addr
        rules.extend(r5_rules)
    notes.extend(r5_notes)

    # R3 兜底③(镇/街道 phonetic): 当 target_county 锁定且 addr 里有 X{镇/街道/乡} 不在辖镇内，
    # 在辖镇内做 phonetic 兜底，唯一近似候选自动修正
    # 典型：海南万宁市辖"万城镇"，ASR 误识为"万盛镇"
    r3b_addr, r3b_diffs = apply_r3_township_phonetic(new_addr, target_county, new_prov, new_city)
    if r3b_diffs:
        new_addr = r3b_addr
        if "R3" not in rules:
            rules.append("R3")
        for d in r3b_diffs:
            notes.append(f"R3 township-phon: {d}")

    # R3 兜底②: 缺市但能从 addr 首段抽出未知 X 县/区/市 时，
    # 在所属 prov 全境做拼音模糊匹配，命中且唯一时自动修正
    province_phon_hits: list[dict[str, Any]] = []
    if not target_county and new_prov:
        body = new_addr
        m = _PROV_PREFIX_PAT.match(body)
        if m:
            body = body[m.end():]
        tok = re.match(r"^([\u4e00-\u9fff]{2,5}(?:县|市|区|旗))", body)
        if tok:
            unknown = tok.group(1)
            if not is_county(unknown):
                pool = counties_of_province(new_prov)
                hits = best_match(unknown, pool, min_score=0.9)
                if hits:
                    province_phon_hits.append({"span": unknown, "candidates": hits[:3]})
                    if len(hits) == 1 and hits[0][1] >= 0.999:
                        good = hits[0][0]
                        new_addr = new_addr.replace(unknown, good, 1)
                        if "R3" not in rules:
                            rules.append("R3")
                        notes.append(f"phon(prov): {unknown}→{good}")
                        # 顺势补地级市
                        cands = lookup_county(good, hint_province=new_prov)
                        if len(cands) == 1:
                            pref, _ = cands[0]
                            if pref != new_prov and not new_city:
                                new_city = pref
                                rules.append("R2")
                        # 重新拼前缀
                        new_addr, _ = rebuild_prefix(new_addr, new_prov, new_city, old_city=old_city_for_strip)

    # R3d 县级方音救济: 当 target_county 在 (new_prov, new_city) 下不自洽时,
    # 尝试两种救济:
    #   ① 在 new_city 辖下做 phonetic 替代 (例: "宾县" → 咸阳市辖"彬县/彬州市")
    #   ② R5 强制反推 (用 addr 里的镇/街道反查正确 county，剥除 bad_county)
    if target_county and new_city and is_prefecture(new_city):
        consistent_hits = lookup_county(
            target_county,
            hint_province=new_prov or None,
            hint_prefecture=new_city,
        )
        if not consistent_hits:
            # 救济①: phonetic
            pool = counties_of_prefecture(new_city)
            phon_hits = best_match(target_county, pool, min_score=0.95) if pool else []
            if phon_hits and len(phon_hits) == 1 and phon_hits[0][1] >= 0.95:
                good = phon_hits[0][0]
                if target_county in new_addr:
                    new_addr = new_addr.replace(target_county, good)
                else:
                    new_addr, _ = rebuild_prefix(new_addr, new_prov, new_city, old_city=old_city_for_strip)
                    if good not in new_addr:
                        new_addr = (new_prov or "") + new_city + good + new_addr.replace(new_prov or "", "", 1).replace(new_city, "", 1)
                notes.append(f"R3d county-phon: {target_county}→{good} (phon={phon_hits[0][1]:.2f})")
                if "R3" not in rules:
                    rules.append("R3")
                target_county = good
            else:
                # 救济②: R5 强制反推
                r5b_addr, r5b_rules, r5b_notes = apply_r5_township(
                    new_addr, new_prov, new_city,
                    skip_county_check=True, bad_county=target_county,
                )
                if r5b_rules:
                    new_addr = r5b_addr
                    rules.extend(r5b_rules)
                    notes.extend(r5b_notes)
                    # 从新 addr 里抽出新的 target_county
                    new_target = detect_county_in_addr(new_addr)
                    if new_target and lookup_county(new_target, hint_province=new_prov, hint_prefecture=new_city):
                        target_county = new_target

    # 复核判定
    review = False
    review_reasons: list[str] = []
    body = new_addr
    if new_prov and body.startswith(new_prov):
        body = body[len(new_prov):]
    if new_city and body.startswith(new_city):
        body = body[len(new_city):]
    if not body.strip():
        review, review_reasons = True, review_reasons + ["详细地址为空"]
    # 仅当 addr 里没有合法 county 且无 target_county 锁定时才标"缺市"
    # 省直辖县级（如海南万宁市）city 留空是合法的，target_county=万宁市 已锁定即合法
    if not new_city and not target_county and not detect_county_in_addr(new_addr):
        review, review_reasons = True, review_reasons + ["缺市/区"]
    if target_county:
        # 一级 prov hint + 二级 prefecture hint (解决"鼓楼区"在江苏多市同名等场景)
        strict_hits = lookup_county(
            target_county,
            hint_province=new_prov or None,
            hint_prefecture=new_city or None,
        )
        if len(strict_hits) > 1:
            review = True
            review_reasons.append(f"县名重名:{target_county}")
        elif len(strict_hits) == 0 and lookup_county(target_county):
            review = True
            review_reasons.append(f"hint 不自洽:{target_county} vs {new_prov}")
        elif len(strict_hits) == 0:
            review = True
            review_reasons.append(f"全国库无{target_county}")
    for ph in phon_candidates + province_phon_hits:
        if len(ph["candidates"]) > 1 or ph["candidates"][0][1] < 0.999:
            review, review_reasons = True, review_reasons + [
                f"拼音候选不确定:{ph['span']}→{ph['candidates']}"
            ]

    # 提取校核区县（用于输出独立列）
    # 优先级：target_county（R2 已锁定的 county）> detect_county_in_addr 兜底
    new_district = (target_county or detect_county_in_addr(new_addr) or "")

    return {
        "校核省": new_prov,
        "校核市": new_city,
        "校核区县": new_district,
        "校核地址": new_addr,
        "rules": sorted(set(rules)),
        "notes": notes,
        "review_needed": review,
        "review_reasons": review_reasons,
        "phon_candidates": phon_candidates,
        "evidence": evidence or {"me": [], "ai": []},
    }


# ---- R8: 高德 geocode 反查 ----------------------------------------------------


def apply_r8_amap(rec: dict[str, Any], client: AmapClient) -> None:
    """对单条记录调用高德 geocode，原地修改 rec：

    - 若 校核区县 空 且 amap 返回 district  → 补 district，标 R8
    - 若 校核区县 与 amap district 不一致 → 标 review（行为：仅 note 不改字段）
    - 失败 / 无结果 / level<区县 → 不动
    """
    addr = rec.get("校核地址", "") or rec.get("原始地址", "")
    if not addr:
        return
    hint_city = rec.get("校核市") or rec.get("校核省") or ""
    g = client.geocode(addr, hint_city=hint_city)
    if not g:
        rec.setdefault("notes", []).append("R8 amap: 无结果")
        return
    a_dist = g.get("district") or ""
    a_prov = g.get("province") or ""
    a_city = g.get("city") or ""
    level = g.get("level") or ""
    rec.setdefault("amap", {})
    rec["amap"] = {
        "province": a_prov, "city": a_city, "district": a_dist,
        "level": level, "adcode": g.get("adcode", ""),
        "formatted": g.get("formatted_address", ""),
    }
    local_dist = rec.get("校核区县", "") or ""
    notes: list[str] = rec.setdefault("notes", [])
    rules: list[str] = rec.setdefault("rules", [])

    if not local_dist and a_dist:
        rec["校核区县"] = a_dist
        if "R8" not in rules:
            rules.append("R8")
        notes.append(f"R8 amap: district={a_dist} level={level}")
        # 同步把空的 city 也补上（R8 顺手）
        if not rec.get("校核市") and a_city:
            rec["校核市"] = a_city
            notes.append(f"R8 amap: city={a_city}")
            # 既然补了 city，原本的"缺市/区"复核理由要清除
            rec["review_reasons"] = [x for x in rec.get("review_reasons", []) if "缺市" not in x]
            if not rec["review_reasons"]:
                rec["review_needed"] = False
    elif local_dist and a_dist and local_dist != a_dist:
        # 别名/全称差异容差（如"印江县" vs "印江土家族苗族自治县" 视为一致）
        same_county = local_dist in aliases_of_county(a_dist) or a_dist in aliases_of_county(local_dist)
        if same_county:
            notes.append(f"R8 amap: 别名一致 ({local_dist} ≡ {a_dist})")
        else:
            # 按"谁在 addr 字面里"决策
            local_in = local_dist in addr
            amap_in = a_dist in addr
            if local_in and not amap_in:
                # 本地区县在地址里出现而 amap 给的没有 → 本地更可信，amap 被 POI 带偏
                notes.append(f"R8 amap 偏差(本地保留): 本地={local_dist}✓ ≠ amap={a_dist}✗ (level={level})")
            elif amap_in and not local_in:
                # amap 在地址里但本地不在 → amap 正确，自动修正
                rec["校核区县"] = a_dist
                if "R8" not in rules:
                    rules.append("R8")
                notes.append(f"R8 amap 修正: 本地={local_dist}✗ → amap={a_dist}✓ (level={level})")
            else:
                # 两者都不在 / 都在 字面里 → 无法自动判定
                notes.append(f"R8 amap 不一致: 本地={local_dist} ≠ amap={a_dist} (level={level})")
                rec["review_needed"] = True
                rec.setdefault("review_reasons", []).append(
                    f"区县不一致: 本地={local_dist} amap={a_dist} (amap_level={level})"
                )
    # 顺手校核 province/city 不一致（仅 note，不自动改）。简称/全称视为一致：
    #   "广西省" / "广西" ≡ "广西壮族自治区"；"内蒙古" ≡ "内蒙古自治区" 等
    def _norm_admin(s: str) -> str:
        for suf in ("壮族自治区", "回族自治区", "维吾尔自治区", "自治区", "特别行政区", "省", "市"):
            s = s.replace(suf, "")
        return s.strip()

    local_prov = rec.get("校核省", "") or ""
    local_city = rec.get("校核市", "") or ""
    if a_prov and local_prov and _norm_admin(a_prov) != _norm_admin(local_prov):
        notes.append(f"R8 amap 省不一致: 本地={local_prov} ≠ amap={a_prov}")
    if a_city and local_city and _norm_admin(a_city) != _norm_admin(local_city):
        notes.append(f"R8 amap 市不一致: 本地={local_city} ≠ amap={a_city}")


# ---- I/O --------------------------------------------------------------------


def _worker(task: tuple) -> dict[str, Any]:
    """multiprocessing 入口：tuple → process_row 结果。

    task = (prov, city, addr, transcript, rid, name, district, phone, gender)
    向后兼容：长度 7/8 时 phone/gender 默认空 (老调用方仍可用)
    """
    if len(task) == 9:
        prov, city, addr, transcript, rid, name, district, phone, gender = task
    elif len(task) == 8:
        prov, city, addr, transcript, rid, name, district, phone = task
        gender = ""
    else:
        prov, city, addr, transcript, rid, name, district = task
        phone = ""
        gender = ""
    evidence = extract_evidence(transcript) if transcript else None
    rec = process_row(prov, city, addr, evidence, district=district)
    rec["原始省"] = prov
    rec["原始市"] = city
    rec["原始区县"] = district
    rec["原始地址"] = addr
    if rid:
        rec["id"] = rid
    if name:
        rec["name"] = name
    if phone:
        rec["phone"] = phone
    if gender:
        rec["gender"] = gender
    # 透传原 transcript 给 R9 (用于 ME: 段证据合并)
    if transcript:
        rec["transcript"] = transcript
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="中文外呼地址端到端校核")
    ap.add_argument("input", type=Path)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--prov-col", default="省")
    ap.add_argument("--city-col", default="市")
    ap.add_argument("--addr-col", default="采集新地址")
    ap.add_argument("--id-col", default=None)
    ap.add_argument("--name-col", default=None,
                    help="原表里的姓名/姓氏列名（输出会写到'采集姓名'列）")
    ap.add_argument("--phone-col", default="联系方式",
                    help="原表里的手机号列名（默认'联系方式'，若不存在自动跳过）")
    ap.add_argument("--gender-col", default="性别",
                    help="原表里的性别列名（默认'性别'，若不存在自动跳过）")
    ap.add_argument("--district-col", default=None,
                    help="原始'区县'列名。若给定，作为辅助信号合并进 addr 主体并独立输出。")
    ap.add_argument("--transcript-col", default=None,
                    help="若给定，则从此列抽取 ASR 通话记录做客户原声/坐席复述对照")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--evidence", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=0,
                    help="并行 worker 数。0=自动(N≥1000 用 min(8,cpu)，否则串行)。1=强制串行。")
    ap.add_argument("--amap-key", default="",
                    help="高德 Web API key；不传则读环境变量 AMAP_KEY。给定时启用 R8 反查。")
    ap.add_argument("--amap-scope", choices=("none", "empty", "review", "all"), default="all",
                    help="R8 调用范围: none=禁用 / empty=仅校核区县为空 / review=空+需复核 / all=全部行")
    ap.add_argument("--amap-qps", type=float, default=3.0,
                    help="高德 QPS 限流（免费版 3）")
    # R9: 录音重听 + 自动学习
    ap.add_argument("--audio-revisit", choices=("none", "review", "gaps", "learn", "all"),
                    default="review",
                    help="R9 录音重听: none=禁用 / review=仅复核行(默认) / "
                         "gaps=复核+区县/市空 / learn=gaps+addr含非法镇名 / all=全部")
    ap.add_argument("--audio-url-col", default="通话录音",
                    help="原表里的录音 URL 列名 (用于 R9 下载 mp3)")
    ap.add_argument("--audio-cache-dir", default="/tmp/revisit",
                    help="R9 mp3 + asr.txt 缓存目录")
    ap.add_argument("--audio-timeout", type=float, default=180,
                    help="R9 单条 ASR 超时秒数")
    args = ap.parse_args()

    if not args.input.exists():
        sys.stderr.write(f"找不到文件: {args.input}\n")
        return 1

    sheet_title, headers, body_rows = read_sheet(args.input, args.sheet)
    idx = {h: i for i, h in enumerate(headers)}

    required = [args.prov_col, args.city_col, args.addr_col]
    for col in required:
        if col not in idx:
            sys.stderr.write(f"缺列: {col}; 实际表头: {headers}\n")
            return 1

    has_id = args.id_col and args.id_col in idx
    has_name = args.name_col and args.name_col in idx
    has_tr = args.transcript_col and args.transcript_col in idx
    has_dist = args.district_col and args.district_col in idx
    has_url = args.audio_url_col and args.audio_url_col in idx
    has_phone = args.phone_col and args.phone_col in idx
    has_gender = args.gender_col and args.gender_col in idx
    # 若 id_col 本身就是手机号列（如 400 表用 --id-col 手机号），避免重复
    phone_same_as_id = has_phone and has_id and args.phone_col == args.id_col

    tasks: list[tuple] = []
    url_by_id: dict[str, str] = {}
    for row in body_rows:
        if not row:
            continue
        addr = row[idx[args.addr_col]] if idx[args.addr_col] < len(row) else None
        if not addr or not str(addr).strip():
            continue
        prov = str(row[idx[args.prov_col]] or "")
        city = str(row[idx[args.city_col]] or "")
        transcript = str(row[idx[args.transcript_col]] or "") if has_tr else ""
        rid = str(row[idx[args.id_col]] or "") if has_id else ""
        name = str(row[idx[args.name_col]] or "") if has_name else ""
        district = str(row[idx[args.district_col]] or "") if has_dist else ""
        phone = str(row[idx[args.phone_col]] or "") if has_phone else ""
        if phone_same_as_id:
            phone = rid
        gender = str(row[idx[args.gender_col]] or "") if has_gender else ""
        if has_url and rid:
            url_by_id[rid] = str(row[idx[args.audio_url_col]] or "")
        tasks.append((prov, city, str(addr), transcript, rid, name, district, phone, gender))

    workers = args.workers
    if workers == 0:
        # process_row 本身很快（~9000 行/秒），且 task 携带完整通话记录，
        # 跨进程 pickle/IPC 成本高。实测 ≤1 万行多进程是负优化（读 Excel 才是瓶颈），
        # 仅在超大批量（≥5 万行）才启用并行。
        import os as _os
        workers = min(8, _os.cpu_count() or 1) if len(tasks) >= 50000 else 1

    if workers <= 1:
        results = [_worker(t) for t in tasks]
    else:
        from multiprocessing import Pool
        with Pool(workers) as p:
            results = p.map(_worker, tasks, chunksize=max(1, len(tasks) // (workers * 8)))

    # R8: 高德 geocode 反查（仅在 main 里串行调用，避免子进程拷贝 client / IO 抢限流）
    r8_called = 0
    if args.amap_scope != "none":
        client = amap_from_env(key=args.amap_key, qps=args.amap_qps)
        if client is None:
            print("⚠ 未提供 AMAP_KEY 且 --amap-key 为空，R8 跳过 (设置 export AMAP_KEY=xxx 后重试)")
        else:
            # 先收集所有需要 amap 的行
            want_rows: list[dict[str, Any]] = []
            for r in results:
                want = False
                if args.amap_scope == "all":
                    want = True
                elif args.amap_scope == "empty":
                    want = not (r.get("校核区县") or "").strip()
                elif args.amap_scope == "review":
                    want = not (r.get("校核区县") or "").strip() or r.get("review_needed", False)
                if want:
                    want_rows.append(r)
            # 批量预热缓存 (batch=true, 单次最多 10 地址, 节省 QPS)
            if want_rows:
                addr_hints = [
                    ((r.get("校核地址") or r.get("原始地址") or "").strip(),
                     (r.get("校核市") or "").strip())
                    for r in want_rows
                ]
                prefetched = client.prefetch(addr_hints)
                if prefetched:
                    print(f"  R8 prefetch: 批量请求 {prefetched} 条新地址 (10/批)")
            # 走原 per-record 逻辑——此时大部分应已缓存命中
            for r in want_rows:
                apply_r8_amap(r, client)
                r8_called += 1
            client.flush()

    # R9: 录音重听 + 自动学习字典（在 R8 之后，处理仍 review/缺信息的行）
    r9_stats = {"target_count": 0, "fixed": 0, "learned_count": 0, "fail": 0}

    def _typo_translate(tok: str, prov: str | None) -> str:
        """把 token 走 PROV_TYPO_DICT + TYPO_DICT 翻译；R9 用于救历史撤县/同音误识。"""
        if not tok:
            return tok
        if prov and prov in PROV_TYPO_DICT and tok in PROV_TYPO_DICT[prov]:
            return PROV_TYPO_DICT[prov][tok]
        if tok in TYPO_DICT:
            return TYPO_DICT[tok]
        return tok

    if args.audio_revisit != "none":
        if not url_by_id and not any(r.get("transcript") for r in results):
            print(f"⚠ R9 跳过: 原表无录音 URL 列 ({args.audio_url_col}) 且 ME 段也空")
        else:
            try:
                from audio_revisit import apply_r9_batch
                r9_stats = apply_r9_batch(
                    results,
                    url_by_id=url_by_id,
                    cache_dir=Path(args.audio_cache_dir),
                    scope=args.audio_revisit,
                    transcribe_timeout=args.audio_timeout,
                    verbose=True,
                    typo_translator=_typo_translate,
                )
            except Exception as e:
                print(f"⚠ R9 audio revisit 失败: {e}")
                import traceback; traceback.print_exc()

    # 输出 Excel (write_only 模式: 减少内存 & 加速)
    out_path = args.output or args.input.with_name(args.input.stem + "-校核.xlsx")
    yellow = PatternFill(start_color="FFF7C7", end_color="FFF7C7", fill_type="solid")
    green = PatternFill(start_color="D6F5D6", end_color="D6F5D6", fill_type="solid")
    out_wb = openpyxl.Workbook(write_only=True)
    out_ws = out_wb.create_sheet(title=(sheet_title[:31] if sheet_title else "校核"))
    out_ws.append([
        "id", "手机号", "采集姓名", "性别",
        "原始省", "原始市", "原始区县", "原始地址",
        "校核省", "校核市", "校核区县", "校核地址",
        "适用规则", "状态", "备注",
    ])
    from openpyxl.cell import WriteOnlyCell
    for r in results:
        status = "需人工复核" if r["review_needed"] else "已校核"
        fill = yellow if r["review_needed"] else (green if r["rules"] else None)
        values = [
            r.get("id", ""), r.get("phone", ""), r.get("name", ""), r.get("gender", ""),
            r["原始省"], r["原始市"], r.get("原始区县", ""), r["原始地址"],
            r["校核省"], r["校核市"], r.get("校核区县", ""), r["校核地址"],
            ",".join(r["rules"]) or "—", status,
            "; ".join(r["notes"] + r["review_reasons"]) or "",
        ]
        if fill:
            cells = []
            for v in values:
                c = WriteOnlyCell(out_ws, value=v)
                c.fill = fill
                cells.append(c)
            out_ws.append(cells)
        else:
            out_ws.append(values)
    out_wb.save(out_path)

    # 中间证据 JSON
    if args.evidence:
        args.evidence.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    auto = sum(1 for r in results if not r["review_needed"])
    review = len(results) - auto
    rule_cnt: dict[str, int] = {}
    for r in results:
        for rl in r["rules"]:
            rule_cnt[rl] = rule_cnt.get(rl, 0) + 1
    print(f"✓ 完成 {len(results)} 行 -> {out_path}")
    print(f"  自动校核 {auto}, 需人工复核 {review}, workers={workers}, R8 调用 {r8_called}")
    if r9_stats["target_count"]:
        print(
            f"  R9 audio revisit: 重听 {r9_stats['target_count']} 条, "
            f"消除复核 {r9_stats['fixed']}, 学习字典新增 {r9_stats['learned_count']} 条, "
            f"失败 {r9_stats['fail']}"
        )
    for k in sorted(rule_cnt):
        print(f"  R{k[1:]}: {rule_cnt[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
