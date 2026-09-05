# -*- coding: utf-8 -*-
"""run.lock 独占语义回归（verify.ps1 会调用）。
背景：acquire 用 CRT open("x")（共享全允许）+ lock_info 用 open("r+b") 探测，
会把 Worker 活锁误判为陈旧锁删除 → 互斥失效 + release PermissionError 误标任务失败。
修复后 acquire/探测均为 CreateFileW share=0 真独占，与 process_all.ps1 的 FileStream 互认。"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from panel import services  # noqa: E402

# 隔离（评审 P1）：不得操作真实仓库根的 run.lock——计划任务/面板 Worker 持锁时
# verify 会假失败，测试自身也会清掉运维现场的陈旧锁。全流程使用临时目录锁。
# patch 在 run() 内进行（import 时 patch 会让"作为模块导入"的其他代码继承脏 LOCK）。
_REAL_LOCK = services.ROOT / "run.lock"


def run() -> bool:
    with tempfile.TemporaryDirectory(prefix="bilive_locktest_") as td:
        services.LOCK = Path(td) / "run.lock"
        LOCK = services.LOCK
        services.release_run_lock()   # 清场
        try:
            assert services.acquire_run_lock() is True, "首次获取应成功"
            assert LOCK.exists(), "锁文件应存在"
            info = services.lock_info()
            assert info.get("locked") is True, f"活锁必须被识别为 locked（旧代码会误删）: {info}"
            assert "stale_cleaned" not in info, f"活锁不得被清除: {info}"
            assert services.acquire_run_lock() is False, "二次获取必须失败（互斥）"

            services.release_run_lock()
            assert not LOCK.exists(), "释放后锁文件应消失"
            assert services.lock_info().get("locked") is False, "释放后应无锁"

            # 陈旧锁（文件在、无持有者）→ 探测应自动清除
            LOCK.write_text("dead", encoding="utf-8")
            info = services.lock_info()
            assert info.get("stale_cleaned") is True and not LOCK.exists(), \
                f"陈旧锁应被清除: {info}"

            # 陈旧锁（文件在、无持有者）→ acquire 自愈：清除后直接获取成功
            # （评审 P2 采纳：面板被杀遗留的锁文件不再让 Worker 空转等状态线程来清）
            LOCK.write_text("dead2", encoding="utf-8")
            assert services.acquire_run_lock() is True, "陈旧锁应被 acquire 自愈获取"
            services.release_run_lock()
            return True
        finally:
            services.release_run_lock()


if __name__ == "__main__":
    ok = run()
    print("[PASS] run.lock 独占语义回归" if ok else "[FAIL]")
    sys.exit(0 if ok else 1)
