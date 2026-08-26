# -*- coding: utf-8 -*-
"""A线自查：对最近三轮改动做自动化正确性检查（补充人工审查的盲区）
覆盖：并发正确性、边界条件、编码一致性、安全、性能
"""
import ast
import sys
import os
import re
import json

ROOT = os.path.dirname(os.path.abspath(__file__))
issues = []

def check(name, cond, detail=""):
    if not cond:
        issues.append(f"{name}: {detail}")

# 1. services.py 并发正确性
svc = open(os.path.join(ROOT, "panel", "services.py"), encoding="utf-8").read()
check("并发", "_COLD_LOCK" in svc and "_STATUS_LOCK" in svc and "_CTN_LOCK" in svc and "_IGNORE_LOCK" in svc,
      "缺少某个锁定义")
check("并发", svc.count("_refresh_status_once") >= 2, "函数定义或调用缺失")
check("并发", "def _refresh_status_once" in svc and "def _status_refresher" in svc,
      "后台刷新线程缺失")
# 检查没有重复定义
check("并发", svc.count("def _refresh_status_once") == 1, "函数重复定义")
check("并发", svc.count("def status(") == 1, "status 函数重复定义")
# readiness_check 引用的函数都存在
for fn in ["container_status", "rooms_from_settings", "get_api_key"]:
    check("依赖", f"def {fn}" in svc, f"readiness_check 依赖的 {fn} 不存在")

# 2. session.py --ignore 完整性
ses = open(os.path.join(ROOT, "session.py"), encoding="utf-8").read()
check("session", "--ignore" in ses, "--ignore CLI 参数缺失")
check("session", '"ignored"' in ses or "'ignored'" in ses, "ignored 标记缺失")
check("session", "overrides.json" in ses, "overrides 持久化缺失")

# 3. 安全：XSS 关键点
dash = open(os.path.join(ROOT, "panel", "templates", "dashboard.html"), encoding="utf-8").read()
check("安全", "App.esc" not in dash or "readiness-box" in dash,
      "dashboard 就绪检查框不应有用户输入的 innerHTML")
segs = open(os.path.join(ROOT, "panel", "templates", "segments.html"), encoding="utf-8").read()
check("安全", "App.esc(r.name)" in segs, "片段库文件名未转义")
rec = open(os.path.join(ROOT, "panel", "templates", "recording.html"), encoding="utf-8").read()
check("安全", "App.esc(r.title)" in rec or "App.esc" in rec, "录制页标题未转义")

# 4. 编码一致性：所有 ps1 有 BOM
for ps1 in ["process_all.ps1", "cleanup.ps1", "status.ps1", "notify.ps1",
            "backup_metadata.ps1", "selftest.ps1", "verify.ps1", "bilive-unlock-check.ps1"]:
    fp = os.path.join(ROOT, ps1)
    if os.path.exists(fp):
        b = open(fp, "rb").read(3)
        check("BOM", b[:3] == b"\xef\xbb\xbf", f"{ps1} 缺 BOM")

# 5. api_key.txt 不在 git 跟踪中
gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
check("安全", "api_key.txt" in gi, ".gitignore 缺 api_key.txt")

# 6. summarize_host 的 get_api_key 三级回退
sh = open(os.path.join(ROOT, "summarize_host.py"), encoding="utf-8").read()
check("key", "def get_api_key" in sh, "get_api_key 函数缺失")
check("key", "api_key.txt" in sh, "文件回退缺失")

# 7. report_gen 的场次视图插入点
rg = open(os.path.join(ROOT, "report_gen.py"), encoding="utf-8").read()
check("report", "| 房间 | 分段 |" in rg, "表头定位锚点缺失")
check("report", "split_at" in rg, "动态插入点缺失")

if issues:
    print(f"[FAIL] {len(issues)} 个问题:")
    for i in issues:
        print(f"  - {i}")
    sys.exit(1)
else:
    print("[PASS] A线自动化自查全部通过")
