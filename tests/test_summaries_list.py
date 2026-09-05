# -*- coding: utf-8 -*-
"""总结库可见性回归（verify.ps1 会调用）。
背景（2026-09-05 实锤）：隐藏规则原为"房间级"——同房间存在任意场级总结就隐藏全部
段级条目，14323359 的 15 条段级总结因 sessions.json stale（仅 1 场）整库隐身。
修复后按"有场级总结文件的场次所列段"精确隐藏；sessions.json 缺失/损坏时
fail-open 全部可见。隐藏≠删除：直接 URL 仍可经 find_summary 访问。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from panel import services  # noqa: E402

REAL_VIDEOS = services.VIDEOS


def _mk(p: Path, content: str = "## 一句话总结\n测试段"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def build_fixture(tmp: Path):
    # 房间 111：5 段被场级覆盖 + 3 段未被覆盖（部分覆盖 = 原 bug 的隐藏误伤面）
    covered = [f"111_20260825-23-{m:02d}-00" for m in (30, 31, 32, 33, 34)]
    uncovered = [f"111_20260826-01-{m:02d}-00" for m in (0, 1, 2)]
    for st in covered + uncovered:
        _mk(tmp / "111" / f"{st}.summary.md")
    _mk(tmp / "111" / "_sessions" / "20260825_233058.summary.md",
        "<!-- session-fingerprint: 5:abc -->\n# 场\n\n## 一句话总结\n场级总结")
    _mk(tmp / "111" / "_sessions" / "sessions.json", json.dumps({"sessions": [{
        "id": "20260825_233058", "room": "111", "title": "覆盖场",
        "start": "2026-08-25 23:30:58", "end_est": "2026-08-26 00:00:58",
        "segment_count": 5, "ignored": False,
        "segments": [{"name": f"{st}.mp4", "start": "2026-08-25 23:30:00"} for st in covered],
    }]}))
    # 房间 222：有 sessions.json 但无任何场级 summary → 段级必须全可见（评审 B2 击穿点）
    _mk(tmp / "222" / "222_20260825-19-23-13.summary.md")
    _mk(tmp / "222" / "_sessions" / "sessions.json", json.dumps({"sessions": [{
        "id": "20260825_192313", "room": "222",
        "segments": [{"name": "222_20260825-19-23-13.mp4", "start": "x"}]}]}))
    # 房间 333：无 _sessions 目录 → 段级可见（评审 B4）
    _mk(tmp / "333" / "333_20260826-01-55-24.summary.md")
    # 房间 444：有场级 summary 但 sessions.json 缺失 → fail-open：段级可见、场级条目在
    _mk(tmp / "444" / "444_20260826-02-25-20.summary.md")
    _mk(tmp / "444" / "_sessions" / "20260826_022520.summary.md")
    return covered, uncovered


def run() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="bilive_sumtest_"))
    services.VIDEOS = tmp
    try:
        covered, uncovered = build_fixture(tmp)
        rows = services.summaries_list()
        by = {(r["room"], r["kind"], r["base"]) for r in rows}
        # 111：场级在；被覆盖 5 段隐藏；未覆盖 3 段可见（房间级旧规则下这里必红）
        assert ("111", "session", "20260825_233058") in by, "场级条目应在"
        for st in covered:
            assert ("111", "segment", st) not in by, f"被覆盖段应隐藏: {st}"
        for st in uncovered:
            assert ("111", "segment", st) in by, f"未覆盖段应可见（旧房间级规则误伤）: {st}"
        # 场级元数据增强不回归（title 来自 sessions.json）
        meta = next(r for r in rows if (r["room"], r["kind"], r["base"])
                    == ("111", "session", "20260825_233058"))
        assert meta["title"] == "覆盖场" and meta["segment_count"] == 5
        # 222：无场级 summary → 段级可见
        assert ("222", "segment", "222_20260825-19-23-13") in by, "222 段级被误藏"
        # 333：无 _sessions → 可见
        assert ("333", "segment", "333_20260826-01-55-24") in by, "333 段级被误藏"
        # 444：sessions.json 缺失 → fail-open（段级可见 + 场级条目仍在）
        assert ("444", "segment", "444_20260826-02-25-20") in by, "444 段级被误藏"
        assert ("444", "session", "20260826_022520") in by, "444 场级条目应在"
        return True
    finally:
        services.VIDEOS = REAL_VIDEOS   # 必须还原（test_run_lock 先例）


if __name__ == "__main__":
    ok = run()
    print("[PASS] summaries_list 覆盖式隐藏回归" if ok else "[FAIL]")
    sys.exit(0 if ok else 1)
