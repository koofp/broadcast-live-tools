# -*- coding: utf-8 -*-
"""bilive 面板主应用 v3：多页面 + 任务队列 Worker + 房间/录制视图"""
import html as html_lib
import os
import threading
import time
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

VERSION = "3.0"
BELOW_NORMAL = 0x00004000


def render_md(text: str) -> str:
    safe = html_lib.escape(text)
    return markdown.markdown(safe, extensions=["tables", "fenced_code"])


def nav(request: Request, title: str, tpl: str, **ctx):
    ctx.setdefault("version", VERSION)
    return templates.TemplateResponse(request, tpl, {"title": title, **ctx})


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


# ---------- 任务队列 Worker ----------
_worker_env = None


def _worker_env():
    global _worker_env
    if _worker_env is None:
        e = os.environ.copy()
        k = services.get_api_key()
        if k:
            e["OPENROUTER_API_KEY"] = k
        _worker_env = e
    return _worker_env


def _run_job(job: dict):
    # 与 process_all.ps1 的互斥点：拿不到 run.lock → 任务退回队列（稍后重试）
    if not services.acquire_run_lock():
        services.defer_job(job["id"])
        print(f"[job {job['id']}] run.lock 被占用，任务退回队列", flush=True)
        return
    try:
        v = job["path"]
        base = Path(v).with_suffix("")
        srt, sum_md = base.with_suffix(".srt"), base.with_suffix(".summary.md")
        model = str(Path(__file__).resolve().parent.parent / "models" / "faster-whisper-small")

        if not srt.exists():
            print(f"[job {job['id']}] 转写 {Path(v).name}", flush=True)
            subprocess.run(["python", str(BASE.parent / "transcribe_host.py"), v,
                            "--model", model], env=_worker_env(),
                           creationflags=BELOW_NORMAL, cwd=str(services.ROOT))
        if not srt.exists():
            services.finish_job(job["id"], False, "转写无产出")
            return
        if services.is_placeholder_srt(srt):
            sum_md.write_text("（该分段无语音内容，未生成总结）", encoding="utf-8")
            services.finish_job(job["id"], True, "占位（无语音）跳过总结")
            return
        if not sum_md.exists():
            print(f"[job {job['id']}] 总结 {Path(v).name}", flush=True)
            subprocess.run(["python", str(BASE.parent / "summarize_host.py"), str(srt),
                            "--prompt-file", str(Path(services.ROOT) / "prompt.txt")],
                           env=_worker_env(), creationflags=BELOW_NORMAL,
                           cwd=str(services.ROOT))
        services.finish_job(job["id"], sum_md.exists(),
                            None if sum_md.exists() else "总结无产出")
    finally:
        services.release_run_lock()


def _worker_loop():
    services.requeue_stale_running()
    while True:
        try:
            job = services.pop_next_job()
            if job:
                _run_job(job)
            else:
                time.sleep(3)
        except Exception as e:
            print("[worker]", repr(e)[:200], flush=True)
            time.sleep(5)


@app.on_event("startup")
async def start_worker():
    threading.Thread(target=_worker_loop, daemon=True).start()


# ---------- 页面 ----------
@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    st = services.status()
    pl = services.pipeline_state()
    jobs = services.queue_snapshot()
    running = next((j for j in jobs if j["status"] == "running"), None)
    return nav(request, "仪表盘", "dashboard.html",
               st=st, activity=pl["tail"][-6:], failures=pl["failures"],
               active_file=(running or {}).get("name"), progress=pl.get("progress"))


