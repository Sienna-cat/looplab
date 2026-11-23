"""R9: 录音重听 + 自动学习。

从 batch_verify 主流程，对 review_needed=True（可选含 校核区县空）的行：
  1) 下载 mp3，调 audio skill 的 sherpa-onnx SenseVoice 重转写
  2) 从转写里抽取地址相关 token：省/市/县/区/旗/州/镇/街道/乡
  3) 自动裁决并修正本地校核值：
     - 转写里出现合法 county（在 hint_province 内）且本地校核区县缺失/不合法 → 采纳
     - 转写里出现 target_county 辖镇 + 本地 addr 含同音误识 token → 学习同音对
  4) 学习同音对落 data/learned_typo.json，下次启动 batch_verify 自动 merge 进 TYPO_DICT/PROV_TYPO_DICT
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 同级模块
from divisions import (
    counties_of_prefecture,
    has_street_data,
    is_county,
    is_prefecture,
    lookup_county,
    lookup_township,
    townships_of_county,
)
from phonetic import phon_score

TRANSCRIBE_PY = Path(__file__).parent / "transcribe.py"
LEARNED_FILE = Path(__file__).parent.parent / "data" / "learned_typo.json"

# 地址 token: 末尾后缀为 省/市/县/区/旗/州/镇/乡/街道
ADDR_TOKEN_PAT = re.compile(
    r"([\u4e00-\u9fff]{1,5}(?:省|市|县|区|旗|州|镇|乡|街道))"
)

# 各种后缀的"最短合理前缀长度"（避免单字 token 噪音 + 跨边界）
_SUFFIX_INFO: list[tuple[str, int]] = [
    ("街道", 2), ("镇", 1), ("乡", 1),
    ("自治州", 2), ("自治县", 2), ("自治旗", 2),
    ("县", 1), ("区", 1), ("旗", 1), ("市", 1), ("州", 1), ("省", 1),
]


def _is_known(seg: str) -> bool:
    """token 是否在任一合法库内（county / prefecture / township）。"""
    if seg.endswith(("县", "市", "区", "旗")):
        if is_county(seg) or is_prefecture(seg):
            return True
    if seg.endswith(("镇", "街道", "乡")):
        if lookup_township(seg):
            return True
    if seg.endswith("省"):
        # 仅作为前缀的省，不验
        return True
    return False


def _extract_tokens(text: str) -> list[str]:
    """从 text 抽取行政区划 token，**每个后缀位置只取一个"最优"**：

    - 优先返回所有合法长度里**最长**的（避免"亭镇"和"华亭镇"同时返回）
    - 若没合法，则返回**最长的**作为非法候选
    - inner 不允许含 县/市/区/旗/省/州 (避免跨边界)
    """
    out: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(text):
        matched_end = -1
        for suf, min_pref in _SUFFIX_INFO:
            if text.startswith(suf, i):
                end = i + len(suf)
                best_known: str | None = None
                best_any: str | None = None  # 最长合规 token (含非法)
                for L in range(min_pref + len(suf), 5 + len(suf) + 1):
                    start = end - L
                    if start < 0:
                        continue
                    seg = text[start:end]
                    inner = seg[: -len(suf)]
                    if not inner:
                        continue
                    if not all("\u4e00" <= c <= "\u9fff" for c in inner):
                        continue
                    if any(c in "县市区旗省州" for c in inner):
                        continue
                    # 取最长
                    if best_any is None or len(seg) > len(best_any):
                        best_any = seg
                    if _is_known(seg) and (best_known is None or len(seg) > len(best_known)):
                        best_known = seg
                pick = best_known or best_any
                if pick and pick not in seen:
                    seen.add(pick)
                    out.append(pick)
                matched_end = end
                break
        if matched_end < 0:
            i += 1
        else:
            i = matched_end
    return out


# ---- 学习字典持久化 -------------------------------------------------------

def load_learned() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """加载 learned_typo.json。

    返回 (global_dict, prov_aware_dict)。
    格式:
      {
        "global": {"华亭镇": "花坪镇", ...},
        "prov_aware": {"广西壮族自治区": {"新安县": "兴安县", ...}, ...}
      }
    """
    if not LEARNED_FILE.exists():
        return {}, {}
    try:
        data = json.loads(LEARNED_FILE.read_text(encoding="utf-8"))
        return dict(data.get("global", {})), dict(data.get("prov_aware", {}))
    except Exception:
        return {}, {}


def save_learned(global_d: dict[str, str], prov_d: dict[str, dict[str, str]]) -> None:
    LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEARNED_FILE.write_text(
        json.dumps(
            {"global": global_d, "prov_aware": prov_d},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ---- 下载 / ASR ----------------------------------------------------------

def _download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            f.write(r.read())
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  ! 下载失败 {url[:60]}...: {e}", file=sys.stderr)
        return False


def _transcribe(mp3: Path, out_txt: Path, timeout: float = 180) -> str | None:
    """调 audio skill 的 transcribe.py。返回 transcript 单行字符串或 None。"""
    if out_txt.exists() and out_txt.stat().st_size > 100:
        log = out_txt.read_text(encoding="utf-8")
    else:
        if not TRANSCRIBE_PY.exists():
            print(f"  ! audio skill 未安装: {TRANSCRIBE_PY}", file=sys.stderr)
            return None
        try:
            subprocess.run(
                ["python3", str(TRANSCRIBE_PY), str(mp3), "--output", str(out_txt)],
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except Exception as e:
            print(f"  ! ASR 失败 {mp3.name}: {e}", file=sys.stderr)
            return None
        log = out_txt.read_text(encoding="utf-8") if out_txt.exists() else ""
    m = re.search(r"=== TRANSCRIPT ===\s*\n([^\n]+)", log)
    return m.group(1).strip() if m else None


def _prewarm_transcribe(jobs: list[tuple[str, Path, Path]], timeout: float = 180,
                        download_workers: int = 8, verbose: bool = True) -> None:
    """R9 性能预热：并发下载所有 mp3 + **一次性加载模型批量转写**，写进 asr.txt 缓存。

    jobs = [(url, mp3_path, asr_log_path), ...]
    之后 revisit_one→_transcribe 会直接命中缓存（asr.txt 已存在），
    避免逐行重启 python 重载 158MB 模型（旧行为每行重载一次，是 R9 最大瓶颈）。

    任一环节失败都安全降级：未生成的 asr.txt 会在主循环里走原 per-row subprocess。
    """
    pending = [(u, mp3, log) for (u, mp3, log) in jobs
               if u and not (log.exists() and log.stat().st_size > 100)]
    if not pending:
        return

    # Phase 1: 并发下载 (I/O bound)
    from concurrent.futures import ThreadPoolExecutor
    to_dl = [(u, mp3) for (u, mp3, _log) in pending
             if not (mp3.exists() and mp3.stat().st_size > 1024)]
    if to_dl:
        if verbose:
            print(f"  R9 预热: 并发下载 {len(to_dl)} 个 mp3 ({download_workers} 线程)…",
                  flush=True)
        with ThreadPoolExecutor(max_workers=download_workers) as ex:
            list(ex.map(lambda t: _download(t[0], t[1]), to_dl))

    # Phase 2: 单进程内加载模型一次，批量转写 (CPU bound)
    ready = [(mp3, log) for (_u, mp3, log) in pending
             if mp3.exists() and mp3.stat().st_size > 1024]
    if not ready:
        return
    try:
        import transcribe as T
        from pathlib import Path as _P
        rec = T.build_recognizer(_P(T.DEFAULT_MODEL_DIR))
    except Exception as e:
        # 模型/依赖不可用 → 放弃批量预热，主循环按原 per-row subprocess 兜底
        if verbose:
            print(f"  R9 预热: 批量转写不可用，回退逐行 ({e})", flush=True)
        return
    if verbose:
        print(f"  R9 预热: 模型已加载一次，批量转写 {len(ready)} 条…", flush=True)
    for mp3, log in ready:
        if log.exists() and log.stat().st_size > 100:
            continue
        try:
            samples, _ = T.decode_to_pcm16k_mono(str(mp3))
            text, timestamps, tokens = T.transcribe(rec, samples)
            lines = ["=== TRANSCRIPT ===", text, "", "=== WORD TIMESTAMPS ==="]
            for ts, tok in zip(timestamps, tokens):
                lines.append(f"{ts:7.2f}s  {tok}")
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:
            print(f"  ! R9 预热转写失败 {mp3.name}: {e}", file=sys.stderr)


# ---- 地址 token 抽取与裁决 ----------------------------------------------

def _candidate_county_tokens(text: str) -> list[str]:
    """对文本里每个 县/市/区/旗 后缀位置, 返回 1-4 字前缀的**所有候选** (不只最长).

    用于 R9: 上下文里可能拼成 "山东陵县彭水", 单 token "山东陵县" 查不到, 但回退到
    "陵县" 即能命中字典。
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"[县市区旗]", text):
        end = m.end()
        for L in range(2, 6):  # 总长 2-5 字 (含后缀)
            start = end - L
            if start < 0:
                continue
            seg = text[start:end]
            inner = seg[:-1]
            if not inner:
                continue
            if not all("\u4e00" <= c <= "\u9fff" for c in inner):
                continue
            # inner 禁含 行政区划后缀字 + 常见连接字 (避免跨边界, "山东陵县" inner="山东陵" 含"东"，但"东"不是禁字)
            if any(c in "县市区旗省州" for c in inner):
                continue
            if seg in seen:
                continue
            seen.add(seg)
            out.append(seg)
    return out


