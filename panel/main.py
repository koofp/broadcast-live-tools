# -*- coding: utf-8 -*-
"""bilive 面板主应用 v2：多页面 + JSON API（路由薄层，逻辑在 services）"""
import html as html_lib
import os
from pathlib import Path

import markdown
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import services

BASE = Path(__file__).resolve().parent
app = FastAPI(title="bilive panel", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

VERSION = "2.0"


def render_md(text: str) -> str:
    """先转义原始 HTML（防注入），再渲染 Markdown（tables/fenced_code）"""
    safe = html_lib.escape(text)
    return markdown.markdown(safe, extensions=["tables", "fenced_code"])


def nav(request: Request, title: str, tpl: str, **ctx):
    ctx.setdefault("version", VERSION)
    return templates.TemplateResponse(request, tpl, {"title": title, **ctx})


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


# ---------- 页面 ----------
@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    st = services.status()
    pl = services.pipeline_state()
    return nav(request, "仪表盘", "dashboard.html",
               st=st, activity=pl["tail"][-6:], failures=pl["failures"])


@app.get("/segments", response_class=HTMLResponse)
async def page_segments(request: Request):
    files = services.files()
    rooms = sorted({f["room"] for f in files})
    return nav(request, "分段库", "segments.html", files=files, rooms=rooms)


@app.get("/pipeline", response_class=HTMLResponse)
async def page_pipeline(request: Request):
    pl = services.pipeline_state()
    return nav(request, "流水线", "pipeline.html", pl=pl)


@app.get("/summaries", response_class=HTMLResponse)
async def page_summaries(request: Request, q: str = ""):
    rows = services.summaries_list(query=q)
    return nav(request, "总结库", "summaries.html", rows=rows, q=q)


@app.get("/summaries/{room}/{name}", response_class=HTMLResponse)
async def summary_read(request: Request, room: str, name: str):
    s = services.find_summary(room, name)
    if not s:
        return RedirectResponse("/summaries")
    content = render_md(s.read_text(encoding="utf-8"))
    return nav(request, name.replace(".summary.md", ""), "summary_read.html",
               room=room, video=name.replace(".summary.md", ".mp4"), content=content)


@app.get("/srt/{room}/{name}", response_class=HTMLResponse)
async def srt_view(request: Request, room: str, name: str):
    s = services.find_srt(room, name)
    if not s:
        return RedirectResponse("/segments")
    lines = s.read_text(encoding="utf-8", errors="ignore").splitlines()
    truncated = len(lines) > 600
    return nav(request, name.replace(".srt", "") + " · 字幕", "srt_view.html",
               room=room, content="\n".join(lines[:600]), truncated=truncated)


@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    key_set = bool(os.environ.get("OPENROUTER_API_KEY"))
    return nav(request, "设置", "settings.html",
               prompt=services.get_prompt(),
               prompt_bak=services.get_prompt_bak(),
               key_set=key_set,
               model_dir=str(Path(services.ROOT) / "models" / "faster-whisper-small"),
               image="bilive-fixed:0.3.1")


# ---------- API ----------
@app.get("/api/status")
async def api_status():
    return services.status()


@app.get("/api/files")
async def api_files():
    return services.files()


@app.get("/api/pipeline")
async def api_pipeline():
    return services.pipeline_state()


@app.get("/api/logs/tail")
async def api_logs_tail(n: int = 40):
    return {"lines": services.tail_log(n)}


@app.get("/api/archive/preview")
async def api_archive_preview():
    out, freed = services.archive(preview_only=True)
    return {"output": out}


@app.post("/api/archive/apply")
async def api_archive_apply(req: Request):
    b = await req.json()
    if not b.get("confirm"):
        return JSONResponse({"applied": False,
                             "reason": "需要 confirm=true 的 POST 才会执行删除"}, status_code=400)
    out, freed = services.archive(preview_only=False)
    return {"applied": True, "freed_gb": freed, "output": out}


@app.post("/api/process")
async def api_process(req: Request):
    b = await req.json()
    li = services.lock_info()
    if li.get("locked"):
        return JSONResponse({"started": False, "locked": True, **li}, status_code=409)
    one = None
    if b.get("name") and b.get("name") != "__all__":
        hit = None
        for room in services.VIDEOS.iterdir():
            cand = room / b["name"]
            if cand.exists():
                hit = str(cand); break
        if not hit:
            return JSONResponse({"started": False, "reason": "file not found"}, status_code=404)
        one = hit
    result = services.trigger(one_path=one)
    time.sleep(0.3)
    result.update(services.lock_info())
    return result


@app.get("/api/prompt")
async def api_prompt_get(include_bak: bool = False):
    d = {"prompt": services.get_prompt()}
    if include_bak:
        d["bak"] = services.get_prompt_bak()
    return d


@app.post("/api/prompt")
async def api_prompt_set(req: Request):
    b = await req.json()
    ok, reason = services.set_prompt(b.get("prompt", ""))
    return ({"saved": True, "detail": reason} if ok
            else JSONResponse({"saved": False, "reason": reason}, status_code=400))


@app.post("/api/prompt/rollback")
async def api_prompt_rollback():
    bak = services.get_prompt_bak()
    if not bak:
        return JSONResponse({"restored": False, "reason": "无备份版本"}, status_code=404)
    ok, reason = services.set_prompt(bak)
    return {"restored": ok}


@app.get("/api/summary")
async def api_summary_md(room: str, name: str):
    s = services.find_summary(room, name)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"md": s.read_text(encoding="utf-8"),
            "html": render_md(s.read_text(encoding="utf-8"))}


@app.get("/srt-api/{room}/{name}")
async def api_srt(room: str, name: str):
    s = services.find_srt(room, name)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"content": s.read_text(encoding="utf-8", errors="ignore")}


@app.post("/api/docker/restart")
async def api_docker_restart():
    subprocess.Popen(["docker", "compose", "restart"], cwd=str(services.VIDEOS.parent),
                     **{k: v for k, v in services._SUBPROC_KW.items() if k in ('cwd',)})
    return {"sent": True}


# ---------- 异常处理：页面与 API 分流 ----------
@app.exception_handler(StarletteHTTPException)
async def http_exc(request: Request, exc: StarletteHTTPException):
    if _wants_json(request):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    return templates.TemplateResponse(request, "500.html",
                                      {"code": exc.status_code}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def generic_exc(request: Request, exc: Exception):
    if _wants_json(request):
        return JSONResponse({"error": repr(exc)[:200]}, status_code=500)
    return templates.TemplateResponse(request, "500.html", {"code": 500}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9090, log_level="warning")