@app.get("/recording", response_class=HTMLResponse)
async def page_recording(request: Request):
    cfg_rooms = {str(r["room_id"]): r for r in services.rooms_from_settings()}
    live = services.live_status([int(r) for r in cfg_rooms] +
                               [int(d.name) for d in services.VIDEOS.iterdir()
                                if d.is_dir() and d.name.isdigit()])
    cards = []
    for d in sorted(services.VIDEOS.iterdir(), key=lambda p: p.name):
        if not (d.is_dir() and d.name.isdigit()):
            continue
        rid = d.name
        lv = live.get(rid, {})
        segs = sorted(list(d.glob("*.mp4")) + list(d.glob("*.flv")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        cur = segs[0] if segs and (time.time() - segs[0].stat().st_mtime) < 600 else None
        total_gb = round(sum(p.stat().st_size for p in segs) / 2**30, 2)
        cards.append({
            "room": rid, "configured": rid in cfg_rooms,
            "live": lv.get("live_status"), "title": lv.get("title") or "(标题获取失败)",
            "online": lv.get("online"),
            "segments": len(segs), "total_gb": total_gb,
            "current_name": cur.name if cur else None,
            "current_mb": round(cur.stat().st_size / 2**20) if cur else None,
            "newest_age_min": int((time.time() - segs[0].stat().st_mtime) / 60) if segs else None,
        })
    unconf = len([c for c in cards if not c["configured"]])
    return nav(request, "录制", "recording.html",
               cards=cards, unconf_count=unconf,
               container=services.status().get("container"))


@app.get("/segments", response_class=HTMLResponse)
async def page_segments(request: Request):
    files = services.files()
    rooms = sorted({f["room"] for f in files})
    return nav(request, "分段库", "segments.html", files=files, rooms=rooms)


@app.get("/pipeline", response_class=HTMLResponse)
async def page_pipeline(request: Request):
    pl = services.pipeline_state()
    jobs = services.queue_snapshot()
    return nav(request, "流水线", "pipeline.html", pl=pl, jobs=jobs)


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
    return nav(request, "设置", "settings.html",
               prompt=services.get_prompt(),
               prompt_bak=services.get_prompt_bak(),
               key_set=bool(services.get_api_key()),
               model_dir=str(Path(services.ROOT) / "models" / "faster-whisper-small"),
               image="bilive-fixed:0.3.1")


# ---------- API：队列 ----------
@app.get("/api/jobs")
async def api_jobs():
    return services.queue_snapshot()


@app.post("/api/jobs/enqueue")
async def api_jobs_enqueue(req: Request):
    b = await req.json()
    name = b.get("name")
    hit = None
    for room in services.VIDEOS.iterdir():
        cand = room / name if name else None
        if cand and cand.exists():
            hit = str(cand); break
    if not hit:
        return JSONResponse({"queued": False, "reason": "file not found"}, status_code=404)
    r = services.enqueue(name, hit, priority=0)   # 用户点按=插队
    return JSONResponse(r, status_code=200 if r["queued"] else 409)


@app.post("/api/jobs/enqueue-all")
async def api_jobs_enqueue_all():
    n = 0
    for f in services.files():
        if f["is_current"] or f["summary"]:
            continue
        path = services.VIDEOS / f["room"] / f["name"]
        r = services.enqueue(f["name"], str(path), priority=5)
        if r.get("queued"):
            n += 1
    return {"enqueued": n}


@app.post("/api/jobs/clear-done")
async def api_jobs_clear_done():
    q = services._load_queue()
    q["jobs"] = [j for j in q["jobs"] if j["status"] != "done"]
    services._save_queue(q)
    return {"cleared": True}


# ---------- API：房间/录制 ----------
@app.get("/api/recording")
async def api_recording():
    return await page_recording_data()


async def page_recording_data():
    cfg_rooms = {str(r["room_id"]): r for r in services.rooms_from_settings()}
    ids = [int(r) for r in cfg_rooms] + \
          [int(d.name) for d in services.VIDEOS.iterdir()
           if d.is_dir() and d.name.isdigit()]
    live = services.live_status(ids)
    out = []
    for d in sorted(services.VIDEOS.iterdir(), key=lambda p: p.name):
        if not (d.is_dir() and d.name.isdigit()):
            continue
        rid = d.name
        lv = live.get(rid, {})
        segs = sorted(list(d.glob("*.mp4")) + list(d.glob("*.flv")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        cur = segs[0] if segs and (time.time() - segs[0].stat().st_mtime) < 600 else None
        out.append({"room": rid, "configured": rid in cfg_rooms,
                    "live": lv.get("live_status"), "title": lv.get("title") or "",
                    "online": lv.get("online"), "segments": len(segs),
                    "total_gb": round(sum(p.stat().st_size for p in segs)/2**30, 2),
                    "current": (cur.name if cur else None),
                    "current_mb": (round(cur.stat().st_size/2**20) if cur else None)})
    return {"rooms": out, "container": services.status().get("container")}


@app.post("/api/rooms/add")
async def api_rooms_add(req: Request):
    b = await req.json()
    try:
        rid = int(b.get("room_id"))
    except Exception:
        return JSONResponse({"ok": False, "reason": "房间号必须是数字"}, status_code=400)
    ok, msg = services.add_room(rid)
    return JSONResponse({"ok": ok, "reason": msg, "need_restart": ok})


@app.post("/api/rooms/remove")
async def api_rooms_remove(req: Request):
    b = await req.json()
    try:
        rid = int(b.get("room_id"))
    except Exception:
        return JSONResponse({"ok": False, "reason": "房间号必须是数字"}, status_code=400)
    ok, msg = services.remove_room(rid)
    return JSONResponse({"ok": ok, "reason": msg, "need_restart": ok})


@app.post("/api/docker/restart")
async def api_docker_restart():
    subprocess.Popen(["docker", "compose", "restart"],
                     cwd=str(services.VIDEOS.parent), **{
                         k: v for k, v in services._SUBPROC_KW.items() if k == "cwd"})
    return {"sent": True}


# ---------- API：兼容旧形状 ----------
@app.get("/api/status")
async def api_status():
    return services.status()


@app.get("/api/files")
async def api_files():
    return services.files()


@app.get("/api/pipeline")
async def api_pipeline():
    d = services.pipeline_state()
    d["jobs"] = services.queue_snapshot()
    return d


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
                             "reason": "需要 confirm=true"}, status_code=400)
    out, freed = services.archive(preview_only=False)
    return {"applied": True, "freed_gb": freed, "output": out}


@app.post("/api/process")
async def api_process(req: Request):
    """兼容旧前端：现在改为入队（高优先级），由 Worker 执行"""
    b = await req.json()
    li = services.lock_info()
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
    if one:
        r = services.enqueue(Path(one).name, one, priority=0)
    else:
        en = await api_jobs_enqueue_all()
        r = {"queued": True, "count": en.get("enqueued", 0)}
    if not r.get("queued", True):
        return JSONResponse({"started": False, "reason": r.get("reason", "已在队列"),
                             "locked": True, **services.lock_info()}, status_code=200)
    time.sleep(0.2)
    return {"started": True, "queued": True, "job": r.get("id"),
            **services.lock_info()}


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
    ok, _ = services.set_prompt(bak)
    return {"restored": ok}


@app.get("/api/summary")
async def api_summary_md(room: str, name: str):
    s = services.find_summary(room, name)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    raw = s.read_text(encoding="utf-8")
    return {"md": raw, "html": render_md(raw)}


@app.get("/srt-api/{room}/{name}")
async def api_srt(room: str, name: str):
    s = services.find_srt(room, name)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"content": s.read_text(encoding="utf-8", errors="ignore")}


# ---------- 异常分流 ----------
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