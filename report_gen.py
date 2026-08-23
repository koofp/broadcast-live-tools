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

ROOT = Path(__file__).resolve().parent
VIDEOS = ROOT / "bilive-docker" / "Videos"
DELETED_LOG = ROOT / "bilive-docker" / "logs" / "pipeline" / "deleted.log"


def ts2sec(t):
    h, m, rest = t.split(":")
    s = float(rest.replace(",", "."))
    return int(h)*3600 + int(m)*60 + s


def parse_srt(p: Path):
    raw = p.read_text(encoding="utf-8", errors="ignore")
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


def main(out_file="REPORT.md"):
    # 已归档（deleted.log）合并进来，保证复盘不随清理蒸发
    archived = set()
    if DELETED_LOG.exists():
        for ln in DELETED_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.search(r"DELETED\t(.+)$", ln)
            if m:
                archived.add(Path(m.group(1)).name)

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
            row = {"room": room.name, "video": vid.name, "archived": vid.name in archived}
            if srt.exists():
                info = parse_srt(srt)
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
             "| 房间 | 分段 | 语音分钟 | 字幕条数 | 总结 | 一句话总结 |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        tag = " *(archived)*" if r["archived"] else ""
        lines.append(f"| {r['room']}{tag} | {r['video']} | {r.get('语音分钟','n/a')} | "
                     f"{r.get('字幕条数','n/a')} | {r.get('总结','-')} | {(r.get('一句话') or '')[:50]} |")

    done = sum(1 for r in rows if r["总结"] == "有")
    out = ROOT / out_file
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[done] {len(rows)} 段（含总结 {done}）→ {out}")


if __name__ == "__main__":
    out = "REPORT.md"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out")+1]
    main(out)
