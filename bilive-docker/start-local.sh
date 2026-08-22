#!/bin/bash
# 本地存档模式：只录制（blrec），不跑 scan（会删原始文件）/upload（不投稿）
export config=./settings.toml
export no_proxy=*

mkdir -p logs/record logs/runtime logs/scan logs/upload

kill -9 $(ps aux | grep '[b]lrec' | awk '{print $2}') 2>/dev/null
nohup blrec -c $config --open --host 0.0.0.0 --port 2233 --api-key "$RECORD_KEY" > ./logs/record/blrec-local.log 2>&1 &
echo "[local-mode] blrec started; scan/upload DISABLED (raw files preserved)"

tail -f /dev/null
