# -*- coding: utf-8 -*-
"""宿主机转录管线：faster-whisper → srt（幂等）
用法:
  python transcribe_host.py <视频...> [--model 路径或名称] [--language zh]
特性:
  - 模型整个进程只加载一次（批量快）
  - PyAV 直接读 mp4/flv，无需系统 ffmpeg
  - 已有同名 .srt 跳过；零语音写出占位 srt（防永久卡在待处理）
"""
import sys, time, os, argparse
from pathlib import Path

try:  # 防 GBK 管道下中文进度输出崩溃
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(video: str, model, language="zh"):
    video = str(Path(video).resolve())
    srt_path = Path(video).with_suffix(".srt")
    if srt_path.exists() and srt_path.stat().st_size > 0:
        print(f"[skip] {srt_path.name} 已存在", flush=True)
        return True
    t0 = time.time()
    segments, info = model.transcribe(
        video, language=language, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        beam_size=5,
    )
    lines, count = [], 0
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        count += 1
        lines.append(f"{count}\n{fmt_ts(seg.start)} --> {fmt_ts(seg.end)}\n{text}\n")
        if count % 50 == 0:
            print(f"  ...{fmt_ts(seg.end)} ({count}条)", flush=True)

    if not lines:
        # 零语音：写占位防止永久待处理
        lines = ["1\n00:00:00,000 --> 00:00:01,000\n[无语音内容]\n"]
        print(f"[warn] {os.path.basename(video)} 无语音，写占位 srt", flush=True)

    # tmp 带进程 PID：手动运行与计划任务无 run.lock 互斥，同名 tmp 会互相覆盖
    tmp = f"{srt_path}.tmp{os.getpid()}"
    Path(tmp).write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, srt_path)
    el = time.time() - t0
    print(f"[done] {count}条 -> {srt_path.name} | 音频{info.duration/60:.1f}分 | "
          f"耗时{el/60:.1f}分 | {info.duration/max(el,0.001):.2f}x实时", flush=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    default_model = Path(__file__).resolve().parent / "models" / "faster-whisper-small"
    ap.add_argument("--model", default=str(default_model),
                    help="本地模型目录或模型名（默认优先用仓库内 models/faster-whisper-small）")
    ap.add_argument("--language", default="zh")
    a = ap.parse_args()

    from faster_whisper import WhisperModel
    model_arg = a.model
    print(f"[load] faster-whisper: {model_arg} (int8/cpu)", flush=True)
    model = WhisperModel(model_arg, device="cpu", compute_type="int8")

    ok = 0
    for v in a.videos:
        try:
            if transcribe(v, model, a.language):
                ok += 1
        except Exception as e:
            print(f"[FAIL] {v}: {repr(e)[:200]}", flush=True)
    sys.exit(0 if ok == len(a.videos) else 1)


if __name__ == "__main__":
    main()
