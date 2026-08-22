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


def status() -> dict:
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
    ctn = subprocess.run(["docker", "ps", "--filter", "name=bilive_docker",
                          "--format", "{{.Status}}"],
                         **{k: v for k, v in _SUBPROC_KW.items() if k != 'cwd'}).stdout.strip()
    d["container"] = ctn or "stopped"
    d.update(lock_info())
    d["ts"] = time.strftime("%H:%M:%S")
    return d


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
                "status_label": ("录制中" if (time.time() - m.stat().st_mtime) < 600 else label)
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
    logs = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime)
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
        if active is None and "[1/2] 转写 " in ln:
            active = ln.split("转写 ")[-1].strip()
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
    return {"locked": LOCK.exists(), **lock_info(), "active_file": active,
            "progress": progress, "progress_ts": progress_ts,
            "tail": lines[-30:], "failures": fails}


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
    """ext 形如 '.summary.md'/'.srt'；兼容传入名带或不带该后缀"""
    room_dir = VIDEOS / room
    stem = name[:-len(ext)] if name.endswith(ext) else \
           (name[:-4] if name.endswith(".mp4") or name.endswith(".flv") else name)
    cand = room_dir / (stem + ext)
    return cand if cand.exists() else None


def find_summary(room: str, name: str) -> Path | None:
    return _resolve_out(room, name, ".summary.md")


def find_srt(room: str, name: str) -> Path | None:
    return _resolve_out(room, name, ".srt")


def summaries_list(query: str = "") -> list:
    out = []
    for s in sorted(VIDEOS.glob("*/*.summary.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        item = {"room": s.parent.name, "video": s.name.replace(".summary.md", ".mp4"),
                "base": s.name.replace(".summary.md", ""),
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(s.stat().st_mtime))}
        if query and query.lower() not in json.dumps(item, ensure_ascii=False).lower():
            continue
        out.append(item)
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
    """非阻塞独占获取 run.lock（Worker 与 process_all.ps1 的互斥点）"""
    if lock_info().get("locked"):
        return False
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
        job = dict(job)
        job["status"] = "running"
        _save_queue(q)
        return job


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
    result = {}
    for rid in room_ids:
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
        result[str(rid)] = entry
    _live_cache["ts"] = now
    _live_cache["data"].update(result)
    return {k: v for k, v in _live_cache["data"].items() if k in ids}


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
    SETTINGS.write_text(text, encoding="utf-8")
    return True, "已写入 settings.toml（需重启容器生效）"


def remove_room(room_id: int) -> tuple[bool, str]:
    if not SETTINGS.exists():
        return False, "settings.toml 不存在"
    lines = SETTINGS.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    out, skip, removed = [], False, 0
    for ln in lines:
        s = ln.strip()
        if s.startswith("[[tasks]]"):
            skip = True; removed_here = False
            continue
        if skip:
            if s.startswith("[") and not s.startswith("[[tasks]]"):
                skip = False
            elif s.startswith("room_id") and s.split("=")[1].strip() == str(room_id):
                removed = 1; continue
            elif s == "":
                continue
            else:
                continue
        out.append(ln)
    if not removed:
        return False, "未找到该房间"
    SETTINGS.write_text("".join(out), encoding="utf-8")
    return True, "已移除（需重启容器生效）"


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