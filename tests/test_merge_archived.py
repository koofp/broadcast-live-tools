# -*- coding: utf-8 -*-
"""merge_archived 归档回填的跨重算幂等回归测试（verify.ps1 会调用）。
场景：段被 cleanup 清理后，sessions.json 重算时归档元数据必须保留且不重复。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import session  # noqa: E402


def run() -> bool:
    # 第一次：旧缓存含已归档段，新扫描无该段 → 应回填
    old = {"sessions": [{"id": "A", "segments": [
        {"name": "gone.mp4", "start": "2026-08-22 13:31:00", "archived": True},
        {"name": "live.mp4", "start": "2026-08-22 14:01:00"},
    ]}]}
    new = [{"id": "A", "room": "R", "segments": [
        {"name": "live.mp4", "start": "2026-08-22 14:01:00"}],
        "archived_count": 0, "segment_count": 0}]
    session.merge_archived(old, new)
    segs = {x["name"]: x for x in new[0]["segments"]}
    assert segs["gone.mp4"]["archived"] is True, "归档段未回填"
    assert new[0]["archived_count"] == 1
    assert new[0]["segment_count"] == 2, "segment_count 应含归档"

    # 第二次：结果序列化再喂回（模拟再次重算）→ 不丢失、不重复
    old2 = {"sessions": [{"id": "A", "segments": new[0]["segments"]}]}
    new2 = [{"id": "A", "room": "R", "segments": [
        {"name": "live.mp4", "start": "2026-08-22 14:01:00"}],
        "archived_count": 0, "segment_count": 0}]
    session.merge_archived(old2, new2)
    arch = [x for x in new2[0]["segments"] if x["name"] == "gone.mp4"]
    assert len(arch) == 1, "重复回填"
    assert arch[0]["archived"] is True, "二次重算归档标记丢失"

    # 第三次（2026-09-05 评审）：整场段全部归档 → 新聚类中该场消失 → 必须以纯归档
    # 身份重建（元数据不蒸发承诺）。fingerprint 沿用旧值 → 场级总结不误判 stale。
    old3 = {"sessions": [{"id": "B", "room": "R", "title": "旧场", "fingerprint": "2:deadbeef",
                          "start": "2026-08-20 10:00:00", "end_est": "2026-08-20 11:00:00",
                          "segments": [
                              {"name": "b1.mp4", "start": "2026-08-20 10:00:00"},
                              {"name": "b2.mp4", "start": "2026-08-20 10:30:00"}]}]}
    new3 = []   # 该场在新扫描中完全消失（全部段已被 cleanup）
    session.merge_archived(old3, new3)
    assert len(new3) == 1, "整场归档的场必须被重建"
    b = new3[0]
    assert b["id"] == "B" and b["title"] == "旧场", "重建场元数据应保留"
    assert b["closed"] is True and b["archived_count"] == 2 and b["segment_count"] == 2
    assert all(s.get("archived") for s in b["segments"]), "重建场所有段应标 archived"
    assert b["fingerprint"] == "2:deadbeef", "重建场指纹沿用旧值（场级总结不误判 stale）"

    # 第四次：重建结果再喂回 → 幂等（不重复追加、不丢字段）
    old4 = {"sessions": [b]}
    new4 = []
    session.merge_archived(old4, new4)
    assert len(new4) == 1 and new4[0]["archived_count"] == 2, "重建场再次重算应幂等"
    return True


if __name__ == "__main__":
    ok = run()
    print("[PASS] merge_archived 回归" if ok else "[FAIL]")
    sys.exit(0 if ok else 1)
