#!/usr/bin/env python3
"""Full-file Chinese ASR with sherpa-onnx SenseVoice int8.

Outputs transcript + word-level timestamps. Auto-detects dual-mono.

Usage:
    python3 transcribe.py /path/to/audio.mp3 [--output /tmp/asr.log]
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


def decode_to_pcm16k_mono(path: str):
    """Decode any audio file to float32 mono 16 kHz numpy array via PyAV.

    Returns:
        samples (np.ndarray[float32]), is_dual_mono (bool)
    """
    import av

    container = av.open(path)
    stream = container.streams.audio[0]
    n_ch = stream.channels
    sr_in = stream.rate

    rs_mono = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks_mono = []
    chunks_per_ch = [[] for _ in range(n_ch)]

    rs_per_ch = None
    if n_ch == 2:
        # planar format ensures to_ndarray() returns shape (channels, samples)
        rs_per_ch = av.AudioResampler(format="fltp", layout="stereo", rate=16000)

    for frame in container.decode(stream):
        for of in rs_mono.resample(frame):
            chunks_mono.append(of.to_ndarray().flatten())
        if rs_per_ch is not None:
            for of in rs_per_ch.resample(frame):
                arr = of.to_ndarray()
                if arr.ndim == 1:
                    # packed fallback: interleaved L,R,L,R
                    chunks_per_ch[0].append(arr[0::2])
                    chunks_per_ch[1].append(arr[1::2])
                elif arr.shape[0] >= 2:
                    chunks_per_ch[0].append(arr[0])
                    chunks_per_ch[1].append(arr[1])

    flushed = rs_mono.resample(None) or []
    for of in flushed:
        chunks_mono.append(of.to_ndarray().flatten())

    samples = np.concatenate(chunks_mono).astype(np.float32) if chunks_mono else np.zeros(0, np.float32)

    is_dual_mono = False
    if rs_per_ch is not None and chunks_per_ch[0] and chunks_per_ch[1]:
        l = np.concatenate(chunks_per_ch[0])
        r = np.concatenate(chunks_per_ch[1])
        n = min(len(l), len(r))
        if n > 0 and np.allclose(l[:n], r[:n], atol=1e-3):
            is_dual_mono = True

    print(
        f"[decode] sr_in={sr_in}Hz channels={n_ch} dual_mono={is_dual_mono} "
        f"duration={len(samples)/16000:.2f}s",
        file=sys.stderr,
    )
    return samples, is_dual_mono


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


def transcribe(recognizer, samples: np.ndarray):
    s = recognizer.create_stream()
    s.accept_waveform(16000, samples)
    recognizer.decode_stream(s)
    text = s.result.text
    timestamps = list(getattr(s.result, "timestamps", []) or [])
    tokens = list(getattr(s.result, "tokens", []) or [])
    return text, timestamps, tokens


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audio")
    ap.add_argument("--output", default=None, help="Path to write transcript log")
    ap.add_argument(
        "--model-dir",
        default=str(DEFAULT_MODEL_DIR),
        help=f"SenseVoice model dir (default: {DEFAULT_MODEL_DIR})",
    )
    args = ap.parse_args()

    samples, _ = decode_to_pcm16k_mono(args.audio)
    rec = build_recognizer(Path(os.path.expanduser(args.model_dir)))

    text, timestamps, tokens = transcribe(rec, samples)

    out_lines = []
    out_lines.append("=== TRANSCRIPT ===")
    out_lines.append(text)
    out_lines.append("")
    out_lines.append("=== WORD TIMESTAMPS ===")
    for ts, tok in zip(timestamps, tokens):
        out_lines.append(f"{ts:7.2f}s  {tok}")

    blob = "\n".join(out_lines) + "\n"

    if args.output:
        Path(args.output).write_text(blob, encoding="utf-8")
        print(f"[transcribe] wrote {args.output} ({len(blob)} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(blob)


if __name__ == "__main__":
    main()
