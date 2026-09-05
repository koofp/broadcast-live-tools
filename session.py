# -*- coding: utf-8 -*-
"""场次聚合器：把房间内分段按开始时间间隔聚类为"场次"，并生成场级二级总结。

模型（架构评审定案）：sessions 是"房间内所有分段按时间确定性聚类"的派生视图，
幂等重算；sessions.json 只是缓存。场级总结头部携带段指纹（数量:哈希），
消费端据此判断过期。段被 cleanup 归档后元数据保留（标 archived），场级总结
本身是压缩摘要，源数据蒸发属预期行为。

用法:
  python session.py                                  # 扫描全部房间，打印场次表并写缓存
  python session.py --room 8139918                   # 只看某房间
  python session.py --room 8139918 --summarize [场次ID]  # 生成场级总结（缺ID=全部已关闭且过期/缺失的；勿把房间号直接跟在 --summarize 后——会被当作场次ID 静默空转）
  python session.py --title 8139918 <场次ID> "TI决赛日"          # 场次命名
  python session.py --merge 8139918 <ID_A> <ID_B>    # 强制合并两场次
  python session.py --split 8139918 <场次ID> <段文件名>  # 该段起切分为新场次
"""
import argparse, hashlib, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
VIDEOS = ROOT / "bilive-docker" / "Videos"
GAP_MIN = 50           # 相邻段开始时间差阈值：≤GAP 同场（真实数据校准：场内最大抖动37min，跨天648min）
SEGMENT_EST_MIN = 30   # duration_limit，用于估算场次结束时间
CLOSED_MIN = 60        # 末段写入距今超过该分钟数 → 场次已关闭（可生成总结）
MIN_SEG_FOR_LLM = 3    # 少于该段数不触发 LLM 场级总结（段级已足够）

sys.path.insert(0, str(ROOT))
from summarize_host import call_oxalpha  # 复用模型/退避语义；429 重试循环在本文件内实现
import provider_config

# ---------- 时间戳解析（双命名格式，实测 46 文件全通过） ----------
_RE_STD = re.compile(r"_(\d{8})-(\d{2})-(\d{2})-(\d{2})")          # room_20260822-15-08-27.mp4
_RE_OLD = re.compile(r"_(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})")  # room_2026-08-22-13-31-.mp4


def parse_start(name: str):
    m = _RE_STD.search(name)
    if m:
        try:
            return datetime.strptime(f"{m[1]} {m[2]}:{m[3]}:{m[4]}", "%Y%m%d %H:%M:%S")
        except ValueError:
            return None
    m = _RE_OLD.search(name)
    if m:
        try:
            return datetime.strptime(f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return None


def fingerprint(names) -> str:
    h = hashlib.md5("|".join(names).encode("utf-8")).hexdigest()[:10]
    return f"{len(names)}:{h}"


# ---------- 扫描与聚类 ----------
def scan_room(room_dir: Path):
    segs = []
    for p in room_dir.glob("*"):
        if p.suffix not in (".mp4", ".flv"):
            continue
        st = parse_start(p.name)
        if st is None:
            print(f"[warn] 无法解析时间戳，跳过 {p.name}", flush=True)
            continue
        segs.append({"name": p.name, "start": st, "mtime": p.stat().st_mtime})
    segs.sort(key=lambda s: s["start"])
    return segs


def cluster(segs, gap_min: float, boundaries: set):
    """相邻段开始时间差 ≤ gap_min 归同场；boundaries 里的段名强制开启新场次。"""
    sessions, cur = [], None
    for s in segs:
        if cur is not None and s["name"] not in boundaries and \
                (s["start"] - cur["_last"]).total_seconds() <= gap_min * 60:
            cur["segs"].append(s)
        else:
            cur = {"segs": [s]}
            sessions.append(cur)
        cur["_last"] = s["start"]
    return sessions


def build_session(room: str, segs) -> dict:
    first, last = segs[0], segs[-1]
    est_end = last["start"] + timedelta(minutes=SEGMENT_EST_MIN)
    age_min = (time.time() - last["mtime"]) / 60
    return {
        "id": first["start"].strftime("%Y%m%d_%H%M%S"),
        "room": room,
        "title": "",
        "start": first["start"].strftime("%Y-%m-%d %H:%M:%S"),
        "end_est": est_end.strftime("%Y-%m-%d %H:%M:%S"),
        "segments": [{"name": s["name"], "start": s["start"].strftime("%Y-%m-%d %H:%M:%S")}
                     for s in segs],
        "segment_count": len(segs),
        "closed": age_min > CLOSED_MIN,
        "fingerprint": fingerprint([s["name"] for s in segs]),
        "archived_count": 0,
    }


def merge_archived(old: dict | None, new_sessions: list):
    """旧缓存里有、本次扫描已消失的段 → 以 archived 身份并回其原场次（元数据不蒸发）。
    注意无条件回填（含已带 archived 标记的）：否则第二次重算时归档元数据会蒸发（验收缺陷#1）。"""
    if not old:
        return
    by_id = {s["id"]: s for s in new_sessions}
    for os_ in old.get("sessions", []):
        ns = by_id.get(os_["id"])
        if not ns:
            continue
        known = {seg["name"] for seg in ns["segments"]}
        for seg in os_.get("segments", []):
            if seg["name"] not in known:
                a = dict(seg)
                a["archived"] = True
                ns["segments"].append(a)
        ns["segments"].sort(key=lambda s: s["start"])
        ns["segment_count"] = len(ns["segments"])   # 口径=含归档总数（与附录行数一致）
        ns["archived_count"] = sum(1 for s in ns["segments"] if s.get("archived"))


# ---------- 缓存读写 ----------
def sessions_file(room: str) -> Path:
    return VIDEOS / room / "_sessions" / "sessions.json"


def load_overrides(room: str) -> dict:
    p = VIDEOS / room / "_sessions" / "overrides.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(room: str, name: str, data: dict):
    d = VIDEOS / room / "_sessions"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / name)


