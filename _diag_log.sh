#!/bin/sh
LOG=$(ls -t /app/logs/record/*.log | head -1)
grep -inE 'error|exception|failed|warn|submit' "$LOG" | head -20
echo '=== last 15 lines ==='
tail -15 "$LOG"
