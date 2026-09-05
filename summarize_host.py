# -*- coding: utf-8 -*-
"""srt → AI 总结（供应商走 provider_config：设置页 provider.json 优先，legacy 回退 OpenRouter）。
幂等：已有 .summary.md 跳过。
用法:
  python summarize_host.py <srt...> [--prompt-file prompt.txt]
key 来源（与端点/模型同源，详见 provider_config 模块注释）:
  设置页 provider.json > env OPENROUTER_API_KEY（历史名，泛指 LLM key）> api_key.txt > 注册表
"""
import json, sys, os, time, argparse
import urllib.request
import provider_config
from pathlib import Path

try:  # 防 GBK 管道下非 ASCII 输出崩溃（计划任务/Worker 重定向场景）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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


def _chat(prompt, key, model, chat_url, max_tokens):
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    # fetch：默认 opener 失败自动直连重试，网络级错误附排障提示（与设置页同口径）
    raw = provider_config.fetch(chat_url, body, headers, timeout=900)[0]
    d = json.loads(raw.decode("utf-8", "ignore"))
    choice = d["choices"][0]
    msg = choice.get("message", {}) or {}
    return (msg.get("content") or "", choice.get("finish_reason"),
            msg.get("reasoning_content") or "", d.get("usage", {}))


_LEGACY_WARNED = False


def call_llm(prompt, key=None, model=None, chat_url=None, max_tokens=None):
    """通用 LLM 调用：参数缺省时读 provider_config（设置页可配）。
    请求体保持 OpenAI messages 结构，兼容 new-api 等 OpenAI 兼容中继。"""
    global _LEGACY_WARNED
    p = provider_config.resolve()
    if p.get("model_deprecated") and not _LEGACY_WARNED:
        # legacy 链默认模型已实锤失效（provider.json 损坏自愈/新 clone 场景会静默落到这里）
        _LEGACY_WARNED = True
        print("[warn] legacy 通道生效且默认模型 stealth/ox-alpha 已失效——"
              "请在设置页「AI 供应商」把 key/base_url/模型配进 provider.json", flush=True)
    key = key or p["api_key"]
    model = model or p["model"]
    chat_url = chat_url or p["chat_url"]
    max_tokens = max_tokens or provider_config.DEFAULT_MAX_TOKENS
    if not key:
        raise RuntimeError("未配置 API key（设置页 AI 供应商 / OPENROUTER_API_KEY / api_key.txt / 注册表 均为空）")
    text, fr, reasoning, usage = _chat(prompt, key, model, chat_url, max_tokens)
    if not text and fr == "length":
        # think 模型可能把额度耗在思考上（content=null）→ 提高上限再试一次。
        # 不再要求 reasoning 非空：部分中继（实测 new-api + DeepSeek-V4-Pro-0813-think）
        # 不回传 reasoning_content 字段，思考token照样烧光——凭 finish=length 即应提额。
        text, fr, _, _ = _chat(prompt, key, model, chat_url, max(64000, max_tokens * 2))
    if not text:
        raise RuntimeError(f"空content finish={fr} usage={json.dumps(usage)[:160]}")
    return text


def call_oxalpha(prompt, key=None):
    """兼容旧调用名（session.py 复用）：委托 call_llm，参数走 provider_config。"""
    return call_llm(prompt, key=key)

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
            # tmp 带进程 PID：手动运行与计划任务无 run.lock 互斥，同名 tmp 会互相覆盖（实测竞争场景）
            tmp = f"{out}.tmp{os.getpid()}"
            Path(tmp).write_text(text, encoding="utf-8")
            os.replace(tmp, out)
            print(f"[done] {out.name} | {time.time()-t0:.0f}s", flush=True)
            return True
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code == 429:
                if attempt < 3:                 # 末次失败不再空睡
                    wait = 60 * (attempt + 1)   # 限流：60/120/180s
                    print(f"[429 rate-limited] {wait}s 后重试 ({attempt+1}/3)", flush=True)
                    time.sleep(wait)
            else:
                print(f"[http {e.code}] 不重试", flush=True)
                break
        except Exception as e:
            last_err = repr(e)[:200]
            if attempt < 3:                 # 末次失败不再空睡
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


def reconcile_retry() -> int:
    """retry.txt 对账：剔除已产出 summary 的条目并去重（治理历史残留/重复行）。
    每次调用 summarize_host 时先跑一遍，无论调用方是批处理还是面板 Worker。"""
    p = Path(__file__).resolve().parent / "retry.txt"
    if not p.exists():
        return 0
    kept = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        srt = ln.split("\t")[0]
        if srt and Path(srt).with_suffix(".summary.md").exists():
            continue
        if ln.strip():
            kept.append(ln)
    kept = list(dict.fromkeys(kept))
    if kept:
        p.write_text("\n".join(kept) + "\n", encoding="utf-8")
    else:
        p.unlink(missing_ok=True)
    return len(kept)


def pick_prompt(srt_path: Path, global_tpl: str) -> str:
    """按房间选提示词：prompt.<房间号>.txt > prompt.txt > 内置默认。
    房间号取 srt 所在目录名（Videos/<room>/）。让不同类型直播间
    （游戏/财经/聊天）使用各自的术语纠错倾向，互不干扰。"""
    room = srt_path.parent.name
    if room.isdigit():
        rp = Path(__file__).resolve().parent / f"prompt.{room}.txt"
        if rp.exists():
            tpl = rp.read_text(encoding="utf-8")
            if "{srt}" in tpl:
                print(f"[prompt] 使用房间专属提示词 {rp.name}", flush=True)
                return tpl
            print(f"[warn] {rp.name} 缺 {{srt}} 占位符，回退全局提示词", flush=True)
    return global_tpl


def get_api_key() -> str:
    """API key：优先设置页 provider.json，回退 env / api_key.txt / 注册表。
    交由 provider_config 统一解析，确保 summarize 与面板/场级总结口径一致。"""
    return provider_config.resolve()["api_key"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("srts", nargs="+")
    ap.add_argument("--prompt-file", default=str(Path(__file__).resolve().parent / "prompt.txt"))
    a = ap.parse_args()

    left = reconcile_retry()
    print(f"[retry] 对账后剩余 {left} 条待重试", flush=True)

    key = get_api_key()
    if not key:
        print("[fatal] 未配置 API key（设置页 AI 供应商 / OPENROUTER_API_KEY / api_key.txt / 注册表 均为空）", flush=True)
        sys.exit(1)

    pf = Path(a.prompt_file)
    tpl = pf.read_text(encoding="utf-8") if pf.exists() else DEFAULT_PROMPT
    if "{srt}" not in tpl:
        tpl = DEFAULT_PROMPT

    ok = 0
    for p in a.srts:
        try:
            sp = Path(p)
            if summarize(sp, pick_prompt(sp, tpl), key):
                ok += 1
                time.sleep(5)  # 批量调用间隔
        except Exception as e:
            print(f"[FAIL-ex] {p}: {repr(e)[:200]}", flush=True)
    sys.exit(0 if ok == len(a.srts) else 1)


if __name__ == "__main__":
    main()
