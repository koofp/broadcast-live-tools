# -*- coding: utf-8 -*-
"""跨场记忆回归（verify.ps1 会调用）：last_scorecard_block 的注入与空态。
机制：上一场的预测记分卡注入本场提示词，让 LLM 主动对账（已证伪/已验证滚动更新，
未发生的继续跟踪）。隔离：monkeypatch session.VIDEOS 到临时目录。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import session  # noqa: E402

REAL_VIDEOS = session.VIDEOS
PREV_ID = "20260831_235537"


def run() -> bool:
    with tempfile.TemporaryDirectory(prefix="bilive_memtest_") as td:
        session.VIDEOS = Path(td)
        try:
            room = "14323359"
            sd = session.VIDEOS / room / "_sessions"
            sd.mkdir(parents=True)
            prev = {"id": PREV_ID, "start": "2026-08-31 23:55:00"}
            card = ("| 存储新链 | 看多 | 回调即买 | 9月 | 待验证 |\n"
                    "| 老登 | 继续回避 | — | 长期 | 已证伪 |\n")

            # ① 上一场有记分卡 → 注入表格行、上一场时间与对账指令
            (sd / f"{PREV_ID}.summary.md").write_text(
                "# 场\n\n## 预测记分卡\n" + card, encoding="utf-8")
            blk = session.last_scorecard_block(prev, room)
            assert "2026-08-31 23:55" in blk, "应带上一场时间"
            assert "存储新链" in blk and "已证伪" in blk, "应注入记分卡行"
            assert "对账" in blk, "应携带对账指令"

            # ② 上一场无记分卡节 → 明确空态
            (sd / f"{PREV_ID}.summary.md").write_text("# 场\n没有记分卡\n", encoding="utf-8")
            assert "无明确预测" in session.last_scorecard_block(prev, room)

            # ③ 上一场总结文件缺失 → 明确空态
            (sd / f"{PREV_ID}.summary.md").unlink()
            assert "缺失" in session.last_scorecard_block(prev, room)

            # ④ 首场（无上一场）→ 明确空态
            assert "首场" in session.last_scorecard_block(None, room)
            return True
        finally:
            session.VIDEOS = REAL_VIDEOS


if __name__ == "__main__":
    ok = run()
    print("[PASS] 跨场记忆注入回归" if ok else "[FAIL]")
    sys.exit(0 if ok else 1)
