# -*- coding: utf-8 -*-
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
print("=== 1. API key check ===")
env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
print(f"env: {repr(env_key[:12]) if env_key else 'MISSING'}")
kf = ROOT / "api_key.txt"
file_key = kf.read_text(encoding="utf-8").strip() if kf.exists() else None
print(f"file: {repr(file_key[:12]) if file_key else 'MISSING'}")
from summarize_host import get_api_key
k = get_api_key()
print(f"get_api_key: {len(k)} chars" if k else "get_api_key: EMPTY")
print("=== 2. Today log ===")
log_dir = ROOT / "logs" / "pipeline"
logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if log_dir.exists() else []
if logs:
    for ln in logs[0].read_text(encoding="utf-8", errors="ignore").splitlines():
        if any(w in ln for w in ["summarize","FAIL","fatal","429","[warn]","1790093449","1937830735","14323359"]):
            print(ln)
print("=== 3. 1937830735 srt ===")
sdir = ROOT / "bilive-docker" / "Videos" / "1937830735"
srts = sorted(sdir.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True) if sdir.exists() else []
if srts:
    s = srts[0]
    c = s.read_text(encoding="utf-8", errors="ignore")
    print(f"size={s.stat().st_size}B placeholder={chr(0x5b)+chr(0x65e0)+chr(0x8bed)+chr(0x97f3)+chr(0x5185)+chr(0x5bb9)+chr(0x5d) in c}")
