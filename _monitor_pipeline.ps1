# 触发流水线加工并监控 1790093449 测试段的转写+总结
$ErrorActionPreference = 'Continue'
Set-Location 'D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools'

Write-Host '=== 触发流水线 ==='
$r = Invoke-RestMethod -Uri 'http://127.0.0.1:9090/api/process' -Method Post -ContentType 'application/json' -Body '{"name":"__all__"}' -TimeoutSec 30
Write-Host ($r | ConvertTo-Json -Compress)

Write-Host '=== 监控加工进度 ==='
$target = '1790093449'
$srtDone = $false; $sumDone = $false
foreach ($i in 1..60) {
    Start-Sleep 15
    $log = Get-Content '.\logs\pipeline\2026-08-25.log' -Encoding UTF8 -ErrorAction SilentlyContinue |
        Where-Object { $_ -match $target } | Select-Object -Last 3
    if ($log) {
        Write-Host "--- 第 $($i*15) 秒 ---"
        $log | ForEach-Object { Write-Host "  $_" }
    }
    $srtP = ".\bilive-docker\Videos\1790093449\${target}-19-23-13.srt"
    $sumP = ".\bilive-docker\Videos\1790093449\${target}-19-23-13.summary.md"
    # 文件名实际是 1790093449_20260825-19-23-13
    $srtP = '.\bilive-docker\Videos\1790093449\1790093449_20260825-19-23-13.srt'
    $sumP = '.\bilive-docker\Videos\1790093449\1790093449_20260825-19-23-13.summary.md'
    $srtDone = (Test-Path $srtP) -and ((Get-Item $srtP -ErrorAction SilentlyContinue).Length -gt 0)
    $sumDone = (Test-Path $sumP) -and ((Get-Item $sumP -ErrorAction SilentlyContinue).Length -gt 0)
    if ($sumDone) {
        Write-Host '=== 产物齐备：srt + summary ==='
        break
    }
}
Write-Host '=== 最终产物 ==='
Get-ChildItem '.\bilive-docker\Videos\1790093449' -File |
    Select-Object Name, @{n='KB';e={[math]::Round($_.Length/1KB,1)}} | Format-Table -AutoSize
