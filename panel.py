# -*- coding: utf-8 -*-
"""bilive 面板薄入口（计划任务指向此文件，实际应用在 panel/ 包）"""
from panel.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9090, log_level="warning")