# -*- coding: utf-8 -*-
"""服务层 v2：状态/文件/流水线/归档（无路由逻辑）
规范：pathlib+utf-8；subprocess 显式编码+cwd；tail 反向截断读取；进度正则唯一出处。
"""
import html as _html
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import tomllib
except ImportError:
    tomllib = None

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "bilive-docker" / "Videos"
SETTINGS = ROOT / "bilive-docker" / "settings.toml"
PROMPT_FILE = ROOT / "prompt.txt"
LOCK = ROOT / "run.lock"
QUEUE_FILE = ROOT / "panel" / "queue.json"
PS_STATUS = str(ROOT / "status.ps1")
PS_PROC = str(ROOT / "process_all.ps1")
PS_CLEAN = str(ROOT / "cleanup.ps1")
LOG_DIR = ROOT / "logs" / "pipeline"

_PLACEHOLDER_MARK = "[无语音内容]"
QUEUE_LOCK = threading.Lock()
_run_lock_stream = None

DEFAULT_PROMPT = """你是资深直播内容分析师。以下字幕来自语音识别，可能含同音误听，
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

_PROGRESS_RE = re.compile(r"\.\.\.(\d{2}:\d{2}:\d{2}),\d+ \((\d+)条\)")

_SUBPROC_KW = dict(capture_output=True, text=True, encoding="utf-8",
                   errors="ignore", cwd=str(ROOT),
                   creationflags=subprocess.CREATE_NO_WINDOW)


def _ps(script: str, *args: str, timeout: int = 300) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, *args],
        timeout=timeout, **_SUBPROC_KW)
    return out.stdout


def lock_info() -> dict:
    """锁的结构化信息。探测方式=独占打开（进程崩溃遗留的陈旧锁自动清除）"""
    if not LOCK.exists():
        return {"locked": False}
    age = round(time.time() - LOCK.stat().st_mtime)
    holder = ""
    try:
        holder = LOCK.read_text(encoding="utf-8", errors="ignore")[:80]
    except Exception:
        pass
    # 独占探测：能打开说明持有进程已死 → 清除陈旧锁
    try:
        with open(LOCK, "r+b"):
            pass
        LOCK.unlink(missing_ok=True)
        return {"locked": False, "stale_cleaned": True}
    except PermissionError:
        return {"locked": True, "age_sec": age, "holder": holder,
                "force_available": age > 7200}


_ctn_cache: dict = {"ts": 0.0, "val": ""}
_status_cache: dict = {"ts": 0.0, "data": None}
_STATUS_LOCK = threading.Lock()


def container_status(max_age: float = 10.0) -> str:
    """轻量容器状态：单次 docker ps + 缓存。
    替代"只为拿一个容器状态字符串就跑全量 status.ps1（5~10 秒）"的旧做法。"""
    now = time.time()
    if now - _ctn_cache["ts"] > max_age:
        try:
            _ctn_cache["val"] = subprocess.run(
                ["docker", "ps", "--filter", "name=bilive_docker",
                 "--format", "{{.Status}}"],
                **{k: v for k, v in _SUBPROC_KW.items() if k != 'cwd'},
                timeout=15).stdout.strip()
        except Exception:
            _ctn_cache["val"] = ""
        _ctn_cache["ts"] = now
    return _ctn_cache["val"] or "stopped"


def _refresh_status_once() -> dict:
    """跑一次 status.ps1 并写入缓存（后台刷新线程与冷启动首调共用）。"""
    out = _ps(PS_STATUS, timeout=180)
    js = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                js = json.loads(line)
                break
            except Exception:
                continue
    d = dict(js or {})
    d["container"] = container_status(max_age=0)
    d.update(lock_info())
    d["ts"] = time.strftime("%H:%M:%S")
    with _STATUS_LOCK:
        _status_cache["ts"] = time.time()
        _status_cache["data"] = dict(d)
    return d


def _status_refresher():
    """后台常驻刷新（架构定案：请求永不阻塞在 status.ps1 上）。daemon 随进程退出。"""
    while True:
        try:
            _refresh_status_once()
        except Exception:
            pass
        time.sleep(20)


def start_status_refresher():
    threading.Thread(target=_status_refresher, daemon=True,
                     name="status-refresher").start()


def status(max_age: float = 20.0) -> dict:
    """返回最近已知状态（立即返回，绝不阻塞）。
    缓存为空（面板刚启动且后台首刷未完成）时同步首刷一次。"""
    with _STATUS_LOCK:
        data = _status_cache["data"]
    if data is None:
        return _refresh_status_once()
    return dict(data)


def _room_dirs():
    return [d for d in VIDEOS.iterdir() if d.is_dir()] if VIDEOS.exists() else []


_STATUS_MAP = {
    ("summary",): ("已总结", "b-done"),
    ("srt",): ("已转写·待总结", "b-srt"),
    (): ("未处理", "b-none"),
}


def files() -> list:
    rows = []
    for room in _room_dirs():
        mp4_names = {p.stem for p in room.glob("*.mp4")}
        cand = list(room.glob("*.mp4")) + \
               [f for f in room.glob("*.flv") if f.stem not in mp4_names]
        for m in sorted(cand, key=lambda p: p.stat().st_mtime, reverse=True):
            base = m.with_suffix("")
            has_srt = base.with_suffix(".srt").exists()
            has_sum = base.with_suffix(".summary.md").exists()
            key = ("summary",) if has_sum else (("srt",) if has_srt else ())
            label, cls = _STATUS_MAP[key]
            rows.append({
                "room": room.name, "name": m.name,
                "size_gb": round(m.stat().st_size / 2**30, 2),
                "mtime": time.strftime("%m-%d %H:%M", time.localtime(m.stat().st_mtime)),
                "srt": has_srt, "summary": has_sum,
                "status_label": ("最近写入" if (time.time() - m.stat().st_mtime) < 600 else label)
                                 if not has_sum else label,
                "status_class": ("b-flv" if (time.time() - m.stat().st_mtime) < 600 and m.suffix==".flv" else cls)
                                 if not has_sum else cls,
                "is_current": (time.time() - m.stat().st_mtime) < 600,
            })
    return rows


def tail_log(n: int = 40) -> list:
    """反向截断读取，避免整文件载入；n 上限 500"""
    n = max(1, min(n, 500))
    if not LOG_DIR.exists():
        return []
    # 只认日期命名的流水线日志（alert.log/deleted.log 等旁路日志不得抢占"最近活动"）
    logs = sorted([p for p in LOG_DIR.glob("*.log")
                   if re.match(r"\d{4}-\d{2}-\d{2}\.log$", p.name)],
                  key=lambda p: p.stat().st_mtime)
    if not logs:
        return []
    with open(logs[-1], "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        block = min(size, 256 * 1024)
        f.seek(max(0, size - block))
        data = f.read().decode("utf-8", "ignore").splitlines()
    return data[-n:]


def pipeline_state() -> dict:
    lines = tail_log(60)
    active, progress, progress_ts = None, None, None
    for ln in reversed(lines):
        if active is None:
            if "[1/2] 转写 " in ln:
                active = ln.split("转写 ")[-1].strip()
            elif "[1/2] 批量转写 " in ln:
                active = "(批量转写中)"   # 审计采纳后的多段合跑模式，无单一活跃文件
        if progress is None:
            m = _PROGRESS_RE.search(ln)
            if m:
                progress = {"audio_time": m.group(1), "count": int(m.group(2))}
                progress_ts = ln.split("|")[0].strip() if "|" in ln else None
        if active and progress:
            break
    fails = []
    retry = ROOT / "retry.txt"
    if retry.exists():
        fails = [l for l in retry.read_text(encoding="utf-8", errors="ignore").splitlines()
                 if l.strip()][-10:]
    duration = durations_get(active) if active else None   # 修复：原按元组解包，实为 float
    pct_val = None
    if duration and progress and progress.get("audio_time"):
        mm = re.match(r"(\d{2}):(\d{2}):(\d{2})", progress["audio_time"])
        if mm:
            h, mi, s = map(int, mm.groups())
            cur_ms = (h*3600 + mi*60 + s) * 1000
            pct_val = min(100, round(cur_ms / (duration * 1000) * 100))
    return {"locked": LOCK.exists(), **lock_info(), "active_file": active,
            "progress": progress, "progress_ts": progress_ts,
            "duration_sec": duration, "progress_pct": pct_val,
            "tail": lines[-30:], "failures": fails}


_DUR_FILE = Path(__file__).resolve().parent.parent / ".durations.json"


def durations_get(name: str):
    """一次性 ffprobe 缓存（经容器查询，每分段仅一次）"""
    try:
        cache = json.loads(_DUR_FILE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    if name in cache:
        return cache[name]
    hit = None
    for room in VIDEOS.iterdir():
        p = room / name
        if p.exists():
            cpath = "/app/Videos/" + room.name + "/" + name
            try:
                out = subprocess.run(
                    ["docker", "exec", "bilive_docker", "ffprobe", "-v", "error",
                     "-show_entries", "format=duration", "-of", "csv=p=0", cpath],
                    capture_output=True, text=True, timeout=20).stdout.strip()
                hit = float(out.splitlines()[-1])
            except Exception:
                return None
            break
    if hit:
        cache[name] = round(hit, 2)
        try: _DUR_FILE.write_text(json.dumps(cache), encoding="utf-8")
        except Exception: pass
    return hit


def trigger(one_path: str | None = None) -> dict:
    li = lock_info()
    if li.get("locked"):
        return {"started": False, **li}
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS_PROC]
    if one_path:
        args += ["-One", one_path]
    subprocess.Popen(args, cwd=str(ROOT), **{k: v for k, v in _SUBPROC_KW.items()
                                             if k in ('cwd',)})
    time.sleep(0.4)
    return {"started": True, **lock_info()}


def archive(preview_only: bool = True) -> tuple[str, int]:
    args = [PS_CLEAN] + ([] if preview_only else ["-Apply"])
    out = _ps(*args, timeout=600)
    freed = 0
    m = None
    import re as _re
    mm = _re.search(r"释放约 ([\d.]+)GB", out)
    if mm:
        freed = float(mm.group(1))
    elif not preview_only:
        pass
    return out, freed


def _resolve_out(room: str, name: str, ext: str) -> Path | None:
    """ext 形如 '.summary.md'/'.srt'；兼容传入名带或不带该后缀。
    场次总结存于房间下 _sessions/ 子目录：name 匹配场次ID模式时优先探测。"""
    room_dir = VIDEOS / room
    stem = name[:-len(ext)] if name.endswith(ext) else \
           (name[:-4] if name.endswith(".mp4") or name.endswith(".flv") else name)
    cand = room_dir / (stem + ext)
    if cand.exists():
        return cand
    if re.fullmatch(r"\d{8}_\d{6}", stem):   # 场次ID → _sessions 子目录
        cand = room_dir / "_sessions" / (stem + ext)
        if cand.exists():
            return cand
    return None


def find_summary(room: str, name: str) -> Path | None:
    return _resolve_out(room, name, ".summary.md")


def find_srt(room: str, name: str) -> Path | None:
    return _resolve_out(room, name, ".srt")


def _first_section_line(t: str, name: str) -> str:
    m = re.search(rf"##\s*{name}[^\n]*\n+(.+)", t)
    return m.group(1).strip().splitlines()[0][:100] if m and m.group(1).strip() else ""


def sessions_index() -> dict:
    """各房间的场次缓存汇总：{room: [session...]}（session.py 生成）。"""
    out = {}
    if not VIDEOS.exists():
        return out
    for d in VIDEOS.iterdir():
        if not d.is_dir() or not d.name.isdigit():
            continue
        sf = d / "_sessions" / "sessions.json"
        if sf.exists():
            try:
                out[d.name] = json.loads(sf.read_text(encoding="utf-8")).get("sessions", [])
            except Exception:
                pass
    return out


def session_ignore_toggle(room: str, sid: str) -> str:
    """切换某场次的"忽略场级总结"标记（复用 session.py CLI，幂等）。"""
    import subprocess
    r = subprocess.run(["python", str(ROOT / "session.py"), "--ignore", room, sid],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore",
                       cwd=str(ROOT), timeout=60)
    return ((r.stdout or "") + (r.stderr or "")).strip()[-200:] or "已切换"


def summaries_list(query: str = "") -> list:
    out = []
    items = []
    for s in sorted(VIDEOS.glob("*/*.summary.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        items.append((s, "segment"))
    for s in sorted(VIDEOS.glob("*/_sessions/*.summary.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        items.append((s, "session"))
    for s, kind in items:
        base = s.name.replace(".summary.md", "")
        # 场次总结在 Videos/<房间>/_sessions/ 下：房间号取上级目录（parent 是 _sessions）
        room = s.parent.parent.name if kind == "session" else s.parent.name
        item = {"room": room, "video": base + ".mp4",
                "base": base,
                "name": s.name, "kind": kind,
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(s.stat().st_mtime)),
                "title": "", "time_range": "", "segment_count": None, "one_liner": "",
                "ignored": False}
        if kind == "session":
            # 场次条目增强：标题/时间范围/段数（sessions.json）+ 一句话（summary 文件）
            try:
                data = json.loads((VIDEOS / room / "_sessions" / "sessions.json")
                                  .read_text(encoding="utf-8"))
                sess = next((x for x in data.get("sessions", []) if x["id"] == base), {})
                item["title"] = sess.get("title", "")
                item["time_range"] = (f"{sess.get('start','')[5:16]}–"
                                      f"{sess.get('end_est','')[11:16]}")
                item["segment_count"] = sess.get("segment_count")
                item["ignored"] = bool(sess.get("ignored"))
            except Exception:
                pass
            try:
                item["one_liner"] = _first_section_line(
                    s.read_text(encoding="utf-8", errors="ignore"), "一句话总结")
            except Exception:
                pass
        if query and query.lower() not in json.dumps(item, ensure_ascii=False).lower():
            continue
        out.append(item)
    # 场次排前排（产品定案：场级负责浏览，段级负责定位）
    out.sort(key=lambda r: (0 if r["kind"] == "session" else 1,
                            0 if r["kind"] == "session" else -time.mktime(
                                time.strptime(r["date"], "%Y-%m-%d %H:%M"))))
    return out


def get_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8") if PROMPT_FILE.exists() else DEFAULT_PROMPT


def set_prompt(text: str) -> tuple[bool, str]:
    if not text.strip():
        return False, "提示词不能为空"
    if "{srt}" not in text:
        return False, '必须包含 {srt} 占位符'
    bak = PROMPT_FILE.with_suffix(".txt.bak")
    if PROMPT_FILE.exists():
        bak.write_text(PROMPT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    PROMPT_FILE.write_text(text, encoding="utf-8")
    return True, "已保存（上一版本存于 prompt.txt.bak 可回滚）"


def get_prompt_bak() -> str | None:
    bak = PROMPT_FILE.with_suffix(".txt.bak")
    return bak.read_text(encoding="utf-8") if bak.exists() else None


# ---------- 任务队列（持久化 + 优先级 + 断电恢复） ----------
def _load_queue() -> dict:
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"jobs": [], "next_id": 1}


def _save_queue(q: dict):
    tmp = QUEUE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, QUEUE_FILE)


def enqueue(name: str, path: str, priority: int = 5) -> dict:
    """priority: 0=用户点按(插队) 5=批量补位；同文件已有 queued/running 则跳过"""
    with QUEUE_LOCK:
        q = _load_queue()
        for j in q["jobs"]:
            if j["path"] == path and j["status"] in ("queued", "running"):
                return {"queued": False, "started": False,
                        "reason": "已在队列中", "id": j["id"]}
        job = {"id": f"j{q['next_id']}", "name": name, "path": str(path),
               "priority": priority, "status": "queued",
               "created": time.strftime("%H:%M:%S"), "error": None}
        q["jobs"].append(job)
        q["next_id"] += 1
        _save_queue(q)
    return {"queued": True, "id": job["id"]}


def queue_snapshot() -> list:
    q = _load_queue()
    order = {"running": 0, "queued": 1, "failed": 2, "done": 3}
    jobs = sorted(q["jobs"], key=lambda j: (order.get(j["status"], 9), j["priority"],
                                            -int(j["id"][1:])))
    return [j for j in jobs if j["status"] != "done" or
            time.time() - os.path.getmtime(QUEUE_FILE) < 86400][:40] or \
           sorted(q["jobs"], key=lambda j: -int(j["id"][1:]))[:20]


def requeue_stale_running():
    """面板启动时：把上次中断的 running 重置为 queued（断电恢复）"""
    with QUEUE_LOCK:
        q = _load_queue()
        changed = False
        for j in q["jobs"]:
            if j["status"] == "running":
                j["status"] = "queued"; changed = True
        if changed:
            _save_queue(q)


def defer_job(job_id: str):
    """Worker 拿不到 run.lock 时把任务退回队列（稍后再跑）"""
    with QUEUE_LOCK:
        q = _load_queue()
        for j in q["jobs"]:
            if j["id"] == job_id and j["status"] == "running":
                j["status"] = "queued"
                _save_queue(q)
                return


def acquire_run_lock() -> bool:
    """非阻塞独占获取 run.lock（Worker 与 process_all.ps1 的互斥点）。
    open("x") 本身即原子"判锁+获取"，不再前置 lock_info() 探测（消除 TOCTOU 窗口）。"""
    try:
        global _run_lock_stream
        _run_lock_stream = open(LOCK, "x")
        _run_lock_stream.write(f"worker pid={os.getpid()} t={time.strftime('%H:%M:%S')}")
        _run_lock_stream.flush()
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def release_run_lock():
    try:
        _run_lock_stream.close()
    except Exception:
        pass
    LOCK.unlink(missing_ok=True)


def pop_next_job() -> dict | None:
    with QUEUE_LOCK:
        q = _load_queue()
        cand = [j for j in q["jobs"] if j["status"] == "queued"]
        if not cand:
            return None
        job = min(cand, key=lambda j: (j["priority"], int(j["id"][1:])))
        job["status"] = "running"   # 先改原引用再落盘（修复：此前在拷贝上置状态，落盘仍为 queued）
        _save_queue(q)
        return dict(job)


def finish_job(job_id: str, ok: bool, error: str | None = None):
    with QUEUE_LOCK:
        q = _load_queue()
        for j in q["jobs"]:
            if j["id"] == job_id:
                j["status"] = "done" if ok else "failed"
                j["error"] = error
                break
        # 只保留最近 60 条，防无限膨胀
        if len(q["jobs"]) > 60:
            done_ids = {j["id"] for j in q["jobs"] if j["status"] == "done"}
            keep = [j for j in q["jobs"] if j["status"] != "done"]
            dones = [j for j in q["jobs"] if j["id"] in done_ids][-30:]
            q["jobs"] = (keep + dones)[-60:]
        _save_queue(q)


# ---------- 房间（settings.toml 解析 + B站直播状态） ----------
def rooms_from_settings() -> list[dict]:
    if not SETTINGS.exists() or tomllib is None:
        return []
    try:
        data = SETTINGS.read_bytes().decode("utf-8-sig")   # 兼容历史 BOM
        data = data.replace("\r\n", "\n").replace("\r", "\n")  # 容错历史 \r\r\n 损坏
        cfg = tomllib.loads(data)
        return [{"room_id": t.get("room_id"),
                 "monitor": t.get("enable_monitor", False),
                 "recorder": t.get("enable_recorder", False)}
                for t in cfg.get("tasks", [])]
    except Exception as e:
        print("[rooms] 解析 settings.toml 失败:", repr(e)[:120], flush=True)
        return []


_live_cache: dict = {"ts": 0.0, "data": {}}


def live_status(room_ids: list[int]) -> dict:
    """B站房间直播状态，60s 缓存。失败容忍→unknown"""
    now = time.time()
    ids = [str(r) for r in room_ids]
    if now - _live_cache["ts"] < 60 and _live_cache["data"]:
        return {k: v for k, v in _live_cache["data"].items() if k in ids}
    def _fetch(rid: int) -> dict:
        entry = {"live_status": None, "title": "", "online": None}
        try:
            req = urllib.request.Request(
                f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={rid}",
                headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode("utf-8", "ignore")).get("data", {})
            entry.update({"live_status": d.get("live_status"),
                          "title": d.get("title", ""),
                          "online": d.get("online")})
        except Exception:
            pass
        return entry

    result = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(room_ids)))) as ex:
        for rid, entry in zip(room_ids, ex.map(_fetch, room_ids)):
            result[str(rid)] = entry
    _live_cache["ts"] = now
    _live_cache["data"].update(result)
    return {k: v for k, v in _live_cache["data"].items() if k in ids}


def _write_settings(text: str):
    """统一 LF 落盘，杜绝 Windows 换行二次翻译产生 \r\r\n。
    （2026-08-23 实锤：remove_room 用 read_bytes+write_text 组合把整个文件写成
    CRCRLF，tomllib 与容器内 blrec 解析双双崩溃。）"""
    data = text.replace("\r\n", "\n").replace("\r", "\n")
    SETTINGS.write_bytes(data.encode("utf-8"))


def add_room(room_id: int) -> tuple[bool, str]:
    text = SETTINGS.read_text(encoding="utf-8-sig") if SETTINGS.exists() else ""
    if f"room_id = {room_id}" in text:
        return False, "房间已存在"
    block = (f"\n[[tasks]]\nroom_id = {room_id}\n"
             "enable_monitor = true\nenable_recorder = true\n")
    anchor = "\n[output]"
    if anchor in text:
        text = text.replace(anchor, block + anchor, 1)
    else:
        text += block
    _write_settings(text)
    return True, "已写入 settings.toml（需重启容器生效）"


def remove_room(room_id: int) -> tuple[bool, str]:
    """块级解析移除房间——不影响其他房间（v3.2 修复：旧版会连带删除后续房间）"""
    if not SETTINGS.exists():
        return False, "settings.toml 不存在"
    text = SETTINGS.read_text(encoding="utf-8-sig")   # universal newlines：读入即归一
    lines = text.splitlines(keepends=True)
    out, removed = [], False
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if ln.strip() == "[[tasks]]":
            block = [ln]; i += 1
            while i < n:
                s = lines[i].strip()
                if s.startswith("[[tasks]]") or (s.startswith("[") and s.endswith("]") and not s.startswith("[[")):
                    break
                block.append(lines[i]); i += 1
            rid = None
            for bl in block:
                if bl.strip().startswith("room_id"):
                    rid = bl.split("=", 1)[1].strip()
            if rid == str(room_id):
                removed = True
                if i < n and lines[i].strip() == "":
                    i += 1
                continue
            out.extend(block)
            continue
        out.append(ln); i += 1
    if not removed:
        return False, "未找到该房间"
    _write_settings("".join(out))
    return True, "已移除（需重启容器生效）"


def validate_room(room_id: int) -> dict:
    """向 B 站校验房间真实性"""
    try:
        req = urllib.request.Request(
            f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        if d.get("code") == 0 and d.get("data"):
            return {"valid": True, "title": d["data"].get("title", ""),
                    "live_status": d["data"].get("live_status")}
        return {"valid": False, "reason": d.get("message") or f"code={d.get('code')}"}
    except Exception as e:
        return {"valid": False, "reason": repr(e)[:120]}

def get_api_key() -> str:
    """env → 用户注册表兜底（解决计划任务/不同父进程差异）"""
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k
    try:
        out = subprocess.run(["reg", "query", r"HKCU\Environment", "/v", "OPENROUTER_API_KEY"],
                             capture_output=True, text=True, timeout=8).stdout
        for ln in out.splitlines():
            if "OPENROUTER_API_KEY" in ln and "REG_SZ" in ln:
                return ln.split("REG_SZ")[-1].strip()
    except Exception:
        pass
    return ""


def is_placeholder_srt(srt_path: Path) -> bool:
    try:
        return _PLACEHOLDER_MARK in srt_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False