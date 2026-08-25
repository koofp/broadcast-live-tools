#!/bin/sh
LOG=$(ls -t /app/logs/record/*.log | head -1)
grep -E '(14323359|71003|1937830735).*(danmu|auth)' "$LOG" | tail -14
