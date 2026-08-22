# 录像归档清理：只删"已转写+已总结且超48小时"的最旧分段，未转写的永不碰
# 用法:
#   .\cleanup.ps1              # 预览模式：列出可安全删除的分段
#   .\cleanup.ps1 -Apply       # 实际删除（与 process_all 共用同一把锁）
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Videos = Join-Path $PSScriptRoot 'bilive-docker\Videos'
$LockFile = Join-Path $PSScriptRoot 'run.lock'
$DelLog = Join-Path $PSScriptRoot 'logs\pipeline\deleted.log'

# 与 process_all 共用崩溃安全锁
try { $lockStream = [IO.File]::Open($LockFile, 'OpenOrCreate', 'ReadWrite', 'None') }
catch { Write-Host '[锁] 处理进程运行中，稍后再试'; exit 3 }
try {
    $freeGB = [math]::Round((Get-PSDrive D).Free/1GB, 1)
    Write-Host "当前 D 盘剩余: ${freeGB}GB (清理阈值 150GB → 删至 200GB)"
    if ($freeGB -ge 150) { Write-Host '磁盘充足，无需清理。'; exit 0 }

    $candidates = @()
    Get-ChildItem $Videos -Directory | ForEach-Object {
        Get-ChildItem $_.FullName -Filter *.mp4 -File -ErrorAction SilentlyContinue | ForEach-Object {
            $base = [IO.Path]::ChangeExtension($_.FullName, '')
            $srt = "${base}.srt"; $sum = "${base}.summary.md"
            $cond = (Test-Path $srt) -and ((Get-Item $srt).Length -gt 1KB) `
                    -and (Test-Path $sum) `
                    -and ((Get-Date) - $_.LastWriteTime).TotalHours -gt 48 `
                    -and ((Get-Date) - $_.LastWriteTime).TotalMinutes -gt 15
            if ($cond) { $candidates += $_ }
        }
    }
    $candidates = $candidates | Sort-Object LastWriteTime
    if (-not $candidates) { Write-Host '没有满足四条件的可删分段。'; exit 0 }

    $target = 200; $freed = 0
    Write-Output "=== 将删除以下分段(最旧优先, 删至${target}GB) ==="
    foreach ($c in $candidates) {
        if ($freeGB + $freed -ge $target) { break }
        $stem = $c.Name -replace '\.mp4$', ''
        $peers = Get-ChildItem (Split-Path $c.FullName) -File | Where-Object { $_.Name -like "$stem*" }
        $gb = [math]::Round(($peers | Measure-Object Length -Sum).Sum/1GB, 2)
        Write-Host ("  {0}  ({1}GB, {2}个文件)" -f $c.Name, $gb, $peers.Count)
        if ($Apply) {
            foreach ($p in $peers) {
                Remove-Item -LiteralPath $p.FullName -Force
                Add-Content -LiteralPath $DelLog -Value "$(Get-Date -Format s)`tDELETED`t$($p.FullName)`t$($p.Length)" -Encoding UTF8
            }
            Write-Host '    已删除' -ForegroundColor Yellow
        }
        $freed += $gb
    }
    if ($Apply) { Write-Host ("完成，释放约 {0}GB" -f [math]::Round($freed,1)) }
    else { Write-Host "`n预览模式。确认后加 -Apply 执行。" -ForegroundColor Cyan }
} finally { $lockStream.Close() }