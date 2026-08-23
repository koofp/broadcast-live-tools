# -*- coding: utf-8 -*-
"""无人值守产出质量验收 v2（评审采纳版）
用法: python qa_check.py <srt路径>
输出: 控制台报告 + 同目录 quality_report.json / .md
分级: critical(任一即不合格) / warning(提示)
"""
import re, sys, json, time, time
from pathlib import Path

BAD_PATTERNS = [
    r"Thank you for watching", r"请不吝点赞", r"字幕由 ?Amara\.org",
    r"字幕由.*提供", r"Subscribe to", r"请订阅",
]

def ts2ms(t):
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h)*3600000 + int(m)*60000 + int(s)*1000 + int(ms)

def main(srt_path: Path):
    critical, warnings = [], []
    raw = srt_path.read_text(encoding="utf-8-sig", errors="ignore")

    # UTF-8 完整性
    if "\ufffd" in raw:
        critical.append("存在替换字符 \\ufffd（编码损坏）")

    blocks = [b for b in raw.split("\n\n") if b.strip()]
    texts, starts, ends, durs = [], [], [], []
    for b in blocks:
        m = re.match(r"\d+\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*)", b, re.S)
        if not m: continue
        st, en = ts2ms(m.group(1)), ts2ms(m.group(2))
        txt = m.group(3).strip()
        starts.append(st); ends.append(en); durs.append(en-st); texts.append(txt)

    n = len(texts)
    total_chars = sum(len(t) for t in texts)
    dur_min = (ends[-1]/60000) if ends else 0
    density = total_chars/dur_min if dur_min else 0

    def chk(name, ok, level="warning", detail=""):
        if not ok:
            (critical if level == "critical" else warnings).append(
                f"{name} — {detail}" if detail else name)

    # C1 内容量：字数密度 ≥60字/分钟
    chk(f"C1 字幕非空且有内容量", n>0 and total_chars>0, detail=f"{n}条/{total_chars}字符")
    if dur_min > 0:
        chk("C2 字数密度≥60字/分", density >= 60, "warning" if density>=30 else "critical",
            f"{density:.0f}字/分")
    # C3 巨块：单条>20s
    big = [(i, round(d/1000)) for i, d in enumerate(durs) if d > 20000]
    chk("C3 无>20s巨块字幕", not big, "warning", f"{len(big)}处 {big[:3]}")
    # C4 时间戳单调
    mono = all(b >= a for a, b in zip(starts, starts[1:])) and all(e>=s for s,e in zip(starts,ends))
    chk("C4 时间戳单调且起止合法", mono)
    # C5 首尾边界
    if starts: chk("C5 首条从≈0开始", starts[0] < 5000, "warning", f"{starts[0]}ms")
    # C6 幻觉：相邻重复连续≥3
    runs, run = [], 1
    for i in range(1, n):
        if texts[i] == texts[i-1] and texts[i]: run += 1
        else: run = 1
        if run == 3: runs.append(texts[i][:30])
    chk("C6 无重复循环(幻觉)", not runs, detail=f"{len(runs)}处 {runs[:2]}")
    # C7 训练残留黑名单
    joined = raw.lower()
    hits = [p for p in BAD_PATTERNS if re.search(p, joined)]
    chk("C7 无训练残留话术", not hits, detail=str(hits))

    # ---- summary 检查 ----
    sm_path = srt_path.with_suffix(".summary.md")
    sm = sm_path.read_text(encoding="utf-8") if sm_path.exists() else ""
    need = ["一句话总结", "核心主题", "讨论要点", "金句", "识别错误"]
    found = [k for k in need if k in sm]
    chk("S1 summary 五段结构齐全", len(found) >= 4, "warning", f"{len(found)}/5")
    chk("S2 无占位符残留", "[无语音内容]" not in sm and "{srt}" not in sm)
    # S3 [mm:ss] 落在 srt 时间范围内
    mm_list = re.findall(r"\[(\d{2}):(\d{2})\]", sm)
    if mm_list and ends:
        max_ms = ends[-1]
        bad_mm = [f"{h}:{m}" for h, m in mm_list if int(h)*60000+int(m)*1000 > max_ms]
        chk("S3 时间戳引用均在范围内", not bad_mm, "warning", str(bad_mm[:3]))
    # S4 金句可回查（取前2条金句的文本片段在 srt 中模糊命中）
    sec = re.search(r"## 金句[^\n]*\n(.*?)(?=\n## |\Z)", sm, re.S)
    if sec:
        quotes = re.findall(r"[「“]?([^「”\n\[\]]{6,})[」”]?", sec.group(1))[:2]
        flat = "".join(texts)
        miss = [q[:12] for q in quotes if len(q) >= 6 and not any(
            q[i:i+4] in flat for i in range(0, max(1, len(q)-3), 3))]
        chk("S4 金句可在字幕中回查", not miss, "warning", str(miss))

    # ---- 报告 ----
    ok = not critical
    sample = []
    for i in sorted({0, n//2, n-1}):
        if 0 <= i < n:
            sample.append(f"[{i+1}] ({starts[i]//60000}:{(starts[i]%60000)//1000:02d}) {texts[i][:60]}")

    crit_l = [f"- {c}" for c in critical] or ["- 无"]
    warn_l = [f"- {w}" for w in warnings] or ["- 无"]
    md_lines = ([f"# 质量验收 · {srt_path.stem}", "",
        f"- 结论: {'✅ PASS' if ok else '❌ FAIL'} | 字幕 {n} 条 / {total_chars} 字 | "
        f"密度 {density:.0f}字/分 | 音频 {dur_min:.0f} 分钟",
        "", "## Critical", *crit_l, "", "## Warning", *warn_l, "", "## 抽样试读"] + [f"> {s}" for s in sample])
    out_md = srt_path.with_name(srt_path.stem.replace(".mp4","").replace(".flv","") + "_qa.md")
    out_md.write_text("\n".join(md_lines), encoding="utf-8")
    out_json = srt_path.with_name(srt_path.stem + "_qa.json")
    out_json.write_text(json.dumps({
        "file": srt_path.name, "pass": ok, "segments": n, "chars": total_chars,
        "density_per_min": round(density), "critical": critical, "warnings": warnings,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n".join(md_lines))
    print(f"\n[PASS={ok}] 报告: {out_md.name} / {out_json.name}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main(Path(sys.argv[1]).resolve()) else 1)