def extract_county_candidates(
    transcript: str,
    prov_hint: str | None = None,
    typo_translator: callable | None = None,
) -> list[tuple[str, str, str]]:
    """从 transcript 抽取合法 county 名。返回 [(name, prefecture, province), ...]。

    - typo_translator(token, prov_hint) -> str: 可选回调, 先把 token 走 typo 字典 (PROV_TYPO_DICT
      + TYPO_DICT + learned_typo.json) 翻译, 再 lookup_county。
      用于救历史撤县/改区名 (如山东"陵县"→"陵城区")。
    - 枚举每个后缀位置的所有 2-5 字候选 (不只取最长), 提高 R9 在嘈杂上下文里的召回。
    """
    out: list[tuple[str, str, str]] = []
    seen_names: set[str] = set()
    for tok in _candidate_county_tokens(transcript):
        # 先走 typo 字典翻译 (历史名/同音误识)
        translated = tok
        if typo_translator:
            try:
                translated = typo_translator(tok, prov_hint) or tok
            except Exception:
                translated = tok
        if translated in seen_names:
            continue
        hits = lookup_county(translated, hint_province=prov_hint)
        if hits and len(hits) == 1:
            pref, prov = hits[0]
            out.append((translated, pref, prov))
            seen_names.add(translated)
        elif hits and prov_hint:
            for pref, prov in hits:
                if prov == prov_hint or pref == prov_hint:
                    out.append((translated, pref, prov))
                    seen_names.add(translated)
                    break
    return out


