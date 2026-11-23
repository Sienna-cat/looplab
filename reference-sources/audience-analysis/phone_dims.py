# -*- coding: utf-8 -*-
"""
手机号信息拆解器(28 维)+ 省域映射表。
给定 11 位号码(+ 已知归属地省/市),离线拆出可分析的号码维度,供漏斗/ROI 交叉分析与号码分级使用。

维度清单见 dimension_catalog()。运营商/号段世代/号码类型/靓号沿用 phone_funnel_cross。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_funnel_cross as P  # generation / number_type / lucky_level / classify via operator_segments
from operator_segments import classify_operator

# ── 省域映射(短名,与解密数据一致) ───────────────────────────────
_REGION = {
    "北京": "华北", "天津": "华北", "河北": "华北", "山西": "华北", "内蒙古": "华北",
    "辽宁": "东北", "吉林": "东北", "黑龙江": "东北",
    "上海": "华东", "江苏": "华东", "浙江": "华东", "安徽": "华东", "福建": "华东", "江西": "华东", "山东": "华东",
    "河南": "华中", "湖北": "华中", "湖南": "华中",
    "广东": "华南", "广西": "华南", "海南": "华南",
    "重庆": "西南", "四川": "西南", "贵州": "西南", "云南": "西南", "西藏": "西南",
    "陕西": "西北", "甘肃": "西北", "青海": "西北", "宁夏": "西北", "新疆": "西北",
}
_COASTAL = {"辽宁", "河北", "天津", "山东", "江苏", "上海", "浙江", "福建", "广东", "广西", "海南"}
_NORTH = {"北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
          "山东", "河南", "陕西", "甘肃", "青海", "宁夏", "新疆"}
_CLUSTER = {
    "北京": "京津冀", "天津": "京津冀", "河北": "京津冀",
    "上海": "长三角", "江苏": "长三角", "浙江": "长三角", "安徽": "长三角",
    "广东": "珠三角", "重庆": "成渝", "四川": "成渝",
    "湖北": "长江中游", "湖南": "长江中游", "江西": "长江中游",
    "河南": "中原", "山东": "山东半岛", "福建": "海西",
}
_ECON = {  # 经济带分层(常规 GDP/消费分层,用于验证"白酒下沉"假说)
    "北京": "T1高线", "上海": "T1高线", "天津": "T1高线", "江苏": "T1高线",
    "浙江": "T1高线", "广东": "T1高线", "福建": "T1高线",
    "山东": "T2中线", "重庆": "T2中线", "湖北": "T2中线", "辽宁": "T2中线",
    "内蒙古": "T2中线", "陕西": "T2中线", "湖南": "T2中线", "四川": "T2中线",
    "安徽": "T2中线", "河北": "T2中线", "江西": "T2中线", "河南": "T2中线",
}  # 其余 → T3下沉


def region(prov):
    return _REGION.get(prov, "其他/未知")


def coastal(prov):
    return "沿海省" if prov in _COASTAL else ("内地省" if prov in _REGION else "未知")


def north_south(prov):
    if prov not in _REGION:
        return "未知"
    return "北方" if prov in _NORTH else "南方"


def cluster(prov):
    return _CLUSTER.get(prov, "其他城市群")


def econ_tier(prov):
    if prov not in _REGION:
        return "未知"
    return _ECON.get(prov, "T3下沉")


# ── 号码数字特征 ──────────────────────────────────────────────────
def tail8_kind(ph):
    """吉利尾号:含8不含4 / 含4 / 中性(基于后4位)。"""
    t = ph[7:]
    has8 = "8" in t or "6" in t or "9" in t
    has4 = "4" in t
    if has4:
        return "含4(忌)"
    if has8:
        return "含吉(6/8/9)"
    return "中性"


def is_straight(ph):
    """后4位顺子(升/降)。"""
    t = ph[7:]
    asc = all(ord(t[i + 1]) - ord(t[i]) == 1 for i in range(3))
    desc = all(ord(t[i]) - ord(t[i + 1]) == 1 for i in range(3))
    return "顺子号" if (asc or desc) else "非顺子"


def tail_parity(ph):
    return "尾偶" if int(ph[-1]) % 2 == 0 else "尾奇"


# ── 完整拆解(28 维) ─────────────────────────────────────────────
def extract(ph, prov="(未知)", city="(未知)"):
    valid = len(ph) == 11 and ph.isdigit()
    d = {}
    # A 切片
    d["前2位(世代)"] = ph[:2] if valid else "—"
    d["前3位(号段)"] = ph[:3] if valid else "—"
    d["前4位"] = ph[:4] if valid else "—"
    d["前7位(归属段)"] = ph[:7] if valid else "—"
    d["后4位"] = ph[7:] if valid else "—"
    # B 运营商
    d["运营商"] = classify_operator(ph) if valid else "其他/未知号段"
    d["号码类型"] = P.number_type(ph) if valid else "异常/非标"
    d["号段世代"] = P.generation(ph) if valid else "其他号段"
    # C 归属地
    d["归属地省"] = prov
    d["归属地市"] = f"{prov}·{city}"
    # D 地理衍生
    d["行政大区"] = region(prov)
    d["沿海/内地"] = coastal(prov)
    d["南北方"] = north_south(prov)
    d["城市群"] = cluster(prov)
    d["经济带分层"] = econ_tier(prov)
    # E 号龄(=世代)已含
    # F 靓号/数字
    d["靓号等级"] = P.lucky_level(ph) if valid else "普通号"
    d["顺子号"] = is_straight(ph) if valid else "非顺子"
    d["吉利尾号"] = tail8_kind(ph) if valid else "中性"
    d["尾号奇偶"] = tail_parity(ph) if valid else "—"
    return d


# 分析用的"有价值"维度(结合白酒下沉画像筛选;过细切片/弱信号不进交叉)
ANALYSIS_DIMS = [
    "运营商", "归属地省", "行政大区", "沿海/内地", "南北方", "城市群",
    "经济带分层", "号段世代", "号码类型", "靓号等级", "吉利尾号",
]
CROSS_PAIRS = [
    ("运营商", "归属地省"),
    ("经济带分层", "运营商"),
    ("沿海/内地", "号段世代"),
    ("行政大区", "号码类型"),
]


def dimension_catalog_md():
    return """# 晓黄 手机号信息拆解 · 维度目录(28 维)

