# -*- coding: utf-8 -*-
"""srt → AI 总结（OpenRouter ox-alpha）。幂等：已有 .summary.md 跳过。
用法:
  python summarize_host.py <srt...> [--prompt-file prompt.txt]
环境变量: OPENROUTER_API_KEY 必须预先设置（不再写入源码）
"""
import json, sys, os, time, argparse
import urllib.request
from pathlib import Path

DEFAULT_PROMPT = """你是资深直播内容分析师。以下字幕来自语音识别（whisper small），可能含同音误听，
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

MAX_PROMPT_CHARS = 100000  # 超长截断保护（>6h 直播才可能触发）


def call_oxalpha(prompt: str, key: str):
    body = json.dumps({
        "model": "stealth/ox-alpha",
        "max_tokens": 16000,
        "reasoning": {"effort": "low"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=900).read().decode("utf-8", "ignore")
    d = json.loads(raw)
    msg = d["choices"][0]["message"]
    text = msg.get("content")
    if not text:
        raise RuntimeError(f"空content finish={d['choices'][0].get('finish_reason')} "
                           f"usage={json.dumps(d.get('usage', {}))[:160]}")
    return text


def summarize(srt_path: Path, prompt_tpl: str, key: str) -> bool:
    out = srt_path.with_suffix(".summary.md")
    if out.exists():
        print(f"[skip] {out.name} 已存在", flush=True)
        return True
    srt = srt_path.read_text(encoding="utf-8")
    if "[无语音内容]" in srt:
        out.write_text("（该分段无语音内容，未生成总结）", encoding="utf-8")
        print("[skip] 占位srt → 写入空总结占位", flush=True)
        return True
    if len(srt) > MAX_PROMPT_CHARS:
        print(f"[warn] 字幕超长({len(srt)}字符)，截断至{MAX_PROMPT_CHARS}", flush=True)
        srt = srt[:MAX_PROMPT_CHARS] + "\n…(后文截断)"
    if "{srt}" not in prompt_tpl:
        print("[error] 提示词缺少 {srt} 占位符", flush=True)
        return False
    prompt = prompt_tpl.replace("{srt}", srt)

    last_err = None
    for attempt in range(4):  # 3次重试；429限流用长退避
        try:
            t0 = time.time()
            text = call_oxalpha(prompt, key)
            tmp = str(out) + ".tmp"
            Path(tmp).write_text(text, encoding="utf-8")
            os.replace(tmp, out)
            print(f"[done] {out.name} | {time.time()-t0:.0f}s", flush=True)
            return True
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code == 429:
                wait = 60 * (attempt + 1)   # 限流：60/120/180s
                print(f"[429 rate-limited] {wait}s 后重试 ({attempt+1}/3)", flush=True)
                time.sleep(wait)
            else:
                print(f"[http {e.code}] 不重试", flush=True)
                break
        except Exception as e:
            last_err = repr(e)[:200]
            wait = 5 * (2 ** attempt)
            print(f"[retry {attempt+1}/3] {last_err} — {wait}s 后重试", flush=True)
            time.sleep(wait)
    print(f"[FAIL] {srt_path.name}: {last_err}", flush=True)
    retry_log = Path(__file__).resolve().parent / "retry.txt"   # 统一写到根目录
    try:
        with open(retry_log, "a", encoding="utf-8") as f:
            f.write(f"{srt_path}\t{last_err}\n")
    except Exception:
        pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("srts", nargs="+")
    ap.add_argument("--prompt-file", default=str(Path(__file__).resolve().parent / "prompt.txt"))
    a = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("[fatal] 未设置 OPENROUTER_API_KEY 环境变量", flush=True)
        sys.exit(1)

    pf = Path(a.prompt_file)
    tpl = pf.read_text(encoding="utf-8") if pf.exists() else DEFAULT_PROMPT
    if "{srt}" not in tpl:
        tpl = DEFAULT_PROMPT

    ok = 0
    for p in a.srts:
        try:
            if summarize(Path(p), tpl, key):
                ok += 1
                time.sleep(5)  # 批量调用间隔
        except Exception as e:
            print(f"[FAIL-ex] {p}: {repr(e)[:200]}", flush=True)
    sys.exit(0 if ok == len(a.srts) else 1)


if __name__ == "__main__":
    main()
