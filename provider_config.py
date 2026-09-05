# -*- coding: utf-8 -*-
"""AI 供应商配置（OpenAI 兼容）：设置页可配 base_url / api_key / 模型列表 / active_model。

key 解析双链（评审定案：key 与端点/模型必须同源——OpenRouter key 打到 new-api
中继必 401，反之亦然）：
  ① provider.json 存有 api_key → 整套走 provider.json（base_url/model/key 同源）
  ② 否则走 legacy 链：env OPENROUTER_API_KEY（历史名，泛指 LLM key）> api_key.txt
     > 注册表 HKCU\\Environment；端点/模型同步回退 OpenRouter + stealth/ox-alpha，
     与 provider.json 引入前的行为完全一致
provider.json 含 key，已在 .gitignore 中，绝不入库。

new-api 中继是 OpenAI 兼容接口：base_url 形如 https://new-api.abrdns.com，
完整 chat 端点为 https://new-api.abrdns.com/v1/chat/completions。
"""
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROVIDER_FILE = ROOT / "provider.json"

# 默认供应商：new-api 中继（OpenAI 兼容）
DEFAULT_BASE_URL = "https://new-api.abrdns.com"
DEFAULT_MODEL = "DeepSeek-V4-Flash-0731-1M-think"
DEFAULT_MODELS = [DEFAULT_MODEL]
DEFAULT_MAX_TOKENS = 16000

# legacy 链（provider.json 无 key 时）：保持 provider.json 引入前的端点与模型不变。
# base_url 带 /api 前缀——resolve_chat_url 会补 /v1/chat/completions，
# 拼出 OpenRouter 正确端点 https://openrouter.ai/api/v1/chat/completions
LEGACY_BASE_URL = "https://openrouter.ai/api"
LEGACY_MODEL = "stealth/ox-alpha"

_LOCK = threading.RLock()   # load 播种 / save 读改写互斥（load 会调 save，需可重入）


def _legacy_key() -> tuple[str, str]:
    """旧 key 三级回退：env → api_key.txt → 注册表。返回 (key, 来源标签)。"""
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k, "环境变量"
    key_file = ROOT / "api_key.txt"
    if key_file.exists():
        k = key_file.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
        if k:
            return k, "api_key.txt"
    try:
        # errors="replace"：GBK 注册表输出遇 PYTHONUTF8=1 会在 reader 线程解码崩溃，key 静默丢失
        out = subprocess.run(["reg", "query", r"HKCU\Environment", "/v", "OPENROUTER_API_KEY"],
                             capture_output=True, text=True, errors="replace", timeout=8).stdout
        for ln in out.splitlines():
            if "OPENROUTER_API_KEY" in ln and "REG_SZ" in ln:
                return ln.split("REG_SZ")[-1].strip(), "注册表"
    except Exception:
        pass
    return "", ""


def _normalize(cfg: dict) -> dict:
    cfg.setdefault("base_url", DEFAULT_BASE_URL)
    cfg.setdefault("api_key", "")
    cfg.setdefault("models", list(DEFAULT_MODELS))
    cfg.setdefault("active_model", DEFAULT_MODEL)
    if not isinstance(cfg["models"], list) or not cfg["models"]:
        cfg["models"] = list(DEFAULT_MODELS)
    if not cfg["active_model"]:
        cfg["active_model"] = cfg["models"][0]
    if cfg["active_model"] not in cfg["models"]:
        cfg["models"].insert(0, cfg["active_model"])
    return cfg


def load() -> dict:
    """读取供应商配置；文件缺失/损坏 → 返回默认骨架并落盘。
    损坏件先改名 .json.bad 留档（内含 key 或可手工抢救），不被默认骨架直接覆盖。"""
    if PROVIDER_FILE.exists():
        try:
            return _normalize(json.loads(PROVIDER_FILE.read_text(encoding="utf-8-sig")))
        except Exception:
            try:
                os.replace(PROVIDER_FILE, PROVIDER_FILE.with_suffix(".json.bad"))
            except Exception:
                pass
    with _LOCK:
        if PROVIDER_FILE.exists():   # 并发下他人已完成播种，重读一次
            try:
                return _normalize(json.loads(PROVIDER_FILE.read_text(encoding="utf-8-sig")))
            except Exception:
                return _normalize({})
        d = _normalize({})
        tmp = PROVIDER_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, PROVIDER_FILE)
        return d