> 从一个 11 位号码 + 归属地省市,离线可拆出的分析维度。✅=进入本轮交叉分析的高价值维度(结合白酒下沉画像筛选)。

## A. 号段字符串切片
| 维度 | 取法 | 说明 |
|---|---|---|
| 前2位(世代) | ph[:2] | 发号年代/世代 |
| 前3位(号段) | ph[:3] | 精细号段 |
| 前4位 | ph[:4] | 更细号段 |
| 前7位(归属段) | ph[:7] | 归属地查询键 |
| 后4位 | ph[7:] | 尾号/靓号判断 |

## B. 运营商类
| 维度 | 说明 |
|---|---|
| ✅ 运营商 | 移动/联通/电信/广电/虚拟/其他 |
| ✅ 号码类型 | 普通/虚拟MVNO/物联网卡 |
| ✅ 号段世代 | 13x最早→19x最新(≈号龄) |

## C. 归属地(7位号段库)
| 维度 | 说明 |
|---|---|
| ✅ 归属地省 | 省 |
| 归属地市 | 地市 |

## D. 地理/区域衍生(省→映射表;白酒下沉最值钱)
| 维度 | 说明 |
|---|---|
| ✅ 行政大区 | 华北/东北/华东/华中/华南/西南/西北 |
| ✅ 沿海/内地 | 沿海省 vs 内地省 |
| ✅ 南北方 | 秦岭淮河 |
| ✅ 城市群 | 京津冀/长三角/珠三角/成渝/长江中游/中原… |
| ✅ 经济带分层 | T1高线/T2中线/T3下沉 |
| 城市线级 | 一线/新一线/二/三/四五线(需地市) |

## E. 号龄
| 维度 | 说明 |
|---|---|
| 号段世代 | 同上,间接反映入网时长 |

## F. 靓号/数字规律
| 维度 | 说明 |
|---|---|
| ✅ 靓号等级 | 强靓/次靓/普通 |
| ✅ 吉利尾号 | 含吉(6/8/9)/含4(忌)/中性 |
| 顺子号 | 后4位连号 |
| 尾号奇偶 | 弱信号 |

## G. 活体信息(需接口,非离线)
| 维度 | 接口 |
|---|---|
| 空号/在网状态 | HLR/空号检测 |
| 在网时长/实名 | 运营商 |
| 风险号 | 黑名单库 |

> 离线稳定可拆 ≈ 22 维(A–F),叠加活体(G)更多。本轮交叉分析选用 11 个高价值维度 + 4 组关键交叉。
"""


if __name__ == "__main__":
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(ROOT, "output", "晓黄_手机号维度拆解.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(dimension_catalog_md())
    print(f"✓ 维度目录: {out}")
    print("示例拆解 13812345678 / 贵州·贵阳:")
    for k, v in extract("13812345678", "贵州", "贵阳").items():
        print(f"  {k}: {v}")
