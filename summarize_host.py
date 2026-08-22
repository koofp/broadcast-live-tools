# -*- coding: utf-8 -*-
"""对 srt 做 AI 总结（OpenRouter ox-alpha），输出 summary.md 到同目录。宿主机版。"""
import json, sys, time, urllib.request, os
from pathlib import Path

KEY = os.environ.get("OPENROUTER_API_KEY", "***REMOVED***")
PROMPT = """你是资深直播内容分析师。以下字幕来自语音识别（whisper small），可能含同音误听，
请结合语境自行纠正（游戏术语、DOTA2英雄名、装备名等）。

字幕：
{srt}

请输出（Markdown）：
## 一句话总结
## 核心主题（不超过20字）
## 讨论要点（按时间顺序，标注[mm:ss]，每条不超过25字）
## 金句/名场面（如有，含时间戳）
## 疑似识别错误对照表（原文→推测正确词）
"""

def summarize(srt_path: str):
    srt_path = Path(srt_path)
    out = srt_path.with_suffix(".summary.md")
    if out.exists():
        print("[skip]", out.name)
        return
    srt = srt_path.read_text(encoding="utf-8")
    body = json.dumps({
        "model": "stealth/ox-alpha",
        "max_tokens": 16000,
        "reasoning": {"effort": "low"},
        "messages": [{"role": "user", "content": PROMPT.replace("{srt}", srt)}],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        raw = urllib.request.urlopen(req, timeout=600).read().decode("utf-8", "ignore")
        d = json.loads(raw)
    except Exception:
        Path(str(out) + ".rawerr").write_text(raw[:3000], encoding="utf-8")
        print("[fail] 原始响应已存", str(out) + ".rawerr"); raise
    msg = d["choices"][0]["message"]
    text = msg.get("content")
    if not text:
        print("[warn] content为空 | finish:", d["choices"][0].get("finish_reason"),
              "| usage:", json.dumps(d.get("usage", {}))[:200])
        sys.exit(2)
    text = text.strip() or sys.exit(2)
    out.write_text(text, encoding="utf-8")
    u = d.get("usage", {})
    print(f"[done] {out.name} | {time.time()-t0:.0f}s | tokens {u.get('total_tokens')}")

if __name__ == "__main__":
    for p in sys.argv[1:]:
        summarize(p)