def extract_township_candidates(
    transcript: str, prefecture: str, county: str
) -> list[str]:
    """从 transcript 抽取 (prefecture, county) 下的合法辖镇/街道/乡。"""
    if not (prefecture and county) or not has_street_data():
        return []
    towns = set(townships_of_county(prefecture, county))
    if not towns:
        return []
    found: list[str] = []
    for tok in _extract_tokens(transcript):
        if tok in towns and tok not in found:
            found.append(tok)
    return found


def find_fake_tokens_in_addr(
    addr: str,
    allowed: set[str],
    target_pref: str = "",
    target_county: str = "",
) -> list[str]:
    """扫 addr 内行政区划 token，找出"疑似 typo"的：

    - 县/市/区/旗 token：全国库 is_county/is_prefecture 都不命中 → 视为伪
    - 镇/街道/乡 token：
        - 若给了 target_pref+target_county，则**不在该辖镇集合内**即视为伪
        - 否则退化到"全国库无"判定
    """
    out: list[str] = []
    valid_towns: set[str] | None = None
    if target_pref and target_county and has_street_data():
        valid_towns = set(townships_of_county(target_pref, target_county))
        if not valid_towns:
            valid_towns = None
    for tok in _extract_tokens(addr):
        if tok in allowed or tok in out:
            continue
        if tok.endswith(("县", "市", "区", "旗")):
            if not (is_county(tok) or is_prefecture(tok)):
                out.append(tok)
        elif tok.endswith(("镇", "街道", "乡")):
            if valid_towns is not None:
                if tok not in valid_towns:
                    out.append(tok)
            else:
                if not lookup_township(tok):
                    out.append(tok)
    return out


