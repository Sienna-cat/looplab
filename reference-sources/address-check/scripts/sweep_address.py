#!/usr/bin/env python3
"""Multi-window / multi-enhancement ASR sweep around customer voice segments.

Use this AFTER transcribe.py: pick the time centers (in seconds) where the
customer (not the operator) is speaking address fields, and pass them via
--centers. The script tries 5 window sizes x 3 offsets x 4 enhancements per
center and prints all distinct ASR hypotheses.

Usage:
    python3 sweep_address.py /path/to/audio.mp3 --centers 118.5,135.5
                            [--output /tmp/sweep.txt]
                            [--model-dir ~/.cache/sherpa-onnx-models/...]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np


DEFAULT_MODEL_DIR = (
    Path.home()
    / ".cache"
    / "sherpa-onnx-models"
    / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
)

HALFS = (1.5, 2.0, 2.5, 3.0, 3.5)
OFFSETS = (-0.5, 0.0, 0.5)


def decode_mono16k(path: str):
    import av

    c = av.open(path)
    s = c.streams.audio[0]
    rs = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks = []
    for f in c.decode(s):
        for of in rs.resample(f):
            chunks.append(of.to_ndarray().flatten())
    flushed = rs.resample(None) or []
    for of in flushed:
        chunks.append(of.to_ndarray().flatten())
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)


def bandpass_norm(x, sr=16000, lo=200.0, hi=3400.0):
    n = len(x)
    if n == 0:
        return x
    X = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    X *= ((freqs >= lo) & (freqs <= hi))
    y = np.fft.irfft(X, n=n).astype(np.float32)
    peak = float(np.max(np.abs(y))) or 1.0
    return (y / peak * 0.95).astype(np.float32)


def gain(x, g):
    return (x * g).clip(-1.0, 1.0).astype(np.float32)


def build_recognizer(model_dir: Path):
    import sherpa_onnx

    model_path = model_dir / "model.int8.onnx"
    tokens_path = model_dir / "tokens.txt"
    if not model_path.exists() or not tokens_path.exists():
        sys.exit(
            f"Model not found in {model_dir}. Run scripts/setup.sh first."
        )
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model_path),
        tokens=str(tokens_path),
        num_threads=4,
        use_itn=True,
        language="zh",
        debug=False,
    )


def asr(rec, samples: np.ndarray) -> str:
    if len(samples) == 0:
        return ""
    s = rec.create_stream()
    s.accept_waveform(16000, samples)
    rec.decode_stream(s)
    return (s.result.text or "").strip()


def sweep_one_center(rec, pcm: np.ndarray, center: float, total_dur: float):
    out = []
    for half in HALFS:
        for off in OFFSETS:
            t0 = max(0.0, center - half + off)
            t1 = min(total_dur, center + half + off)
            seg = pcm[int(t0 * 16000):int(t1 * 16000)]
            for label, processed in [
                ("raw", seg),
                ("bp", bandpass_norm(seg)),
                ("g3", gain(seg, 3.0)),
                ("g3_bp", bandpass_norm(gain(seg, 3.0))),
            ]:
                txt = asr(rec, processed)
                if txt:
                    out.append((t0, t1, label, txt))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audio")
    ap.add_argument(
        "--centers",
        required=True,
        help="Comma-separated time centers in seconds (e.g. 118.5,135.5)",
    )
    ap.add_argument("--output", default=None)
    ap.add_argument(
        "--model-dir",
        default=str(DEFAULT_MODEL_DIR),
    )
    args = ap.parse_args()

    centers = [float(c.strip()) for c in args.centers.split(",") if c.strip()]
    pcm = decode_mono16k(args.audio)
    total = len(pcm) / 16000.0
    rec = build_recognizer(Path(os.path.expanduser(args.model_dir)))

    lines = []
    for i, c in enumerate(centers):
        hyps = sweep_one_center(rec, pcm, c, total)
        # de-dup texts but keep narrowest window
        seen = set()
        uniq = []
        for t0, t1, lbl, txt in hyps:
            if txt in seen:
                continue
            seen.add(txt)
            uniq.append((t0, t1, lbl, txt))

        lines.append(f"=== center={c:.1f}s  hypotheses={len(uniq)}/{len(hyps)} ===")
        for t0, t1, lbl, txt in uniq:
            lines.append(f"  [{t0:5.1f}-{t1:5.1f}s {lbl:6}] {txt}")
        lines.append("")

    blob = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(blob, encoding="utf-8")
        print(f"[sweep] wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(blob + "\n")


if __name__ == "__main__":
    main()
