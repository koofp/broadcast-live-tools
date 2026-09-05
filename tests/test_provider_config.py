# -*- coding: utf-8 -*-
"""provider_config 纯逻辑回归（verify.ps1 会调用）：双链解析/base_url 规整/
_normalize 边界/test_model 假阳性防护/损坏文件留档。全部隔离真实 provider.json，无网络。"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import provider_config as pc  # noqa: E402


def test_resolve_chat_url():
    cases = {
        "https://new-api.abrdns.com": "https://new-api.abrdns.com/v1/chat/completions",
        "https://new-api.abrdns.com/": "https://new-api.abrdns.com/v1/chat/completions",
        "https://x.example/v1": "https://x.example/v1/chat/completions",
        "https://x.example/v1/chat/completions": "https://x.example/v1/chat/completions",
        "": "https://new-api.abrdns.com/v1/chat/completions",
    }
    for raw, want in cases.items():
        got = pc.resolve_chat_url(raw)
        assert got == want, f"resolve_chat_url({raw!r}) = {got!r}, 期望 {want!r}"


def test_normalize():
    d = pc._normalize({"api_key": "k", "models": [], "active_model": ""})
    assert d["models"] == [pc.DEFAULT_MODEL], "空 models 应回退默认"
    d = pc._normalize({"api_key": "k", "active_model": "m2", "models": ["m1", "m2"]})
    assert d["active_model"] == "m2" and d["models"] == ["m1", "m2"]
    d = pc._normalize({"api_key": "k", "active_model": "m9", "models": ["m1"]})
    assert d["models"][0] == "m9", "active_model 不在列表应插队头部"
    d = pc._normalize({"api_key": "k", "models": "not-a-list"})
    assert d["models"] == [pc.DEFAULT_MODEL], "非列表 models 应回退默认"


def test_resolve_dual_chain():
    """P1 回归：key 与端点/模型必须同源——legacy key 回退时不得携带 provider.json 的
    new-api 端点/模型（否则 OpenRouter key 打到 new-api 中继必 401）。"""
    old_load, old_legacy = pc.load, pc._legacy_key
    try:
        # 链①：provider.json 有 key → 整套走 provider.json
        pc.load = lambda: {"base_url": "https://x.example", "api_key": "sk-new",
                           "models": ["m1"], "active_model": "m1"}
        r = pc.resolve()
        assert r["chat_url"] == "https://x.example/v1/chat/completions"
        assert r["model"] == "m1" and r["key_source"] == "provider.json"
        # 链②：provider.json 无 key → 整套走 legacy（OpenRouter + ox-alpha）
        pc.load = lambda: {"base_url": "https://x.example", "api_key": "",
                           "models": ["m1"], "active_model": "m1"}
        pc._legacy_key = lambda: ("sk-or-legacy", "api_key.txt")
        r = pc.resolve()
        assert r["chat_url"] == "https://openrouter.ai/api/v1/chat/completions", \
            f"legacy key 必须配 legacy 端点（OpenRouter 带 /api 前缀），实测 {r['chat_url']}"
        assert r["model"] == pc.LEGACY_MODEL and r["key_source"] == "api_key.txt"
        # 链③：到处无 key → api_key 为空但结构完整
        pc._legacy_key = lambda: ("", "")
        r = pc.resolve()
        assert r["api_key"] == "" and r["chat_url"].startswith("https://")
    finally:
        pc.load, pc._legacy_key = old_load, old_legacy


def test_test_model_no_false_positive():
    """P2 回归：HTTP 200 但无 choices / 带 error 的中继响应必须判失败。"""
    real_urlopen = pc.urllib.request.urlopen
    try:
        def fake(body):
            pc.urllib.request.urlopen = lambda req, timeout: io.BytesIO(body)
        fake(json.dumps({"error": {"message": "quota exceeded"}}).encode())
        r = pc.test_model("https://x.example", "k", "m")
        assert r["ok"] is False and "quota" in r["error"], f"error 体应判失败: {r}"
        fake(json.dumps({"object": "list"}).encode())
        r = pc.test_model("https://x.example", "k", "m")
        assert r["ok"] is False, "无 choices 应判失败"
        fake(json.dumps({"choices": [{"message": {"content": "pong"},
                                      "finish_reason": "stop"}]}).encode())
        r = pc.test_model("https://x.example", "k", "m")
        assert r["ok"] is True and r["preview"] == "pong"
        r = pc.test_model("https://x.example", "", "m")
        assert r["ok"] is False and "key" in r["error"]
    finally:
        pc.urllib.request.urlopen = real_urlopen


def test_load_save_roundtrip_and_corrupt():
    with tempfile.TemporaryDirectory() as td:
        old = pc.PROVIDER_FILE
        pf = Path(td) / "provider.json"
        pc.PROVIDER_FILE = pf
        try:
            d = pc.load()   # 缺失 → 播种默认骨架
            assert pf.exists() and d["base_url"] == pc.DEFAULT_BASE_URL
            pc.save({"api_key": "k1", "models": ["m1"], "active_model": "m1"})
            d = pc.load()
            assert d["api_key"] == "k1" and d["active_model"] == "m1"
            pf.write_text("{corrupt!!", encoding="utf-8")   # 损坏 → .bad 留档 + 重建骨架
            d = pc.load()
            assert d["models"] == [pc.DEFAULT_MODEL]
            assert pf.with_suffix(".json.bad").exists(), "损坏件应留档 .json.bad"
            assert json.loads(pf.read_text(encoding="utf-8-sig"))["api_key"] == ""
        finally:
            pc.PROVIDER_FILE = old


def test_models_url_and_list_models():
    """模型列表在线获取（GET /v1/models）：URL 规整 + 解析 + 异常分支。"""
    cases = {
        "https://new-api.abrdns.com": "https://new-api.abrdns.com/v1/models",
        "https://x.example/v1": "https://x.example/v1/models",
        "https://x.example/v1/chat/completions": "https://x.example/v1/models",
        "https://openrouter.ai/api": "https://openrouter.ai/api/v1/models",
        "": "https://new-api.abrdns.com/v1/models",
    }
    for raw, want in cases.items():
        got = pc.resolve_models_url(raw)
        assert got == want, f"resolve_models_url({raw!r}) = {got!r}, 期望 {want!r}"

    real_urlopen = pc.urllib.request.urlopen
    try:
        def fake(body):
            pc.urllib.request.urlopen = lambda req, timeout: io.BytesIO(body)
        fake(json.dumps({"data": [{"id": "m2"}, {"id": "m1"}, {"id": "m1"},
                                   {"id": ""}, "bad-item"]}).encode())
        r = pc.list_models("https://x.example", "k")
        assert r["ok"] is True and r["models"] == ["m1", "m2"], f"去重排序失败: {r}"
        fake(json.dumps({"error": {"message": "invalid token"}}).encode())
        r = pc.list_models("https://x.example", "k")
        assert r["ok"] is False and "invalid token" in r["error"]
        fake(b"not json")
        r = pc.list_models("https://x.example", "k")
        assert r["ok"] is False, "非 JSON 响应应判失败"
        r = pc.list_models("https://x.example", "  ")
        assert r["ok"] is False and "key" in r["error"]
    finally:
        pc.urllib.request.urlopen = real_urlopen


def test_fetch_fallback():
    """网络级错误 → 无代理直连重试；HTTPError 不重试直接上抛；全败附 Clash 提示。"""
    real_urlopen, real_direct, real_hosts = pc.urllib.request.urlopen, pc._DIRECT_OPENER, set(pc._HOST_DIRECT)
    pc._HOST_DIRECT.clear()
    try:
        # ① 默认 opener 网络错误、直连成功 → via=直连 且主机被记住
        pc.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            __import__("urllib.error", fromlist=["URLError"]).URLError("proxy dead"))
        class _OkOpener:
            def __init__(self): self.calls = 0
            def open(self, req, timeout): self.calls += 1; return io.BytesIO(b"direct-ok")
        ok = _OkOpener(); pc._DIRECT_OPENER = ok
        body, via = pc._fetch("https://x.example/v1", None, {}, 5)
        assert body == b"direct-ok" and via == "直连" and ok.calls == 1
        # ② 已学过的主机跳过代理尝试
        body2, via2 = pc._fetch("https://x.example/v1", None, {}, 5)
        assert via2 == "直连" and ok.calls == 2
        # ③ 两级全败 → RuntimeError 且信息含 Clash 排障提示
        pc._DIRECT_OPENER = type("F", (), {"open": lambda self, r, timeout: (_ for _ in ()).throw(
            OSError("eof"))})()
        try:
            pc._fetch("https://y.example/v1", None, {}, 5)
            assert False, "应抛 RuntimeError"
        except RuntimeError as e:
            assert "Clash" in str(e) and "DIRECT" in str(e)
        # ④ HTTPError（已拿到服务端响应）不重试，原样上抛
        import urllib.error as _ue
        def _raise_http(*a, **k):
            raise _ue.HTTPError("u", 503, "x", {}, io.BytesIO(b"svc"))
        pc.urllib.request.urlopen = _raise_http
        try:
            pc._fetch("https://z.example/v1", None, {}, 5)
            assert False, "HTTPError 应上抛"
        except _ue.HTTPError:
            pass
    finally:
        pc.urllib.request.urlopen, pc._DIRECT_OPENER = real_urlopen, real_direct
        pc._HOST_DIRECT.clear(); pc._HOST_DIRECT.update(real_hosts)


def run() -> bool:
    test_resolve_chat_url()
    test_normalize()
    test_resolve_dual_chain()
    test_test_model_no_false_positive()
    test_models_url_and_list_models()
    test_fetch_fallback()
    test_load_save_roundtrip_and_corrupt()
    return True


if __name__ == "__main__":
    run()
    print("[PASS] provider_config 回归（双链/base_url规整/normalize/test_model防假阳性/模型列表/直连回退/损坏留档）")
    sys.exit(0)