# ---- 单条裁决 -------------------------------------------------------------

def _extract_me_segments(orig_transcript: str) -> str:
    """从原 transcript_col (带 ME:/AI: 标签) 抽取客户原声段，拼接成单串。

    上游 ASR (来自厂商) 通常质量比我们本地 sherpa-onnx int8 重转写更高，且区分声道。
    优先用 ME: 段做地址证据来源。
    """
    if not orig_transcript:
        return ""
    me_lines: list[str] = []
    for line in orig_transcript.split("\n"):
        line = line.strip()
        if line.startswith("ME:") or line.startswith("ME："):
            me_lines.append(line.split(":", 1)[-1].split("：", 1)[-1].strip())
    return " | ".join(me_lines)


def revisit_one(
    rec: dict[str, Any],
    url: str,
    cache_dir: Path,
    transcribe_timeout: float = 180,
    typo_translator: callable | None = None,
) -> dict[str, Any]:
    """对单条记录跑重听 + 自动裁决（v2 双路 ASR 证据）。

    证据来源（优先级从高到低）:
      1. rec["transcript"] 里的 ME: 段 (上游 ASR, 区分声道, 质量更高)
      2. 本地 sherpa-onnx 重转写
      把两路转写拼接后一起抽 token，提高召回率。

    会**就地修改 rec**，并返回 dict 报告。
    """
    rid = rec.get("id", "unknown")
    orig_transcript = rec.get("transcript", "") or ""
    me_text = _extract_me_segments(orig_transcript)
    report: dict[str, Any] = {
        "transcript": "",
        "me_text": me_text,
        "county_found": [],
        "township_found": [],
        "fake_tokens_in_addr": [],
        "learned": [],
        "action": "",
    }
    if not url and not me_text:
        report["action"] = "缺录音 URL 且无 ME 段"
        return report
    transcript = ""
    if url:
        cache_dir.mkdir(parents=True, exist_ok=True)
        mp3 = cache_dir / f"{rid[-10:]}.mp3"
        asr_log = cache_dir / f"{rid[-10:]}.asr.txt"
        if _download(url, mp3):
            transcript = _transcribe(mp3, asr_log, timeout=transcribe_timeout) or ""
    if not transcript and not me_text:
        report["action"] = "ASR 失败且无 ME 段"
        return report
    report["transcript"] = transcript
    # 双路证据合并 (ME 段优先, 重转写补充)
    transcript = (me_text + " " + transcript).strip() if me_text else transcript

    prov_hint = (rec.get("校核省") or "").strip() or None
    local_city = (rec.get("校核市") or "").strip()
    local_dist = (rec.get("校核区县") or "").strip()
    local_addr = rec.get("校核地址", "") or ""

    # 转写里发现的合法 county
    county_hits = extract_county_candidates(transcript, prov_hint, typo_translator)
    report["county_found"] = [
        {"name": n, "prefecture": p, "province": pr} for n, p, pr in county_hits
    ]

    rules: list[str] = rec.setdefault("rules", [])
    notes: list[str] = rec.setdefault("notes", [])
    actions: list[str] = []

    # === 裁决①: 本地区县缺失/与转写不一致 ===
    if county_hits:
        # 多个候选时优先 prov_hint 内的
        in_hint = [h for h in county_hits if not prov_hint or h[2] == prov_hint]
        candidates_for_action = in_hint or county_hits
        # 若 hint_province 内有 ≥2 个不同 county 候选 → 客户原声本身模糊 (如 058802 "陵县/临县")，
        # 不自动改，但把候选写入备注供人工电访
        unique_names = list({h[0] for h in candidates_for_action})
        if len(unique_names) > 1:
            review_reasons = rec.setdefault("review_reasons", [])
            tag = f"R9 多候选 (录音里客户提到): {' / '.join(unique_names)}"
            if tag not in review_reasons:
                review_reasons.append(tag)
            notes.append(f"R9 audio: 客户原声出现多个 county 候选 {unique_names}，需人工电访裁决")
            if "R9" not in rules:
                rules.append("R9")
            rec["review_needed"] = True
            report["action"] = f"多候选保留 review: {unique_names}"
            return report
        best = candidates_for_action[0]
        new_dist_name, new_pref, new_prov_full = best
        # 本地 dist 是否在 hint_province 内合法（自洽）
        local_consistent = bool(local_dist and (
            not prov_hint or lookup_county(local_dist, hint_province=prov_hint)
        ))
        if not local_dist:
            rec["校核区县"] = new_dist_name
            if not local_city and new_pref != new_prov_full:
                rec["校核市"] = new_pref
            if "R9" not in rules:
                rules.append("R9")
            notes.append(f"R9 audio: 区县={new_dist_name} (录音转写)")
            actions.append(f"补区县={new_dist_name}")
            if new_dist_name not in local_addr:
                if new_pref and new_pref in local_addr:
                    local_addr = local_addr.replace(new_pref, new_pref + new_dist_name, 1)
                elif new_prov_full in local_addr:
                    local_addr = local_addr.replace(
                        new_prov_full, new_prov_full + (new_pref if new_pref != new_prov_full else "") + new_dist_name, 1
                    )
                else:
                    local_addr = new_prov_full + (new_pref if new_pref != new_prov_full else "") + new_dist_name + local_addr
                rec["校核地址"] = local_addr
            local_dist = new_dist_name
        elif local_dist != new_dist_name and not local_consistent:
            # 本地区县在 hint_province 内不自洽 + 转写里有合法的 → 用转写
            # 同时记录学习对 (县级 typo): {local_dist → new_dist_name} 限于该 prov
            rec["校核区县"] = new_dist_name
            if not local_city and new_pref != new_prov_full:
                rec["校核市"] = new_pref
            if "R9" not in rules:
                rules.append("R9")
            actions.append(f"修区县 {local_dist}→{new_dist_name}")
            # 在 addr 主体里替换 local_dist → new_dist_name
            # 防重复保护: 若 new_dist_name 已在 addr 字面 (如 city 列就是它),
            # 直接删除 local_dist 而不是替换 (避免出现"永城市永城市"重复堆栈)
            if local_dist in local_addr:
                stripped = local_addr.replace(local_dist, "", 1)
                if new_dist_name in stripped:
                    local_addr = stripped
                    notes.append(f"R9 audio: 删除冗余 {local_dist} (new={new_dist_name} 已在 addr/city)")
                else:
                    local_addr = local_addr.replace(local_dist, new_dist_name)
                    notes.append(f"R9 audio: 区县 {local_dist}→{new_dist_name} (录音转写,hint 不自洽)")
                rec["校核地址"] = local_addr
            else:
                notes.append(f"R9 audio: 区县 {local_dist}→{new_dist_name} (录音转写,hint 不自洽)")
            # 学习: 仅在 prov_hint 下做 (避免污染合法的同名县)
            if prov_hint:
                report["learned_county"] = {
                    "bad": local_dist, "good": new_dist_name, "prov": prov_hint
                }
            local_dist = new_dist_name

    # === 裁决②: 学习同音对（本地 addr 含非法 token，转写里有近音合法 token）===
    target_county = (rec.get("校核区县") or "").strip()
    target_pref = (rec.get("校核市") or "").strip() or prov_hint
    township_hits = extract_township_candidates(transcript, target_pref or "", target_county)
    report["township_found"] = township_hits

    fake_tokens = find_fake_tokens_in_addr(
        local_addr,
        allowed={target_pref, target_county, prov_hint or ""} - {""},
        target_pref=target_pref or "",
        target_county=target_county or "",
    )
    report["fake_tokens_in_addr"] = fake_tokens

    learned: list[dict[str, Any]] = []
    if township_hits and fake_tokens:
        # 找到最相似的同音对（拼音 ≥ 0.6）
        for bad in fake_tokens:
            if not bad.endswith(("镇", "街道", "乡")):
                continue  # 仅学镇级（县级已通过 hint_province 自动消歧）
            best_good = None
            best_sc = 0.0
            for good in township_hits:
                if bad == good:
                    continue
                sc = phon_score(bad, good)
                if sc > best_sc:
                    best_sc = sc
                    best_good = good
            if best_good and best_sc >= 0.5:
                learned.append({
                    "bad": bad,
                    "good": best_good,
                    "score": round(best_sc, 3),
                    "scope": "prov",  # 默认 prov 条件字典，更稳健
                    "prov": prov_hint or "",
                })
                # 同时把 addr 主体的 bad 替换为 good
                if bad in local_addr:
                    local_addr = local_addr.replace(bad, best_good)
                    rec["校核地址"] = local_addr
                    if "R9" not in rules:
                        rules.append("R9")
                    notes.append(f"R9 audio learn: {bad}→{best_good} (phon={best_sc:.2f}, prov={prov_hint})")
                    actions.append(f"学{bad}→{best_good}")
    report["learned"] = learned

    # === 收尾: 若 review_needed=True 且本次有动作 → 重新评估 ===
    if rec.get("review_needed") and actions:
        # 若 review_reasons 全部是"区县不一致/缺市区/详细地址为空"类，且本次修过 → 消除 review
        remaining = []
        for reason in rec.get("review_reasons", []):
            if "区县不一致" in reason or "缺市" in reason or "为空" in reason or "hint 不自洽" in reason:
                continue
            remaining.append(reason)
        rec["review_reasons"] = remaining
        if not remaining:
            rec["review_needed"] = False
            notes.append("R9 audio: review_needed 自动消除")

    report["action"] = " / ".join(actions) if actions else "无修正"
    return report


