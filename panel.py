# -*- coding: utf-8 -*-
"""bilive 面板薄入口（计划任务指向此文件，实际应用在 panel/ 包）

v2（2026-08-23）：启动前先探测 9090 端口——已有实例在跑则直接 exit(0)。
修复背景：计划任务 bilive-panel 用 pythonw 启动，若端口已被占（重复触发/
上次实例未退），uvicorn 绑定失败以退出码 1 收场，任务历史里就留下
LastTaskResult=1 的"假故障"。现在双实例竞争无害化。
"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # WorkDir 兜底


def _port_busy(host: str = "127.0.0.1", port: int = 9090) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


if __name__ == "__main__":
    if _port_busy():
        print("[panel] 9090 已有实例在运行，本次启动直接退出(0)", flush=True)
        sys.exit(0)
    from panel.main import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9090, log_level="warning")
