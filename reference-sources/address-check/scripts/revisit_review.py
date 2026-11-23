#!/usr/bin/env python3
"""对 batch_verify 输出的"需复核/省市区不完整"行做录音重听深挖。

输入:
  - batch_verify 生成的 evidence.json
  - 原 Excel（含"通话录音"URL 列）
输出:
  - markdown 深挖报告（每条一节：本地校核 / 上游 ASR / 重听转写 / 候选地址）
  - 增量 JSON: 在原 evidence 上加 'revisit' 字段

用法:
  python3 revisit_review.py \
      --evidence /tmp/200-evidence.json \
      --input '/path/to/原表.xlsx' --sheet Sheet1 \
      --url-col 通话录音 --id-col 通话记录id \
      --report /tmp/200-revisit.md [--cache-dir /tmp/revisit] [--limit 20]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import openpyxl

THIS_DIR = Path(__file__).parent
TRANSCRIBE = THIS_DIR / "transcribe.py"

# 触发词：地址核对相关
ADDR_KEYWORDS = re.compile(
    r"(省|市|区|县|州|盟|旗|镇|乡|街道|村|组|路|号|栋|楼|单元|室|户|社区|小区|大道|新村|花园|公寓)"
)

# 海南省直辖县级单位（市字段留空合法）
HAINAN_DIRECT = {"万宁市", "东方市", "儋州市", "三沙市", "五指山市", "文昌市",
                 "琼海市", "澄迈县", "屯昌县", "定安县", "临高县", "陵水黎族自治县",
                 "保亭黎族苗族自治县", "白沙黎族自治县", "琼中黎族苗族自治县",
                 "昌江黎族自治县", "乐东黎族自治县"}


def needs_revisit(rec: dict[str, Any]) -> str | None:
    """判断是否需要重听。返回原因字符串，或 None 表示不需要。"""
    if rec.get("review_needed"):
        return "需人工复核"
    dist = (rec.get("校核区县") or "").strip()
    city = (rec.get("校核市") or "").strip()
    prov = (rec.get("校核省") or "").strip()
    # 海南省直辖县级：市空但 dist 是合法直辖单位 → 合法，跳过
    if not city and dist and (prov == "海南省" or dist in HAINAN_DIRECT):
        return None
    if not dist:
        return "校核区县空"
    if not city:
        return "校核市空"
    return None


def download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
            f.write(r.read())
        return True
    except Exception as e:
        print(f"  ! 下载失败 {url[:50]}...: {e}", file=sys.stderr)
        return False


def transcribe(mp3: Path, out_txt: Path) -> str | None:
    if out_txt.exists() and out_txt.stat().st_size > 100:
        return out_txt.read_text(encoding="utf-8")
    cmd = ["python3", str(TRANSCRIBE), str(mp3), "--output", str(out_txt)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except Exception as e:
        print(f"  ! ASR 失败: {e}", file=sys.stderr)
        return None
    return out_txt.read_text(encoding="utf-8") if out_txt.exists() else None


def extract_transcript_only(asr_log: str) -> str:
    """从 transcribe.py 的输出里抽取顶部 transcript 行。"""
    m = re.search(r"=== TRANSCRIPT ===\s*\n([^\n]+)", asr_log)
    return m.group(1).strip() if m else ""


def slice_address_windows(transcript: str, max_chars: int = 80) -> list[str]:
    """从全文中抽取含 ADDR_KEYWORDS 的片段窗口（地址相关上下文）。"""
    windows: list[str] = []
    i = 0
    seen: set[str] = set()
    while i < len(transcript):
        m = ADDR_KEYWORDS.search(transcript, i)
        if not m:
            break
        start = max(0, m.start() - 12)
        end = min(len(transcript), m.start() + max_chars)
        seg = transcript[start:end].strip()
        # 去重 / 滑动
        if seg not in seen:
            seen.add(seg)
            windows.append(seg)
        i = m.start() + max_chars - 12
    return windows[:8]


def build_section(rec: dict[str, Any], reason: str, mp3_path: Path,
                  transcript: str, addr_windows: list[str]) -> str:
    lines: list[str] = []
    rid = rec.get("id", "")
    lines.append(f"### {rid} {rec.get('name', '')}  · {reason}")
    lines.append("")
    lines.append("**本地校核**")
    lines.append(f"- 省: `{rec.get('校核省', '')}`  市: `{rec.get('校核市', '')}`  "
                 f"区县: `{rec.get('校核区县', '')}`")
    lines.append(f"- 校核地址: `{rec.get('校核地址', '')}`")
    if rec.get("amap"):
        a = rec["amap"]
        lines.append(f"- amap: 省=`{a.get('province','')}` 市=`{a.get('city','')}` "
                     f"区县=`{a.get('district','')}` level=`{a.get('level','')}`")
    if rec.get("review_reasons"):
        lines.append(f"- review_reasons: {rec['review_reasons']}")
    lines.append("")
    lines.append("**上游 ASR（采集表"
                 "「通话记录」列摘要 ME 片段）**")
    me_lines = [s for s in (rec.get("evidence", {}).get("me") or [])
                if ADDR_KEYWORDS.search(s)]
    if me_lines:
        for x in me_lines[:8]:
            lines.append(f"  - `{x}`")
    else:
        lines.append("  - (无地址相关 ME 片段)")
    lines.append("")
    lines.append("**重听 ASR（sherpa-onnx SenseVoice int8 重转）**")
    lines.append(f"- mp3: `{mp3_path.name}`")
    if not transcript:
        lines.append("- ! 转写失败")
    else:
        lines.append("- 全文转写片段（含地址关键字的窗口）:")
        for w in addr_windows:
            lines.append(f"  - `{w}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", type=Path, required=True)
    ap.add_argument("--input", type=Path, required=True, help="原 Excel 路径")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--url-col", default="通话录音")
    ap.add_argument("--id-col", default="通话记录id")
    ap.add_argument("--cache-dir", type=Path, default=Path("/tmp/revisit"))
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=20, help="最多处理多少条")
    args = ap.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = json.loads(args.evidence.read_text(encoding="utf-8"))

    # 选目标行
    candidates = [(r, needs_revisit(r)) for r in records]
    candidates = [(r, why) for r, why in candidates if why]
    if not candidates:
        print("没有需要重听的行。")
        return 0
    print(f"需重听 {len(candidates)} 条，最多处理 {args.limit} 条")
    candidates = candidates[: args.limit]

    # 读 URL 映射
    wb = openpyxl.load_workbook(args.input, data_only=True, read_only=True)
    ws = wb[args.sheet] if args.sheet else wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    headers = list(next(rows_iter))
    idx = {h: i for i, h in enumerate(headers)}
    for col in (args.url_col, args.id_col):
        if col not in idx:
            print(f"列 {col} 缺失。可用: {headers}", file=sys.stderr)
            return 1
    url_by_id: dict[str, str] = {}
    for row in rows_iter:
        if not row:
            continue
        rid = row[idx[args.id_col]]
        if rid is None:
            continue
        url_by_id[str(rid)] = row[idx[args.url_col]] or ""
    wb.close()

    md_sections: list[str] = []
    for rec, reason in candidates:
        rid = rec.get("id", "")
        url = url_by_id.get(rid, "")
        print(f"\n[{reason}] id…{rid[-6:]} {rec.get('name', '')}")
        print(f"  url: {url[:60]}...")
        if not url:
            md_sections.append(build_section(rec, reason,
                Path("(no_url)"), "", []))
            continue
        mp3 = args.cache_dir / f"{rid[-10:]}.mp3"
        if not download(url, mp3):
            continue
        txt_log = args.cache_dir / f"{rid[-10:]}.asr.txt"
        log = transcribe(mp3, txt_log)
        transcript = extract_transcript_only(log) if log else ""
        windows = slice_address_windows(transcript)
        rec["revisit"] = {
            "reason": reason,
            "mp3": str(mp3),
            "transcript_len": len(transcript),
            "address_windows": windows,
        }
        md_sections.append(build_section(rec, reason, mp3, transcript, windows))
        print(f"  ✓ 转写 {len(transcript)} 字  地址窗口 {len(windows)} 个")

    args.report.write_text(
        "# 重听 ASR 深挖报告\n\n" + "\n---\n\n".join(md_sections),
        encoding="utf-8",
    )
    args.evidence.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告: {args.report}")
    print(f"evidence 已更新 (含 revisit 字段): {args.evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