def save(cfg: dict):
    cfg = _normalize(dict(cfg))
    with _LOCK:
        tmp = PROVIDER_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, PROVIDER_FILE)


def resolve_chat_url(base_url: str) -> str:
    """把用户填的 base_url 规整成完整 chat 端点。
    兼容三种填法：https://host · https://host/v1 · https://host/v1/chat/completions"""
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def resolve() -> dict:
    """解析当前生效连接参数（双链同源，见模块 docstring）。
    返回 {base_url, api_key, model, chat_url, key_source}。"""
    cfg = load()
    key = (cfg.get("api_key") or "").strip()
    if key:
        base = (cfg.get("base_url") or DEFAULT_BASE_URL).strip()
        model = (cfg.get("active_model") or DEFAULT_MODEL).strip()
        return {"base_url": base.rstrip("/"), "api_key": key, "model": model,
                "chat_url": resolve_chat_url(base), "key_source": "provider.json"}
    key, src = _legacy_key()
    if key:
        # legacy key 属 OpenRouter 体系：端点/模型必须同步回退，混搭 new-api 中继必 401
        return {"base_url": LEGACY_BASE_URL, "api_key": key, "model": LEGACY_MODEL,
                "chat_url": resolve_chat_url(LEGACY_BASE_URL), "key_source": src}
    # 无任何 key：返回 provider.json 骨架，调用方给出完整来源指引
    base = (cfg.get("base_url") or DEFAULT_BASE_URL).strip()
    return {"base_url": base.rstrip("/"), "api_key": "",
            "model": (cfg.get("active_model") or DEFAULT_MODEL).strip(),
            "chat_url": resolve_chat_url(base), "key_source": ""}


def test_model(base_url: str, api_key: str, model: str, timeout: int = 90) -> dict:
    """向指定 model 发一条最小 chat 请求，回显 成功/失败+错误+延迟。
    成功判定：HTTP 200 且返回体含 choices（连接/鉴权/模型名均通过）。
    think 模型在 max_tokens 较小时可能只产出 reasoning_content（content=null），
    此时仍视为连接成功，并回显 finish_reason 供判断。"""
    chat_url = resolve_chat_url(base_url)
    key = (api_key or "").strip()
    if not key:
        return {"ok": False, "latency_ms": 0, "error": "未填写 API key"}
    body = json.dumps({
        "model": model,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode()
    req = urllib.request.Request(
        chat_url, data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        latency = int((time.time() - t0) * 1000)
        d = json.loads(raw)
        if "choices" in d and d["choices"]:
            msg = d["choices"][0].get("message", {}) or {}
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            finish = d["choices"][0].get("finish_reason")
            preview = (content or "").strip()
            if not preview and reasoning:
                preview = "(仅推理未产出正文) " + reasoning.strip()[:80]
            return {"ok": True, "latency_ms": latency,
                    "model": d.get("model", model), "finish_reason": finish,
                    "preview": preview[:120] or "(空回复)"}
        # 部分 new-api 系中继对配额不足/令牌异常返回 HTTP 200 + {"error": ...}——必须判失败
        if isinstance(d, dict) and d.get("error"):
            return {"ok": False, "latency_ms": latency,
                    "error": str(d["error"])[:120] or "中继返回 error",
                    "detail": json.dumps(d)[:200]}
        return {"ok": False, "latency_ms": latency,
                "error": "HTTP 200 但响应无 choices（中继异常）",
                "detail": json.dumps(d)[:200]}
    except urllib.error.HTTPError as e:
        latency = int((time.time() - t0) * 1000)
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        return {"ok": False, "latency_ms": latency,
                "error": f"HTTP {e.code}", "detail": detail}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                "error": repr(e)[:200]}