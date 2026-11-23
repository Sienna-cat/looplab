# -*- coding: utf-8 -*-
"""号段 → 运营商 映射表(手机号前3位)。
覆盖三大运营商 + 广电 + 虚拟运营商(MVNO)主要号段。
数据来源:工信部公开号段分配 + 行业通用整理。
"""

MOBILE = {
    "134", "135", "136", "137", "138", "139",
    "147", "148",
    "150", "151", "152", "157", "158", "159",
    "172", "178",
    "182", "183", "184", "187", "188",
    "198",
    "165",
    "1440",  # 物联网(按4位另判)
}

UNICOM = {
    "130", "131", "132",
    "145", "146",
    "155", "156",
    "166", "167",
    "175", "176",
    "185", "186",
    "196",
}

TELECOM = {
    "133",
    "149",
    "153",
    "162",
    "173", "174", "177",
    "180", "181", "189",
    "190", "191", "193", "199",
}

BROADCAST = {  # 中国广电
    "192",
}

# 虚拟运营商(MVNO)号段:170/171 段按 4 位细分;此处用 3 位粗判为虚拟
MVNO = {
    "170", "171",
}

# 物联网卡号段(13/14/16 位),非个人用户
IOT_PREFIX = {"140", "141", "144", "146", "148"}


def classify_operator(phone: str) -> str:
    """输入 11 位明文手机号,返回运营商。非明文返回 '需解密/异常'。"""
    if not phone:
        return "空号码"
    p = str(phone).strip()
    if not (len(p) == 11 and p.isdigit() and p.startswith("1")):
        return "需解密/非明文"
    pre3 = p[:3]
    if pre3 in MVNO:
        return "虚拟运营商"
    if pre3 in MOBILE:
        return "中国移动"
    if pre3 in UNICOM:
        return "中国联通"
    if pre3 in TELECOM:
        return "中国电信"
    if pre3 in BROADCAST:
        return "中国广电"
    return "其他/未知号段"


def phone_segment(phone: str) -> str:
    """返回号段(前3位),非明文返回 ''。"""
    if not phone:
        return ""
    p = str(phone).strip()
    if len(p) == 11 and p.isdigit() and p.startswith("1"):
        return p[:3]
    return ""


def is_md5(s: str) -> bool:
    if not s:
        return False
    s = str(s).strip()
    return len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s)