def build_room(room: str, gap_min: float) -> dict:
    room_dir = VIDEOS / room
    segs = scan_room(room_dir)
    ov = load_overrides(room)
    clusters = cluster(segs, gap_min, set(ov.get("boundaries", [])))
    sessions = [build_session(room, c["segs"]) for c in clusters]
    # 手动合并（并查集式：把 merged 对里的所有 id 并到第一个存在的场次）
    for a, b in ov.get("merged", []):
        ia = next((i for i, s in enumerate(sessions) if s["id"] == a), None)
        ib = next((i for i, s in enumerate(sessions) if s["id"] == b), None)
        if ia is not None and ib is not None and ia != ib:
            lo, hi = sorted((ia, ib))
            sessions[lo]["segments"].extend(sessions[hi]["segments"])
            sessions[lo]["segments"].sort(key=lambda s: s["start"])
            sessions[lo]["segment_count"] = len(sessions[lo]["segments"])
            # 重算结束时间（验收缺陷#2：合并后 end_est 沿用旧值会污染时长）
            last_start = datetime.strptime(sessions[lo]["segments"][-1]["start"],
                                           "%Y-%m-%d %H:%M:%S")
            sessions[lo]["end_est"] = (last_start + timedelta(minutes=SEGMENT_EST_MIN)).strftime(
                "%Y-%m-%d %H:%M:%S")
            sessions[lo]["fingerprint"] = fingerprint([s["name"] for s in sessions[lo]["segments"] if not s.get("archived")])
            sessions[lo]["closed"] = sessions[lo]["closed"] and sessions[hi]["closed"]
            del sessions[hi]
    # 标题覆盖 + 忽略标记
    ignored_ids = set(ov.get("ignored", []))
    for s in sessions:
        s["title"] = ov.get("titles", {}).get(s["id"], "")
        s["ignored"] = s["id"] in ignored_ids
    old = None
    cache = sessions_file(room)
    if cache.exists():
        try:
            old = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            old = None
    merge_archived(old, sessions)
    save_json(room, "sessions.json", {"room": room, "gap_min": gap_min,
                                      "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                                      "sessions": sessions})
    return {"room": room, "sessions": sessions}


# ---------- 场级总结 ----------
def one_liner(p: Path) -> str:
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    m = re.search(r"##\s*一句话总结\s*\n+(.+)", t)
    if m and m.group(1).strip():
        return m.group(1).strip().splitlines()[0][:80]
    for ln in t.splitlines():
        if ln.strip() and not ln.startswith("#"):
            return ln.strip()[:80]
    return ""


def summary_path(room: str, sid: str) -> Path:
    return VIDEOS / room / "_sessions" / f"{sid}.summary.md"


def summary_state(session: dict) -> tuple[str, Path]:
    p = summary_path(session["room"], session["id"])
    if not p.exists():
        return "missing", p
    head = p.read_text(encoding="utf-8", errors="ignore")[:300]
    m = re.search(r"session-fingerprint:\s*([^\s-]+)", head)
    if not m:
        return "legacy", p
    return ("ok" if m.group(1) == session["fingerprint"] else "stale"), p


def gather_summaries(session: dict) -> str:
    room_dir = VIDEOS / session["room"]
    lines = []
    for seg in session["segments"]:
        hhmm = seg["start"][11:16]
        if seg.get("archived"):
            lines.append(f"[{hhmm}] （该段已归档清理，内容含于场级叙事中）")
            continue
        sp = room_dir / (Path(seg["name"]).stem + ".summary.md")
        ol = one_liner(sp) if sp.exists() else "（无逐段总结）"
        lines.append(f"[{hhmm}] {ol}")
    return "\n".join(lines)


def load_prompt_session(room: str) -> str:
    root = Path(__file__).resolve().parent
    for cand in (root / f"prompt_session.{room}.txt", root / "prompt_session.txt"):
        if cand.exists():
            tpl = cand.read_text(encoding="utf-8")
            if "{summaries}" in tpl:
                return tpl
            print(f"[warn] {cand.name} 缺 {{summaries}} 占位符，忽略", flush=True)
    return DEFAULT_SESSION_PROMPT


DEFAULT_SESSION_PROMPT = """你是资深直播内容分析师。下面是一场直播中各时间段的逐段总结（按时间顺序）。
请站在整场视角做二级总结：跨段提炼主线，不要复述单段细节。

各段总结：
{summaries}

请输出（Markdown）：
## 一句话总结
（100字内，整场核心主题）
## 时间线概览
（3-8个阶段节点，每节点标注大致时段 [HH:MM]）
## 关键要点
（3-5条，跨段提炼）
## 高光时刻
（2-3个，标注时段）
## 金句精选
（top 3-5，从各段金句中优中选优，标注时段）
"""


def call_with_retry(prompt: str, key: str) -> str:
    last = None
    for attempt in range(4):
        try:
            return call_oxalpha(prompt, key)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 429 and attempt < 3:
                wait = 60 * (attempt + 1)
                print(f"[429] {wait}s 后重试 ({attempt+1}/3)", flush=True)
                time.sleep(wait)
            else:
                break
        except Exception as e:
            last = repr(e)[:160]
            if attempt < 3:
                time.sleep(5 * (2 ** attempt))
    raise RuntimeError(f"LLM 调用失败: {last}")


def gen_session_summary(session: dict, key: str, force: bool) -> bool:
    state, p = summary_state(session)
    if state == "ok" and not force:
        print(f"[skip] {p.name} 指纹一致", flush=True)
        return True
    if session.get("ignored") and not force:
        print(f"[skip] 场次 {session['id']} 已标记忽略总结", flush=True)
        return True
    if not session["closed"] and not force:
        print(f"[skip] 场次 {session['id']} 未关闭（直播中），跳过", flush=True)
        return True
    live_segs = [s for s in session["segments"] if not s.get("archived")]
    if len(live_segs) < MIN_SEG_FOR_LLM:
        print(f"[skip] 场次 {session['id']} 仅 {len(live_segs)} 段，段级总结已足够", flush=True)
        return True
    tpl = load_prompt_session(session["room"])
    prompt = tpl.replace("{summaries}", gather_summaries(session))
    print(f"[session] 生成场级总结 {session['id']}（{len(live_segs)} 段）…", flush=True)
    text = call_with_retry(prompt, key)
    dur_h = (datetime.strptime(session["end_est"], "%Y-%m-%d %H:%M:%S")
             - datetime.strptime(session["start"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    title = session.get("title") or ""
    doc = (f"<!-- session-fingerprint: {session['fingerprint']} -->\n"
           f"# 场次总结 · {session['room']} · {title}{session['start'][:10]}\n\n"
           f"> {session['start'][11:16]}–{session['end_est'][11:16]}（约 {dur_h:.1f} 小时）· "
           f"{session['segment_count']} 段（含归档 {session.get('archived_count', 0)}）\n\n"
           f"{text}\n\n---\n\n## 附录 · 分段清单\n\n"
           f"| 时间 | 分段 | 状态 |\n|---|---|---|\n"
           + "\n".join(f"| {s['start'][11:16]} | `{s['name']}` | {'已归档' if s.get('archived') else '在库'} |"
                       for s in session["segments"])
           + "\n")
    tmp = str(p) + ".tmp"
    Path(tmp).write_text(doc, encoding="utf-8")
    os.replace(tmp, p)
    print(f"[done] {p.name} | 指纹 {session['fingerprint']}", flush=True)
    return True


def get_api_key() -> str:
    """API key：优先设置页 provider.json，回退 env / api_key.txt / 注册表。
    交由 provider_config 统一解析，确保 summarize 与面板/场级总结口径一致。"""
    return provider_config.resolve()["api_key"]

# ---------- 主流程 ----------
def print_table(data: dict):
    for s in data["sessions"]:
        state, _ = summary_state(s)
        title = s.get("title") or ""
        print(f"  {s['id']}  {s['start'][5:16]} ~ {s['end_est'][11:16]}  "
              f"{s['segment_count']}段(归档{s.get('archived_count', 0)})  "
              f"{'已关闭' if s['closed'] else '直播中'}  总结:{state}  {title}")


def main():
    ap = argparse.ArgumentParser(description="场次聚合与场级总结")
    ap.add_argument("--room", default="")
    ap.add_argument("--gap", type=float, default=GAP_MIN)
    ap.add_argument("--summarize", nargs="?", const="__all__", default=None,
                    help="生成场级总结（可指定场次ID；缺省=该房间全部已关闭且缺失/过期的）")
    ap.add_argument("--force", action="store_true", help="强制重新生成（忽略指纹/关闭状态）")
    ap.add_argument("--title", nargs=3, metavar=("ROOM", "SESSION_ID", "TITLE"))
    ap.add_argument("--ignore", nargs=2, metavar=("ROOM", "SESSION_ID"),
                    help="切换某场次的「忽略场级总结」标记（幂等）")
    ap.add_argument("--merge", nargs=3, metavar=("ROOM", "ID_A", "ID_B"))
    ap.add_argument("--split", nargs=3, metavar=("ROOM", "SESSION_ID", "SEGMENT_NAME"))
    a = ap.parse_args()

    if a.title:
        room, sid, title = a.title
        ov = load_overrides(room)
        ov.setdefault("titles", {})[sid] = title
        save_json(room, "overrides.json", ov)
        print(f"[done] 已命名 {sid} = {title}（重扫后生效）")
        build_room(room, a.gap)
        return
    if a.ignore:
        room, sid = a.ignore
        ov = load_overrides(room)
        lst = list(ov.get("ignored", []))
        if sid in lst:
            lst.remove(sid)
            print(f"[done] 已恢复场级总结: {sid}")
        else:
            lst.append(sid)
            print(f"[done] 已忽略场级总结: {sid}")
        ov["ignored"] = lst
        save_json(room, "overrides.json", ov)
        build_room(room, a.gap)
        return
    if a.merge:
        room, ida, idb = a.merge
        ov = load_overrides(room)
        ov.setdefault("merged", []).append([ida, idb])
        save_json(room, "overrides.json", ov)
        build_room(room, a.gap)
        print(f"[done] 已合并 {ida} + {idb}")
        return
    if a.split:
        room, sid, seg = a.split
        data = build_room(room, a.gap)   # 先重算，基于最新场次校验
        target = next((s for s in data["sessions"] if s["id"] == sid), None)
        if target is None or seg not in {x["name"] for x in target["segments"]}:
            print(f"[fail] 段 {seg} 不属于场次 {sid}（验收缺陷#4：参数校验）")
            sys.exit(1)
        ov = load_overrides(room)
        ov.setdefault("boundaries", []).append(seg)
        save_json(room, "overrides.json", ov)
        build_room(room, a.gap)
        print(f"[done] 已在 {seg} 处切分")
        return

    rooms = [a.room] if a.room else \
        [d.name for d in VIDEOS.iterdir() if d.is_dir() and d.name.isdigit()]
    results = {}
    for room in rooms:
        if not (VIDEOS / room).exists():
            print(f"[warn] 房间目录不存在: {room}")
            continue
        results[room] = build_room(room, a.gap)
        print(f"[{room}]")
        print_table(results[room])

    if a.summarize:
        key = get_api_key()
        if not key:
            print("[fatal] 未配置 API key（设置页 AI 供应商 / OPENROUTER_API_KEY / api_key.txt / 注册表 均为空）", flush=True)
            sys.exit(1)
        for room, data in results.items():
            for s in data["sessions"]:
                if a.summarize != "__all__" and s["id"] != a.summarize:
                    continue
                try:
                    gen_session_summary(s, key, force=a.force)
                except Exception as e:
                    print(f"[FAIL] {s['id']}: {repr(e)[:200]}", flush=True)


if __name__ == "__main__":
    main()
