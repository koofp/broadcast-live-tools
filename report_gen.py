# -*- coding: utf-8 -*-
"""全量复盘报告：遍历所有房间分段 → 语音时长/字幕条数/一句话总结 → REPORT.md + report.json
用法: python report_gen.py [--out REPORT.md]
说明:
  - 时长口径 = 字幕末时间戳（语音区间），非视频文件时长，表头已注明
  - 占位 srt 标注「无语音」；summary 缺「一句话总结」时取首个非空行
  - 与 cleanup 竞争安全：文件消失即跳过；deleted.log 中已归档的分段合并标注 archived
"""
import json, re, sys, time
from pathlib import Path

try:  # 防 GBK 管道下「→」等字符输出崩溃
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
VIDEOS = ROOT / "bilive-docker" / "Videos"
DELETED_LOG = ROOT / "logs" / "pipeline" / "deleted.log"   # 修复：原误写 bilive-docker/logs（cleanup 实际写在根 logs）


def ts2sec(t):
    h, m, rest = t.split(":")
    s = float(rest.replace(",", "."))
    return int(h)*3600 + int(m)*60 + s


def parse_srt(p: Path):
    raw = p.read_text(encoding="utf-8-sig", errors="ignore").replace("\r\n", "\n")   # sig 剥 BOM 防坏首块
    if "[无语音内容]" in raw:
        return {"placeholder": True, "count": 0, "first": None, "last": None, "chars": 0}
    blocks = [b for b in raw.split("\n\n") if b.strip()]
    starts, texts = [], []
    for b in blocks:
        m = re.match(r"\d+\n(\d{2}:\d{2}:\d{2},\d{3}) --> ", b)
        if m:
            starts.append(m.group(1))
        t = b.split("\n", 2)[-1].strip() if "\n" in b else ""
        if t: texts.append(t)
    return {"placeholder": False, "count": len(texts),
            "first": starts[0] if starts else None,
            "last": starts[-1] if starts else None,
            "chars": sum(len(t) for t in texts)}


def one_liner(sm_path: Path) -> str | None:
    try:
        t = sm_path.read_text(encoding="utf-8")
    except Exception:
        return None
    m = re.search(r"##\s*一句话总结\s*\n+(.+)", t)
    if m and m.group(1).strip():
        return m.group(1).strip().splitlines()[0][:80]
    for ln in t.splitlines():
        if ln.strip() and not ln.startswith("#"):
            return ln.strip()[:80]
    return None


def _md_section(t: str, name: str) -> str:
    m = re.search(rf"##\s*{name}[^\n]*\n+(.*?)(?=\n## |\Z)", t, re.S)
    return m.group(1).strip() if m else ""


def session_lines(room: Path) -> list:
    """场次视图（读 session.py 的缓存与场级总结，含实际内容；无则跳过）。"""
    sf = room / "_sessions" / "sessions.json"
    if not sf.exists():
        return []
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        return []
    sessions = data.get("sessions", [])
    if not sessions:
        return []
    out = [f"### 房间 {room.name}"]
    for s in sessions:
        sp = room / "_sessions" / f"{s['id']}.summary.md"
        title = s.get("title") or ""
        head = (f"#### {s['start'][:10]} {title} {s['start'][11:16]}–{s['end_est'][11:16]} · "
                f"{s['segment_count']} 段").rstrip()
        out += ["", head]
        if not sp.exists():
            out.append("（尚无场级总结——`python session.py --summarize "
                       f"{room.name} {s['id']}` 生成）")
            continue
        txt = sp.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"session-fingerprint:\s*([^\s-]+)", txt[:300])
        stale = not (m and m.group(1) == s.get("fingerprint"))
        one = _md_section(txt, "一句话总结")
        pts = _md_section(txt, "关键要点")
        hl = _md_section(txt, "高光时刻")
        if one:
            out += ["", f"**一句话**：{one}"]
        if pts:
            out += ["", "**要点**："] + [f"- {ln.strip()}" for ln in pts.splitlines() if ln.strip()]
        if hl:
            out += ["", "**高光**："] + [f"- {ln.strip()}" for ln in hl.splitlines() if ln.strip()]
        out += ["", f"完整场级总结：`_sessions/{s['id']}.summary.md`"
                + ("（⚠️指纹过期，建议重跑 --summarize）" if stale else "")]
    out.append("")
    return out


