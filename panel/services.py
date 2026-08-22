# -*- coding: utf-8 -*-
"""服务层 v2：状态/文件/流水线/归档（无路由逻辑）
规范：pathlib+utf-8；subprocess 显式编码+cwd；tail 反向截断读取；进度正则唯一出处。
"""
import json
import re
import subprocess
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "bilive-docker" / "Videos"
PROMPT_FILE = ROOT / "prompt.txt"
LOCK = ROOT / "run.lock"
PS_STATUS = str(ROOT / "status.ps1")
PS_PROC = str(ROOT / "process_all.ps1")
PS_CLEAN = str(ROOT / "cleanup.ps1")
LOG_DIR = ROOT / "logs" / "pipeline"

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
    """锁的结构化信息：谁持有(时间戳)/多久——供 409 与面板展示"""
    if not LOCK.exists():
        return {"locked": False}
    age = round(time.time() - LOCK.stat().st_mtime)
    holder = ""
    try:
        holder = LOCK.read_text(encoding="utf-8", errors="ignore")[:80]
    except Exception:
        pass
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
                "status_label": label, "status_class": cls,
                "is_current": ((time.time() - m.stat().st_mtime) < 600),
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