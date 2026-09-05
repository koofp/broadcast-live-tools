# -*- coding: utf-8 -*-
"""bilive 面板主应用 v3：多页面 + 任务队列 Worker + 房间/录制视图"""
import html as html_lib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import markdown
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import services

# ---- 输出重定向：print 永远有效（防管道断裂 EINVAL 杀死 Worker 线程）----
_logs_dir = Path(__file__).resolve().parent.parent / "logs"
_logs_dir.mkdir(exist_ok=True)
_stdout_log = open(_logs_dir / "panel-stdout.log", "a", buffering=1, encoding="utf-8")
sys.stdout = _stdout_log
sys.stderr = _stdout_log

BASE = Path(__file__).resolve().parent
app = FastAPI(title="bilive panel", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

# 本机防护（评审发现）：面板只绑定 127.0.0.1，但浏览器内恶意页面可跨站 POST
# （/api/provider 可改写 key 去向=密钥外泄跳板），DNS rebinding 可伪装 Host 读接口。
# 规则：非本机 Host 一律 403；写方法带 Origin 时必须同源。curl/计划任务无 Origin，按 Host 放行。
_LOCAL_HOSTS = {"127.0.0.1:9090", "localhost:9090"}


@app.middleware("http")
async def _local_guard(request: Request, call_next):
    host = (request.headers.get("host") or "").lower()
    if host and host not in _LOCAL_HOSTS:
        return JSONResponse({"error": f"host {host} 不在允许列表（面板仅限本机访问）"},
                            status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = (request.headers.get("origin") or "").lower()
        if origin and origin.split("://", 1)[-1] not in _LOCAL_HOSTS:
            return JSONResponse({"error": "跨站写请求已拦截"}, status_code=403)
    return await call_next(request)

VERSION = "3.2"
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
def _get_worker_env():
    """子进程环境：不做任何 key 注入。summarize/session 均经 provider_config
    自行解析（provider.json > env > api_key.txt > 注册表），此处注入快照会在
    设置页改 key 后产生"改了配置 Worker 仍用旧 key"的陈旧值（评审发现）。"""
    return os.environ.copy()


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
            try:
                subprocess.run(["python", str(BASE.parent / "transcribe_host.py"), v,
                                "--model", model], env=_get_worker_env(),
                               creationflags=BELOW_NORMAL, cwd=str(services.ROOT),
                               timeout=3600)
            except subprocess.TimeoutExpired:
                print(f"[job {job['id']}] 转写超时(1h)，放弃本次", flush=True)
        if not srt.exists():
            services.finish_job(job["id"], False, "转写无产出")
            return
        if services.is_placeholder_srt(srt):
            sum_md.write_text("（该分段无语音内容，未生成总结）", encoding="utf-8")
            services.finish_job(job["id"], True, "占位（无语音）跳过总结")
            return
        if not sum_md.exists():
            print(f"[job {job['id']}] 总结 {Path(v).name}", flush=True)
            try:
                subprocess.run(["python", str(BASE.parent / "summarize_host.py"), str(srt),
                                "--prompt-file", str(Path(services.ROOT) / "prompt.txt")],
                               env=_get_worker_env(), creationflags=BELOW_NORMAL,
                               cwd=str(services.ROOT), timeout=1800)
            except subprocess.TimeoutExpired:
                print(f"[job {job['id']}] 总结超时(30min)", flush=True)
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
                try:
                    _run_job(job)
                except Exception as e:
                    services.finish_job(job["id"], False, repr(e)[:200])
                    print(f"[worker] 任务失败已记录: {repr(e)[:150]}", flush=True)
            else:
                time.sleep(3)
        except Exception as e:
            print("[worker]", repr(e)[:200], flush=True)
            time.sleep(5)


def _worker_supervisor():
    """外层守护：Worker 线程任何异常都自动重启，永不静默死亡"""
    while True:
        try:
            _worker_loop()
        except Exception as e:
            try:
                print(f"[supervisor] Worker 异常重启: {repr(e)[:180]}", flush=True)
            except Exception:
                pass
        time.sleep(10)


@app.on_event("startup")
async def start_worker():
    threading.Thread(target=_worker_supervisor, daemon=True).start()
    services.start_status_refresher()   # 后台常驻刷新状态（架构定案：请求永不阻塞在 status.ps1 上）


# ---------- 页面 ----------
@app.get("/", response_class=HTMLResponse)
def page_dashboard(request: Request):
    st = services.status()
    pl = services.pipeline_state()
    jobs = services.queue_snapshot()
    running = next((j for j in jobs if j["status"] == "running"), None)
    stall_note = None
    if (st.get("newest_age_min") or 0) > 45:
        ids = [int(r["room_id"]) for r in services.rooms_from_settings()]
        lv = services.live_status(ids) if ids else {}
        if ids and all((lv.get(i, {}) or {}).get("live_status") == 0 for i in ids):
            stall_note = "全部房间未开播——待机中，开播自动录制"
        else:
            stall_note = "有房间在播但未写入——检查网络/Clash/容器日志"
    return nav(request, "仪表盘", "dashboard.html",
               st=st, activity=pl["tail"][-6:], failures=pl["failures"],
               active_file=(running or {}).get("name"), progress=pl.get("progress"),
               stall_note=stall_note)


@app.get("/recording", response_class=HTMLResponse)
def page_recording(request: Request):
    payload = _recording_payload()
    return nav(request, "录制", "recording.html",
               cards=payload["rooms"], unconf_count=payload["unconf_count"],
               container=payload["container"])


def _recording_payload() -> dict:
    """录制页与 /api/recording 共用的数据装配（同步函数，路由层自动进线程池）。

    「正在录制」判定 v2（2026-08-23）：以 B 站直播状态为主、文件新鲜度为辅。
    旧逻辑仅看「最新视频 mtime < 600s」，有两个实锤误报源：
      ① blrec 的 postprocessing 会在分段结束后 remux 出新 mp4（新 mtime、
         写入中途只有几 MB）——把旧分段误报成"正在录制"
         （实测卡片显示过 15-23-59.mp4/10MB，即此因）；
      ② 主播已下播（live_status=0）时红灯仍可亮最长 10 分钟。
    """
    cfg_rooms = {str(r["room_id"]): r for r in services.rooms_from_settings()}
    disk_rooms = [d.name for d in services.VIDEOS.iterdir()
                  if d.is_dir() and d.name.isdigit()]
    all_ids = sorted(set(list(cfg_rooms) + disk_rooms), key=lambda x: int(x))
    live = services.live_status([int(r) for r in all_ids])
    cards = []
    for rid in all_ids:
        d = services.VIDEOS / rid
        lv = live.get(rid, {})
        try:
            segs = sorted(list(d.glob("*.mp4")) + list(d.glob("*.flv")),
                          key=lambda p: p.stat().st_mtime, reverse=True) if d.exists() else []
        except OSError:
            segs = []   # 目录被锁/权限异常时不拖垮整页
        newest_age_min = int((time.time() - segs[0].stat().st_mtime) / 60) if segs else None
        lstat = lv.get("live_status")
        cur = None
        if segs:
            if lstat == 1:
                cur = segs[0] if newest_age_min <= 15 else None   # 直播中：最新段即当前录制
            elif lstat == 0:
                cur = None                                        # 未开播 → 绝不亮红灯
            elif newest_age_min is not None and newest_age_min < 10:
                cur = segs[0]                                     # 状态未知 → 沿用旧启发式
        total_gb = round(sum(p.stat().st_size for p in segs) / 2**30, 2)
        cards.append({
            "room": rid, "configured": rid in cfg_rooms,
            "live": lstat, "title": lv.get("title") or "(标题获取失败)",
            "online": lv.get("online"),
            "segments": len(segs), "total_gb": total_gb,
            "current": cur.name if cur else None,
            "current_mb": round(cur.stat().st_size / 2**20) if cur else None,
            "newest_age_min": newest_age_min,
        })
    unconf = len([c for c in cards if not c["configured"]])
    return {"rooms": cards, "unconf_count": unconf,
            "container": services.container_status()}


@app.get("/segments", response_class=HTMLResponse)
def page_segments(request: Request):
    files = services.files()
    rooms = sorted({f["room"] for f in files})
    return nav(request, "片段库", "segments.html", files=files, rooms=rooms,
               sessions=services.sessions_index())


@app.get("/pipeline", response_class=HTMLResponse)
def page_pipeline(request: Request):
    pl = services.pipeline_state()
    jobs = services.queue_snapshot()
    return nav(request, "流水线", "pipeline.html", pl=pl, jobs=jobs)


@app.get("/summaries", response_class=HTMLResponse)
def page_summaries(request: Request, q: str = ""):
    rows = services.summaries_list(query=q)
    return nav(request, "总结库", "summaries.html", rows=rows, q=q)


@app.get("/summaries/{room}/{name}", response_class=HTMLResponse)
def summary_read(request: Request, room: str, name: str):
    s = services.find_summary(room, name)
    if not s:
        return RedirectResponse("/summaries")
    content = render_md(s.read_text(encoding="utf-8"))
    return nav(request, name.replace(".summary.md", ""), "summary_read.html",
               room=room, video=name.replace(".summary.md", ".mp4"), content=content)


@app.get("/srt/{room}/{name}", response_class=HTMLResponse)
def srt_view(request: Request, room: str, name: str):
    s = services.find_srt(room, name)
    if not s:
        return RedirectResponse("/segments")
    lines = s.read_text(encoding="utf-8", errors="ignore").splitlines()
    truncated = len(lines) > 600
    return nav(request, name.replace(".srt", "") + " · 字幕", "srt_view.html",
               room=room, content="\n".join(lines[:600]), truncated=truncated)


@app.get("/settings", response_class=HTMLResponse)
def page_settings(request: Request):
    return nav(request, "设置", "settings.html",
               prompt=services.get_prompt(),
               prompt_bak=services.get_prompt_bak(),
               provider=services.provider_view(),
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
            # 修复(评审P2)：路径穿越防护——断言解析后路径在 Videos 内
            if not cand.resolve().is_relative_to(services.VIDEOS.resolve()):
                continue
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
def api_recording():
    return _recording_payload()


@app.post("/api/rooms/add")
async def api_rooms_add(req: Request):
    b = await req.json()
    try:
        rid = int(b.get("room_id"))
    except Exception:
        return JSONResponse({"ok": False, "reason": "房间号必须是数字"}, status_code=400)
    v = await run_in_threadpool(services.validate_room, rid)
    if not v.get("valid"):
        return JSONResponse({"ok": False,
                             "reason": f"房间不存在或无法访问（{v.get('reason','校验失败')}）"},
                            status_code=400)
    ok, msg = await run_in_threadpool(services.add_room, rid)
    # 修复（与 remove 对称）：同步通知 blrec 添加任务——不需要重启容器即可生效
    # 修复(评审P1)：blrec API 调用包进 run_in_threadpool，不再阻塞事件循环
    blrec_ok = False
    if ok:
        def _blrec_add():
            import urllib.request as _ur
            req_t = _ur.Request(f"http://127.0.0.1:22333/api/v1/tasks/{rid}",
                                method="POST", headers={"X-API-KEY": "Bil1veLocal2026"})
            _ur.urlopen(req_t, timeout=10)
            for ep in ("monitor/enable", "recorder/enable"):
                req_e = _ur.Request(f"http://127.0.0.1:22333/api/v1/tasks/{rid}/{ep}",
                                    method="POST", headers={"X-API-KEY": "Bil1veLocal2026"})
                _ur.urlopen(req_e, timeout=10)
        try:
            await run_in_threadpool(_blrec_add)
            blrec_ok = True
        except Exception:
            pass
    return JSONResponse({"ok": ok,
                         "reason": f"{msg} · {v.get('title','')}" + (" · 已实时生效" if blrec_ok else " · 需重启容器生效"),
                         "need_restart": not blrec_ok})


@app.post("/api/rooms/remove")
async def api_rooms_remove(req: Request):
    b = await req.json()
    try:
        rid = int(b.get("room_id"))
    except Exception:
        return JSONResponse({"ok": False, "reason": "房间号必须是数字"}, status_code=400)
    ok, msg = await run_in_threadpool(services.remove_room, rid)
    # 修复（用户实锤缺陷）：同步通知 blrec 移除任务——否则 blrec 在内存中
    # 继续监控/录制已"移除"的房间，直到下次容器重启才消失
    if ok:
        def _blrec_remove():
            import urllib.request as _ur
            req_d = _ur.Request(f"http://127.0.0.1:22333/api/v1/tasks/{rid}",
                                method="DELETE", headers={"X-API-KEY": "Bil1veLocal2026"})
            _ur.urlopen(req_d, timeout=10)
        try:
            await run_in_threadpool(_blrec_remove)
        except Exception:
            pass
    return JSONResponse({"ok": ok, "reason": msg, "need_restart": not ok})


@app.get("/api/readiness")
def api_readiness():
    return services.readiness_check()


@app.get("/prompts")
def page_prompts(req: Request):
    rooms = services.rooms_from_settings()
    global_prompt = services.read_prompt("global")
    room_prompts = {}
    for r in rooms:
        rid = str(r["room_id"])
        room_prompts[rid] = bool(services.read_prompt(rid))
    return templates.TemplateResponse(req, "prompts.html", {
        "title": "提示词",
        "rooms": rooms,
        "room_prompts": room_prompts,
    })


@app.get("/api/prompts/{room}")
def api_prompts_get(room: str):
    return {"room": room, "content": services.read_prompt(room)}


@app.put("/api/prompts/{room}")
async def api_prompts_put(room: str, req: Request):
    b = await req.json()
    content = b.get("content", "")
    # 修复(评审P1)：拒空串（防写入空文件导致总结无提示词）；拒超长
    if not isinstance(content, str) or not content.strip() or len(content) > 20000:
        return JSONResponse({"ok": False, "reason": "内容为空或超长(上限20000字符)"}, status_code=400)
    ok = await run_in_threadpool(services.write_prompt, room, content)
    if not ok:
        return JSONResponse({"ok": False, "reason": "无效房间号或写入失败"}, status_code=400)
    return JSONResponse({"ok": True})


@app.get("/api/sessions")
def api_sessions():
    return services.sessions_index()


@app.post("/api/sessions/ignore")
async def api_sessions_ignore(req: Request):
    """切换某场次的「忽略场级总结」标记（复用 session.py CLI，幂等）。"""
    b = await req.json()
    room, sid = b.get("room"), b.get("session_id")
    if not room or not sid:
        return JSONResponse({"ok": False, "reason": "参数缺失"}, status_code=400)
    msg = await run_in_threadpool(services.session_ignore_toggle, str(room), str(sid))
    return {"ok": True, "message": msg}


@app.post("/api/docker/restart")
async def api_docker_restart():
    # 修复(评审高危)：原 kwargs 过滤保留 cwd 与显式 cwd 重复 → TypeError，重启按钮必 500
    subprocess.Popen(["docker", "compose", "restart"], cwd=str(services.VIDEOS.parent),
                     creationflags=subprocess.CREATE_NO_WINDOW)
    return {"sent": True}


# ---------- API：兼容旧形状 ----------
@app.get("/api/status")
def api_status():
    return services.status()


@app.get("/api/files")
def api_files():
    return services.files()


@app.get("/api/pipeline")
def api_pipeline():
    d = services.pipeline_state()
    d["jobs"] = services.queue_snapshot()
    return d


@app.get("/api/logs/tail")
def api_logs_tail(n: int = 40):
    return {"lines": services.tail_log(n)}


@app.get("/api/archive/preview")
def api_archive_preview():
    out, freed = services.archive(preview_only=True)
    return {"output": out}


@app.post("/api/archive/apply")
async def api_archive_apply(req: Request):
    b = await req.json()
    if not b.get("confirm"):
        return JSONResponse({"applied": False,
                             "reason": "需要 confirm=true"}, status_code=400)
    out, freed = await run_in_threadpool(services.archive, preview_only=False)
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


@app.get("/api/provider")
def api_provider_get():
    return services.provider_view()


@app.post("/api/provider")
async def api_provider_set(req: Request):
    b = await req.json()
    base_url, api_key, active_model = b.get("base_url"), b.get("api_key"), b.get("active_model")
    models = b.get("models")
    # 评审发现：此前只校验 models 是 list，元素非字符串（如数字）会在 services 层
    # m.strip() 抛 AttributeError → 500；base_url 等非字符串同理。入口统一严检。
    if not all(isinstance(x, str) for x in (base_url, api_key, active_model)):
        return JSONResponse({"saved": False, "reason": "base_url/api_key/active_model 必须为字符串"},
                            status_code=400)
    if not isinstance(models, list) or not models or \
            not all(isinstance(m, str) and m.strip() for m in models):
        return JSONResponse({"saved": False, "reason": "models 必须为非空字符串列表（至少保留一个模型）"},
                            status_code=400)
    view = services.provider_save(base_url.strip(), api_key,
                                  [m.strip() for m in models], active_model.strip())
    return {"saved": True, "provider": view}


@app.post("/api/provider/test")
async def api_provider_test(req: Request):
    b = await req.json()
    base_url, api_key, model = b.get("base_url"), b.get("api_key"), b.get("model")
    if not all(isinstance(x, str) for x in (base_url, api_key, model)):
        return JSONResponse({"ok": False, "error": "base_url/api_key/model 必须为字符串"}, status_code=400)
    # 留空参数由 services.provider_test 回退当前生效配置（resolve），与真实链路同源
    return await run_in_threadpool(services.provider_test, base_url, api_key, model)


@app.post("/api/provider/models")
async def api_provider_models(req: Request):
    """拉取中继可用模型列表（GET /v1/models），设置页在线填充模型下拉。"""
    b = await req.json()
    base_url, api_key = b.get("base_url"), b.get("api_key")
    if not all(isinstance(x, str) for x in (base_url, api_key)):
        return JSONResponse({"ok": False, "error": "base_url/api_key 必须为字符串"}, status_code=400)
    return await run_in_threadpool(services.provider_models, base_url, api_key)


@app.get("/api/summary")
def api_summary_md(room: str, name: str):
    s = services.find_summary(room, name)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    raw = s.read_text(encoding="utf-8")
    return {"md": raw, "html": render_md(raw)}


@app.get("/srt-api/{room}/{name}")
def api_srt(room: str, name: str):
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