@echo off
rem ================================================================
rem  bilive panel one-click launcher (double-click me / 双击我即可)
rem  - panel already running -> just open browser
rem  - otherwise start it (hidden), wait until up, then open browser
rem ================================================================
setlocal
set "ROOT=D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools"
set "PY=D:\system\pyhone\pythonw.exe"
set "URL=http://127.0.0.1:9090/"

curl -s -o nul -m 1 %URL% >nul 2>nul && goto open

echo Starting bilive panel ...
start "" /D "%ROOT%" "%PY%" "%ROOT%\panel.py"
for /L %%i in (1,1,15) do (
  curl -s -o nul -m 1 %URL% >nul 2>nul && goto open
  ping -n 2 127.0.0.1 >nul
)
echo [X] Panel did not come up within 15s. Check: %ROOT%\logs\panel-stdout.log
pause
exit /b 1

:open
start "" %URL%
exit /b 0
