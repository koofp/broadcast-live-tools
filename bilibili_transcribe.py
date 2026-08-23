# -*- coding: utf-8 -*-
"""bilive 旁路式"语音转文字 + AI 整场总结"扩展（独立于录制阻塞，可离线自测）

设计目标：
1. 不侵入 bilive 主流水线（不动 src/），作为独立脚本/服务旁路运行
2. 输入：任意视频文件（mp4/flv）或 bilive 的成片目录
3. 输出：
   - 同目录 .srt 字幕（本地 Whisper 或 groq API 两种后端）
   - 同目录 .summary.md 整场总结（可选 LLM，OpenAI 兼容接口/Ollama）
4. 幂等：已存在 .srt/.summary.md 则跳过（支持断点续跑）

用法：
  python bilibili_transcribe.py <video_or_dir> [--backend local|groq] [--llm ollama|openai|off]
  # 例：python bilibili_transcribe.py D:/.../Videos/1832485943 --backend groq --llm ollama
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

SRT_EXT = ".srt"
SUMMARY_EXT = ".summary.md"

# ---------- 工具 ----------
def ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def extract_audio(video: str, out_wav: str, rate: int = 16000) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-ac", "1", "-ar", str(rate), out_wav],
        check=True, capture_output=True,
    )
    return out_wav


# ---------- 后端 1：groq whisper API ----------
def transcribe_groq(video: str, api_key: str, srt_path: str) -> bool:
    """用 groq 免费 whisper-large-v3-turbo 转写（≤40MB/次，约半小时）。"""
    tmp_wav = video + ".tmp.wav"
    extract_audio(video, tmp_wav)
    try:
        # groq 也接受音频直传；此处用 requests 风格 via urllib 走 multipart 略繁，
        # 生产中建议 pip install groq 后改用官方 SDK。这里给出可运行版本。
        import mimetypes
        boundary = "----biliveboundary%d" % os.getpid()
        fname = os.path.basename(tmp_wav)
        ctype = mimetypes.guess_type(tmp_wav)[0] or "audio/wav"
        with open(tmp_wav, "rb") as f:
            data = f.read()
        body = b""
        for k, v in [("model", "whisper-large-v3-turbo"), ("response_format", "srt")]:
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode() + data + ("\r\n--%s--\r\n" % boundary).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/transcriptions", data=body,
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "multipart/form-data; boundary=" + boundary},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            srt = r.read().decode("utf-8", "ignore")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt)
        return bool(srt.strip())
    finally:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)


# ---------- 后端 2：本地 whisper（openai-whisper / faster-whisper 可选） ----------
def transcribe_local(video: str, srt_path: str, model: str = "small") -> bool:
    """本地 whisper 转写。依赖: pip install openai-whisper（或 faster-whisper 改一下）。
    输出 srt。语言自动检测，中文输出 zh-cn。"""
    try:
        import whisper
    except ImportError:
        print("本地后端需要: pip install openai-whisper")
        return False
    model_obj = whisper.load_model(model)
    result = model_obj.transcribe(video, language=None, verbose=False)
    segs = result.get("segments", [])
    if not segs:
        return False
    def ts(sec):
        total = float(sec)
        h = int(total // 3600); m = int(total % 3600 // 60); s = total % 60
        return ("%02d:%02d:%06.3f" % (h, m, s)).replace(".", ",")   # 修复：原实现截断毫秒且对元组误调 replace 必崩
    lines = []
    for i, seg in enumerate(segs, 1):
        text = seg["text"].strip()
        if text:
            lines.append(f"{i}\n{ts(seg['start'])} --> {ts(seg['end'])}\n{text}\n")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


# ---------- LLM 总结（可选） ----------
def summarize_srt(srt_path: str, summary_path: str, llm: str, base: str = None, key: str = None) -> bool:
    if llm == "off":
        return False
    with open(srt_path, encoding="utf-8") as f:
        srt = f.read()
    if len(srt) > 60000:
        srt = srt[:60000] + "\n...(截断)"
    prompt = ("你是一个直播内容分析师。根据下面的直播字幕（SRT），输出结构化总结：\n"
              "## 主题\n## 关键信息（要点列表）\n## 时间线亮点\n## 一句话总结\n\n字幕：\n" + srt)
    if llm == "ollama":
        url = base or "http://localhost:11434/api/generate"
        payload = json.dumps({"model": "qwen2.5:7b", "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read().decode())
        text = d.get("response", "")
    elif llm in ("openai", "groq"):
        url = base or ("https://api.groq.com/openai/v1/chat/completions" if llm == "groq"
                       else "https://api.openai.com/v1/chat/completions")
        payload = json.dumps({
            "model": "llama-3.3-70b-versatile" if llm == "groq" else "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + (key or os.environ.get("GROQ_API_KEY") or "")})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read().decode())
        text = d["choices"][0]["message"]["content"]
    else:
        return False
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


# ---------- 主流程 ----------
def process_video(video: str, backend: str, llm: str, groq_key: str, local_model: str):
    base = video.rsplit(".", 1)[0]
    srt_path = base + SRT_EXT
    summary_path = base + SUMMARY_EXT
    ok = True
    if not os.path.exists(srt_path):
        if backend == "groq":
            ok = transcribe_groq(video, groq_key, srt_path)
        else:
            ok = transcribe_local(video, srt_path, local_model)
        print("srt ->", srt_path, "ok" if ok else "FAIL")
    else:
        print("skip srt (exists):", srt_path)
    if ok and llm != "off" and not os.path.exists(summary_path):
        ok2 = summarize_srt(srt_path, summary_path, llm)
        print("summary ->", summary_path, "ok" if ok2 else "skip")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="视频文件或目录（递归处理视频）")
    ap.add_argument("--backend", choices=["local", "groq"], default="local")
    ap.add_argument("--llm", choices=["ollama", "openai", "groq", "off"], default="off")
    ap.add_argument("--groq-key", default=os.environ.get("GROQ_API_KEY", ""))
    ap.add_argument("--local-model", default="small")
    ap.add_argument("--llm-base", default=None)
    ap.add_argument("--llm-key", default=os.environ.get("OPENAI_API_KEY", ""))
    args = ap.parse_args()

    if not ffmpeg_available():
        print("需要 ffmpeg（含 ffprobe）在 PATH。Windows 可 winget install ffmpeg 或装进项目。")
        return
    target = args.target
    if os.path.isdir(target):
        videos = []
        for root, _, files in os.walk(target):
            for f in files:
                if f.lower().endswith((".mp4", ".flv", ".mkv")):
                    videos.append(os.path.join(root, f))
    else:
        videos = [target]
    for v in videos:
        process_video(v, args.backend, args.llm, args.groq_key, args.local_model)
    print("done: %d files" % len(videos))


if __name__ == "__main__":
    main()