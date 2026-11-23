"""中国行政区划反查 API（基于 GB/T 2260 简化版）。

数据来源: modood/Administrative-divisions-of-China
  - pca-code.json   31 省 / 343 地级 / 3056 县级 (默认加载)
  - pcas-code.json  扩展到街道/镇/乡级 (~3.3 万条, 按需懒加载)

公开 API:
    iter_divisions()              -> Iterator[(province, prefecture, county)]
    lookup_county(name, hint_province=None) -> list[(prefecture, province)]
    is_county(name)               -> bool
    is_prefecture(name)           -> bool
    province_of_prefecture(name)  -> str | None
    counties_of_prefecture(name)  -> list[county_name]
    counties_of_province(name)    -> list[county_name]
    canonical_prefecture(name)    -> str | None   # 黔西南州 → 黔西南布依族苗族自治州
    aliases_of_county(name)       -> set[str]

  -- R5 (区/县补全) 相关 --
    lookup_township(name, hint_prefecture=None, hint_province=None)
                                  -> list[(county, prefecture, province)]
    townships_of_county(prefecture, county) -> list[township_name]
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Iterator

DATA_FILE = Path(__file__).parent.parent / "data" / "pca-code.json"
DATA_FILE_STREET = Path(__file__).parent.parent / "data" / "pcas-code.json"


@lru_cache(maxsize=1)
def _load() -> list[dict]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


# modood pca-code.json 把直辖市的区、海南省的县级市等挂在虚拟父节点下，
# 这些节点名不能作为校核后的"地级市"出现。
_VIRTUAL_PREFECTURE = {"市辖区", "省直辖县级行政区划", "县"}


def _resolve_prefecture(prov_name: str, city_name: str) -> str:
    """虚拟父节点 → 升级为省名（这样校核值看起来正常）。"""
    if city_name in _VIRTUAL_PREFECTURE:
        return prov_name
    return city_name


@lru_cache(maxsize=1)
def _county_index() -> dict[str, list[tuple[str, str]]]:
    """完整县名 → [(prefecture, province), ...]
    若挂在 _VIRTUAL_PREFECTURE 下，prefecture 用省名替代。
    """
    idx: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for prov in _load():
        for city in prov.get("children", []):
            pref = _resolve_prefecture(prov["name"], city["name"])
            for area in city.get("children", []):
                idx[area["name"]].append((pref, prov["name"]))
    return dict(idx)


# "印江县" / "印江土家族苗族自治县" 之类的别名等价：
#   把民族自治县/旗的全名映射到口语短名，让"印江县"也能查到。
_AUTONOMOUS_SUFFIXES = (
    "土家族苗族自治县",
    "苗族侗族自治县",
    "藏族羌族自治县",
    "彝族自治县",
    "回族自治县",
    "壮族自治县",
    "苗族自治县",
    "侗族自治县",
    "瑶族自治县",
    "土家族自治县",
    "蒙古族自治县",
    "朝鲜族自治县",
    "哈萨克族自治县",
    "满族自治县",
    "撒拉族自治县",
    "黎族自治县",
    "畲族自治县",
    "水族自治县",
    "布依族苗族自治县",
    "各族自治县",
    "自治县",
    "自治旗",
    "土家族苗族自治州",
    "壮族苗族自治州",
)


# 通用民族自治县/旗/州别名剥离 regex
# 用已知民族名列表（覆盖宪法登记的 55 个少数民族常见命名形式）做后缀剥离，避免 regex 误吞 stem
# 兼容: "印江土家族苗族自治县" / "民和回族土族自治县" / "西畴苗族瑶族壮族自治县" / 双江拉祜族佤族布朗族傣族
_ETHNIC_NAMES = [
    "汉", "回", "壮", "苗", "藏", "土家", "彝", "维吾尔", "哈萨克", "朝鲜",
    "满", "蒙古", "布依", "白", "傣", "瑶", "侗", "土", "撒拉", "畲", "水",
    "黎", "羌", "佤", "拉祜", "纳西", "景颇", "傈僳", "阿昌", "普米", "怒",
    "德昂", "独龙", "基诺", "布朗", "门巴", "珞巴", "高山", "京", "塔吉克",
    "塔塔尔", "乌孜别克", "柯尔克孜", "锡伯", "达斡尔", "鄂温克", "鄂伦春",
    "赫哲", "毛南", "仫佬", "京", "仡佬", "东乡", "保安", "裕固", "各",
]
_AUTONOMOUS_RE = re.compile(
    r"^([\u4e00-\u9fff]+?)"
    r"((?:(?:" + "|".join(_ETHNIC_NAMES) + r")族)+)"
    r"(自治县|自治旗|自治州)$"
)


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, list[tuple[str, str]]]:
    """口语短名 → 全名候选 → (prefecture, province) 反查。
    例: 'X县' → 'X土家族苗族自治县' 列表。
    """
    base = _county_index()
    aliases: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for full_name, places in base.items():
        # 1) 旧的固定后缀表（保留兼容）
        for suf in _AUTONOMOUS_SUFFIXES:
            if full_name.endswith(suf) and len(full_name) > len(suf):
                stem = full_name[: -len(suf)]
                short = stem + "县"
                aliases[short].extend(places)
        # 2) 通用 regex 剥族组合 + 自治县/旗/州 (覆盖"民和回族土族自治县"这类)
        m = _AUTONOMOUS_RE.match(full_name)
        if m and m.group(2):  # 必须有"族"组合，避免吞噬普通"X自治县"
            stem = m.group(1)
            tail = m.group(3)
            short_suffix = "县" if tail in ("自治县", "自治旗") else "州"
            short = stem + short_suffix
            if short != full_name:
                aliases[short].extend(places)
                aliases[stem].extend(places)
    return dict(aliases)


# 民族自治州/盟 等"X 族 / X 自治州" 全名的口语简称——例如：
#   黔西南布依族苗族自治州 → {黔西南州, 黔西南}
_PREF_AUTO_SUFFIXES = (
    "布依族苗族自治州",
    "壮族苗族自治州",
    "苗族侗族自治州",
    "土家族苗族自治州",
    "藏族羌族自治州",
    "回族自治州",
    "彝族自治州",
    "藏族自治州",
    "傣族自治州",
    "傣族景颇族自治州",
    "白族自治州",
    "哈尼族彝族自治州",
    "傈僳族自治州",
    "蒙古族藏族自治州",
    "蒙古自治州",
    "柯尔克孜自治州",
    "哈萨克自治州",
    "自治州",
    "蒙古族自治州",
    "盟",
)


def _prefecture_aliases(name: str) -> list[str]:
    """给定 prefecture 全名，返回常见口语简称列表。"""
    out: list[str] = []
    for suf in _PREF_AUTO_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf):
            stem = name[: -len(suf)]
            out.append(stem + "州")  # 黔西南州
            out.append(stem)         # 黔西南
            break
    return out


@lru_cache(maxsize=1)
def _prefecture_index() -> dict[str, str]:
    """地级市/州/盟 → 省（不含虚拟节点；包含自治州的口语简称别名）"""
    idx: dict[str, str] = {}
    for prov in _load():
        for city in prov.get("children", []):
            cname = city["name"]
            if cname in _VIRTUAL_PREFECTURE:
                continue
            idx[cname] = prov["name"]
            for alias in _prefecture_aliases(cname):
                # 别名不覆盖已有的真实地级市
                idx.setdefault(alias, prov["name"])
    return idx


@lru_cache(maxsize=1)
def _prefecture_canonical() -> dict[str, str]:
    """口语简称 → 完整规范名 (黔西南州 → 黔西南布依族苗族自治州)。
    真实地级市自身映射到自己。
    """
    canon: dict[str, str] = {}
    for prov in _load():
        for city in prov.get("children", []):
            cname = city["name"]
            if cname in _VIRTUAL_PREFECTURE:
                continue
            canon[cname] = cname
            for alias in _prefecture_aliases(cname):
                canon.setdefault(alias, cname)
    return canon


def canonical_prefecture(name: str) -> str | None:
    """name 可能是口语简称，返回规范地级市名；无映射返回 None。"""
    return _prefecture_canonical().get(name)


@lru_cache(maxsize=1)
def _counties_of_prefecture() -> dict[str, list[str]]:
    """地级市 → 它的县/区列表；虚拟节点的县挂到所在省名下。"""
    out: dict[str, list[str]] = defaultdict(list)
    for prov in _load():
        for city in prov.get("children", []):
            pref = _resolve_prefecture(prov["name"], city["name"])
            for a in city.get("children", []):
                out[pref].append(a["name"])
    return dict(out)


# ---- Public API --------------------------------------------------------------


def iter_divisions() -> Iterator[tuple[str, str, str]]:
    """逐条产出 (省, 地级市, 县/区)。"""
    for prov in _load():
        for city in prov.get("children", []):
            for area in city.get("children", []):
                yield prov["name"], city["name"], area["name"]


def lookup_county(
    name: str,
    hint_province: str | None = None,
    hint_prefecture: str | None = None,
) -> list[tuple[str, str]]:
    """返回 county 的 (prefecture, province) 候选列表；不存在则空表。

    - hint_province 严格按省名过滤（容忍简称/全称的别名互换）
    - hint_prefecture 在多省同名（如"鼓楼区"在江苏南京/徐州/福建福州/河南开封都有）时
      做二级消歧；只要 prefecture 名与 hint 匹配即保留
    - 候选与 hint 都不匹配时返回空（信号"hint 与 county 不自洽"）
    """
    if not name:
        return []
    direct = _county_index().get(name, [])
    aliased = _alias_index().get(name, [])
    seen = set()
    merged: list[tuple[str, str]] = []
    for pair in direct + aliased:
        if pair in seen:
            continue
        seen.add(pair)
        merged.append(pair)

    if not merged:
        return merged

    # 一级过滤：province
    if hint_province:
        h_norm = (
            hint_province.replace("省", "")
            .replace("壮族自治区", "")
            .replace("回族自治区", "")
            .replace("维吾尔自治区", "")
            .replace("自治区", "")
            .replace("特别行政区", "")
            .strip()
        )
        if h_norm:
            by_prov: list[tuple[str, str]] = []
            for pref, prov in merged:
                prov_norm = (
                    prov.replace("省", "").replace("自治区", "").replace("特别行政区", "")
                    .replace("壮族", "").replace("回族", "").replace("维吾尔", "")
                )
                if h_norm in prov_norm or prov_norm in h_norm or h_norm in prov:
                    by_prov.append((pref, prov))
            merged = by_prov

    # 二级过滤：prefecture（解决多省同名后仍有多市同名，如"桥西区"在河北石家庄/邢台/张家口都有）
    if merged and hint_prefecture:
        p_norm = (
            hint_prefecture.replace("市", "")
            .replace("土家族苗族自治州", "")
            .replace("壮族苗族自治州", "")
            .replace("回族自治州", "")
            .replace("自治州", "")
            .replace("地区", "")
            .strip()
        )
        if p_norm:
            by_pref: list[tuple[str, str]] = []
            for pref, prov in merged:
                pref_norm = (
                    pref.replace("市", "").replace("自治州", "").replace("地区", "")
                    .replace("土家族", "").replace("苗族", "").replace("壮族", "")
                    .replace("回族", "").replace("布依族", "")
                )
                if p_norm in pref_norm or pref_norm in p_norm or p_norm in pref:
                    by_pref.append((pref, prov))
            if by_pref:
                merged = by_pref
            # 若 prefecture hint 命中 0 条，**保留 province 级筛选结果**（pref hint 可能就是错的）

    return merged


def is_county(name: str) -> bool:
    return name in _county_index() or name in _alias_index()


def is_prefecture(name: str) -> bool:
    return name in _prefecture_index()


def province_of_prefecture(name: str) -> str | None:
    return _prefecture_index().get(name)


def counties_of_prefecture(name: str) -> list[str]:
    """该地级市辖下的所有 county_name (用于做拼音模糊匹配的候选集)。"""
    return _counties_of_prefecture().get(name, [])


@lru_cache(maxsize=1)
def _counties_of_province() -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for prov in _load():
        for city in prov.get("children", []):
            for a in city.get("children", []):
                out[prov["name"]].append(a["name"])
    return dict(out)


def counties_of_province(name: str) -> list[str]:
    """该省辖下的所有 county/区/旗 名（不分地级市），用于全省范围拼音匹配。"""
    if not name:
        return []
    # 容忍简称：广西 → 广西壮族自治区
    direct = _counties_of_province().get(name)
    if direct:
        return direct
    for full_prov in _counties_of_province():
        if name in full_prov or full_prov in name:
            return _counties_of_province()[full_prov]
    return []


# ---- 街道 / 镇 / 乡 级反查 (R5) ----------------------------------------------

@lru_cache(maxsize=1)
def _load_streets() -> list[dict] | None:
    """懒加载 pcas-code.json。若文件不存在则返回 None（R5 自动跳过）。

    首次解析 JSON 后落 pickle 到同目录的 `pcas-code.pkl`，下次直接 unpickle，
    1.9MB JSON 解析 ~32ms → ~5ms（spawn 多进程下显著）。
    """
    if not DATA_FILE_STREET.exists():
        return None
    import pickle
    pkl = DATA_FILE_STREET.with_suffix(".pkl")
    # pickle 比源 JSON 新且体积合理 → 直接用
    if pkl.exists() and pkl.stat().st_mtime >= DATA_FILE_STREET.stat().st_mtime:
        try:
            with open(pkl, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass  # pickle 损坏则回退
    data = json.loads(DATA_FILE_STREET.read_text(encoding="utf-8"))
    try:
        with open(pkl, "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass  # 写不进去无所谓，只是没缓存
    return data


@lru_cache(maxsize=1)
def _township_index() -> dict[str, list[tuple[str, str, str]]]:
    """街道/镇/乡 名 → [(area, prefecture, province), ...]"""
    data = _load_streets()
    if data is None:
        return {}
    idx: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for prov in data:
        for city in prov.get("children", []):
            pref = _resolve_prefecture(prov["name"], city["name"])
            for area in city.get("children", []):
                for sub in area.get("children", []):
                    idx[sub["name"]].append((area["name"], pref, prov["name"]))
    return dict(idx)


@lru_cache(maxsize=1)
def _townships_of_county() -> dict[tuple[str, str], list[str]]:
    """(prefecture, county) → 该县/区下属街道/镇/乡列表"""
    data = _load_streets()
    if data is None:
        return {}
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    for prov in data:
        for city in prov.get("children", []):
            pref = _resolve_prefecture(prov["name"], city["name"])
            for area in city.get("children", []):
                key = (pref, area["name"])
                out[key].extend(sub["name"] for sub in area.get("children", []))
    return dict(out)


def lookup_township(
    name: str,
    hint_prefecture: str | None = None,
    hint_province: str | None = None,
) -> list[tuple[str, str, str]]:
    """街道/镇/乡 → [(area, prefecture, province), ...]。

    可选 hint_prefecture / hint_province 用于在同名时优先消歧。
    返回经 hint 过滤后的候选；若 hint 不匹配任何候选，返回空（信号"不自洽"）。
    """
    if not name:
        return []
    hits = _township_index().get(name, [])
    if not hits:
        return []
    if not hint_prefecture and not hint_province:
        return hits
    out: list[tuple[str, str, str]] = []
    for area, pref, prov in hits:
        ok = True
        if hint_prefecture:
            ok = ok and (hint_prefecture == pref or hint_prefecture in pref or pref in hint_prefecture)
        if hint_province and ok:
            h = hint_province.replace("省", "").replace("自治区", "").replace("特别行政区", "")
            prov_norm = prov.replace("省", "").replace("自治区", "").replace("特别行政区", "")
            ok = ok and (h in prov_norm or prov_norm in h)
        if ok:
            out.append((area, pref, prov))
    return out


def townships_of_county(prefecture: str, county: str) -> list[str]:
    return _townships_of_county().get((prefecture, county), [])


def has_street_data() -> bool:
    return _load_streets() is not None


def aliases_of_county(name: str) -> set[str]:
    """返回与 name 等价的所有可能写法。"""
    out: set[str] = {name}
    full = _county_index().get(name)
    if full is None:
        # 反查别名表
        full = _alias_index().get(name)
    # 找出全部全名
    for prov in _load():
        for city in prov.get("children", []):
            for area in city.get("children", []):
                an = area["name"]
                if an == name:
                    out.add(an)
                else:
                    for suf in _AUTONOMOUS_SUFFIXES:
                        if an.endswith(suf):
                            stem = an[: -len(suf)]
                            if name in (stem, stem + "县", an):
                                out.add(an)
                                out.add(stem + "县")
                                out.add(stem)
    return out


# ---- 自检 --------------------------------------------------------------------

if __name__ == "__main__":
    cases = [
        "江油市",     # 县级市 → 绵阳市
        "宁都县",     # 县 → 赣州市
        "印江县",     # 别名 → 印江土家族苗族自治县 → 铜仁市
        "杞县",       # 简短县名
        "朝阳区",     # 重名（北京/长春）
        "南山县",     # 不存在
    ]
    print("=== lookup_county 测试 ===")
    for c in cases:
        print(f"  {c}: {lookup_county(c)}")
    print()
    print("江油市 hint='四川省':", lookup_county("江油市", "四川省"))
    print("朝阳区 hint='吉林省':", lookup_county("朝阳区", "吉林省"))
    print()
    total_counties = sum(1 for _ in iter_divisions())
    print(f"总县级数: {total_counties}")

    if has_street_data():
        print(f"\n=== lookup_township 测试 ===")
        for t in ("安亭镇", "茅台镇", "黄坡镇", "武都镇", "陈家镇", "旧县乡", "长安镇"):
            print(f"  {t}: {lookup_township(t)[:5]}")
        print(f"\n  长安镇 hint_prefecture='东莞市':",
              lookup_township('长安镇', hint_prefecture='东莞市'))
        print(f"\n  安亭镇 hint_province='上海市':",
              lookup_township('安亭镇', hint_province='上海市'))
    else:
        print("\n(街道级数据 pcas-code.json 未下载，R5 反查不可用)")