def main(out_file="REPORT.md"):
    # 已归档（deleted.log）合并进来，保证复盘不随清理蒸发
    archived = set()
    if DELETED_LOG.exists():
        for ln in DELETED_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.search(r"DELETED\t(.+)$", ln)
            if m:
                p = Path(m.group(1))
                # 修复：按「房间/文件名」匹配（原只取文件名，跨房间同名会互相误标且丢失溯源）
                archived.add(f"{p.parent.name}/{p.name}")

    rows = []
    for room in sorted(VIDEOS.iterdir()):
        if not room.is_dir():
            continue
        mp4_names = {p.stem for p in room.glob("*.mp4")}
        cands = list(room.glob("*.mp4")) + \
                [f for f in room.glob("*.flv") if f.stem not in mp4_names]
        for vid in sorted(cands, key=lambda p: p.name):
            stem = vid.with_suffix("")
            srt = stem.with_suffix(".srt")
            summ = stem.with_suffix(".summary.md")
            row = {"room": room.name, "video": vid.name,
                   "archived": f"{room.name}/{vid.name}" in archived}
            if srt.exists():
                try:
                    info = parse_srt(srt)
                except Exception:
                    info = {"placeholder": False, "count": 0, "first": None, "last": None, "chars": 0}
                row.update({"语音分钟": round((ts2sec(info["last"])/60) if info["last"] else 0, 1) if not info["placeholder"] else 0,
                            "字幕条数": info["count"],
                            "无语音": info["placeholder"]})
            else:
                row.update({"语音分钟": None, "字幕条数": None, "无语音": None})
            row["总结"] = "有" if summ.exists() else "-"
            ol = one_liner(summ) if summ.exists() else None
            row["一句话"] = ol or ""
            rows.append(row)

    lines = ["# bilive 录播复盘总览",
             "",
             f"> 生成时间 {time.strftime('%Y-%m-%d %H:%M')} · "
             "时长口径=字幕语音区间(≠视频全长) · 「无语音」=VAD 判定无可转写内容 · archived=已归档清理",
             "",
             "| 房间 | 分段 | 语音分钟 | 字幕条数 | 无语音 | 总结 | 一句话总结 |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        tag = " *(archived)*" if r["archived"] else ""
        nov = '是' if r.get('无语音') else '-'
        lines.append(f"| {r['room']}{tag} | {r['video']} | {r.get('语音分钟','n/a')} | "
                     f"{r.get('字幕条数','n/a')} | {nov} | {r.get('总结','-')} | {(r.get('一句话') or '')[:50]} |")

    done = sum(1 for r in rows if r["总结"] == "有")
    # 场次视图前置为主视图（含实际总结内容）；逐段平铺表退居其后作为明细。
    # 插入点按表头定位（评审[中]：不硬编码头部行数，头部增删行不致错位）
    session_blocks = []
    for room in sorted(VIDEOS.iterdir()):
        if room.is_dir():
            out_lines = session_lines(room)
            if out_lines:
                session_blocks += ["", "## 场次视图（整场汇报）"] + out_lines
    if session_blocks:
        split_at = next((i for i, ln in enumerate(lines)
                         if ln.startswith("| 房间 | 分段 |")), None)
        if split_at is None:
            lines += session_blocks
        else:
            lines = (lines[:split_at] + session_blocks
                     + ["", "---", "", "## 逐段明细"] + lines[split_at:])
    out = ROOT / out_file
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[done] {len(rows)} 段（含总结 {done}）→ {out}")


if __name__ == "__main__":
    out = "REPORT.md"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out")+1]
    main(out)