# ---- 批量入口 -------------------------------------------------------------

def apply_r9_batch(
    records: list[dict[str, Any]],
    url_by_id: dict[str, str],
    cache_dir: Path,
    scope: str = "review",
    transcribe_timeout: float = 180,
    verbose: bool = True,
    typo_translator: callable | None = None,
) -> dict[str, Any]:
    """对 records 中需要重听的行批量调用 revisit_one。

    scope:
      "review": 仅 review_needed=True
      "gaps":   review_needed=True OR 校核区县空 OR 校核市空（非直辖白名单）
      "all":    所有行（慎用）
    返回汇总统计 dict。
    """
    HAINAN_DIRECT = {
        "万宁市", "东方市", "儋州市", "三沙市", "五指山市", "文昌市",
        "琼海市", "澄迈县", "屯昌县", "定安县", "临高县", "陵水黎族自治县",
        "保亭黎族苗族自治县", "白沙黎族自治县", "琼中黎族苗族自治县",
        "昌江黎族自治县", "乐东黎族自治县",
    }

    def _has_fake_township(r: dict[str, Any]) -> bool:
        """addr 主体里是否有"与 target_county 辖镇不一致"的镇 token。

        即便 token 全国合法（如"华亭镇"在福建/安徽/上海有），只要不在 target_county
        的辖镇里，就值得重听验证。R8 amap 可能误判 OK，但 addr 里残留同音错字。
        """
        addr = r.get("校核地址", "") or ""
        target_county = (r.get("校核区县") or "").strip()
        target_pref = (r.get("校核市") or "").strip() or (r.get("校核省") or "").strip()
        if not target_county or not has_street_data():
            return False
        towns = set(townships_of_county(target_pref, target_county))
        if not towns:
            return False
        for tok in _extract_tokens(addr):
            if not tok.endswith(("镇", "街道", "乡")):
                continue
            if tok not in towns:
                return True
        return False

    def need(r: dict[str, Any]) -> str | None:
        if scope == "all":
            return "all"
        if r.get("review_needed"):
            return "review"
        if scope == "learn" and _has_fake_township(r):
            return "addr含非法镇"
        if scope not in ("gaps", "learn"):
            return None
        dist = (r.get("校核区县") or "").strip()
        city = (r.get("校核市") or "").strip()
        prov = (r.get("校核省") or "").strip()
        if not city and dist and (prov == "海南省" or dist in HAINAN_DIRECT):
            return None
        if not dist:
            return "区县空"
        if not city:
            return "市空"
        if scope == "learn" and _has_fake_township(r):
            return "addr含非法镇"
        return None

    targets = [(r, need(r)) for r in records]
    targets = [(r, why) for r, why in targets if why]
    if not targets:
        return {"target_count": 0, "fixed": 0, "learned_count": 0, "fail": 0}

    if verbose:
        print(f"\n  R9 audio revisit: {len(targets)} 条待重听 (scope={scope})")

    # 性能预热：并发下载 + 一次性加载模型批量转写，填充 asr.txt 缓存。
    # 之后逐行 revisit_one 直接命中缓存，避免每行重载 158MB 模型。
    cache_dir.mkdir(parents=True, exist_ok=True)
    prewarm_jobs: list[tuple[str, Path, Path]] = []
    for r, _why in targets:
        rid = r.get("id", "?")
        url = url_by_id.get(rid, "")
        if url:
            prewarm_jobs.append((url, cache_dir / f"{rid[-10:]}.mp3",
                                 cache_dir / f"{rid[-10:]}.asr.txt"))
    if prewarm_jobs:
        _prewarm_transcribe(prewarm_jobs, timeout=transcribe_timeout, verbose=verbose)

    # 加载已学字典
    learned_global, learned_prov = load_learned()
    new_learned_count = 0
    fixed = 0
    fail = 0

    for r, why in targets:
        rid = r.get("id", "?")
        url = url_by_id.get(rid, "")
        if verbose:
            print(f"    [{why}] id…{rid[-6:]} {r.get('name','')}", end=" ", flush=True)
        was_review = r.get("review_needed", False)
        rpt = revisit_one(
            r, url, cache_dir,
            transcribe_timeout=transcribe_timeout,
            typo_translator=typo_translator,
        )
        if rpt.get("action") in ("缺录音 URL", "下载失败", "ASR 失败"):
            fail += 1
            if verbose:
                print(f"✗ {rpt['action']}")
            continue
        if verbose:
            print(f"→ {rpt['action']}")
        # 收集学习: 镇级同音对
        for ld in rpt.get("learned", []):
            bad, good = ld["bad"], ld["good"]
            if ld["scope"] == "global":
                if bad not in learned_global:
                    learned_global[bad] = good
                    new_learned_count += 1
            else:
                prov = ld.get("prov", "")
                if prov:
                    learned_prov.setdefault(prov, {})
                    if bad not in learned_prov[prov]:
                        learned_prov[prov][bad] = good
                        new_learned_count += 1
        # 收集学习: 县级 hint 内 typo
        lc = rpt.get("learned_county")
        if lc and lc.get("prov"):
            learned_prov.setdefault(lc["prov"], {})
            if lc["bad"] not in learned_prov[lc["prov"]]:
                learned_prov[lc["prov"]][lc["bad"]] = lc["good"]
                new_learned_count += 1
        if was_review and not r.get("review_needed"):
            fixed += 1

    if new_learned_count:
        save_learned(learned_global, learned_prov)
        if verbose:
            print(f"  R9 学习字典: 新增 {new_learned_count} 条 → {LEARNED_FILE.name}")

    return {
        "target_count": len(targets),
        "fixed": fixed,
        "learned_count": new_learned_count,
        "fail": fail,
    }
