# -*- coding: utf-8 -*-
"""宿主机转录管线：faster-whisper (small/int8) → srt
用法:
  python transcribe_host.py <video> [video2 ...] [--model small] [--language zh]
输出: 与视频同目录同名 .srt；已存在则跳过（幂等）
"""
import sys, time, os
from pathlib import Path
from faster_whisper import WhisperModel


def fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(video: str, model_size: str = "small", language: str = "zh") -> str:
    video = str(Path(video).resolve())
    srt_path = Path(video).with_suffix(".srt")
    if srt_path.exists():
        print(f"[skip] {srt_path.name} 已存在")
        return str(srt_path)

    t0 = time.time()
    print(f"[load] faster-whisper {model_size}/int8 (首次运行会下载权重)...", flush=True)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print(f"[transcribe] {os.path.basename(video)} ...", flush=True)
    segments, info = model.transcribe(
        video, language=language, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        beam_size=5,
    )

    lines = []
    count = 0
    for seg in segments:
        count += 1
        text = seg.text.strip()
        if text:
            lines.append(f"{count}\n{fmt_ts(seg.start)} --> {fmt_ts(seg.end)}\n{text}\n")
            if count % 50 == 0:
                print(f"  ...{fmt_ts(seg.end)} ({count} 条)", flush=True)

    tmp = str(srt_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, srt_path)

    el = time.time() - t0
    print(f"[done] {count} 条字幕 -> {srt_path}")
    print(f"[done] 音频时长 {info.duration/60:.1f} 分钟 | 转写耗时 {el/60:.1f} 分钟 | 速度 {info.duration/el:.2f}x 实时")
    return str(srt_path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+", help="视频文件（可多个）")
    ap.add_argument("--model", default="small", help="模型名或本地模型目录路径")
    a = ap.parse_args()
    for v in a.videos:
        transcribe(v, a.model)
