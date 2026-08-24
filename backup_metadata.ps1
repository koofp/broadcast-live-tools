# bilive 元数据备份：字幕/总结/场次/配置 → 异盘累积备份（robocopy 增量，永不删历史）
# 计划任务 bilive-backup 每日 10:00；可随时手动运行。目标默认 C 盘用户目录（与 D 盘数据盘物理隔离）。
# 说明：robocopy 不带 /PURGE——源端被 cleanup 清理的分段，其 srt/summary 在备份端永久保留（历史资产）。
param([string]$Dest = "$env:USERPROFILE\bilive_backup")
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$Log = Join-Path $PSScriptRoot 'logs\backup.log'
New-Item -ItemType Directory -Force (Split-Path $Log), $Dest | Out-Null
function Log($m) { Add-Content -LiteralPath $Log -Value ("{0} {1}" -f (Get-Date -Format s), $m) -Encoding UTF8; Write-Host $m }

Log "=== 元数据备份开始 → $Dest"

# 1) Videos 下全部字幕/总结/场次（含 _sessions 与 _trash，历史不丢）
robocopy 'bilive-docker\Videos' (Join-Path $Dest 'Videos') '*.srt' '*.summary.md' /S /NFL /NDL /NP | Out-Null
$rcVideos = $LASTEXITCODE
if ($rcVideos -ge 8) { Log "[FAIL] Videos robocopy exit=$rcVideos" }
else { Log ("[ok] Videos 元数据同步完成 (robocopy={0})" -f $rcVideos) }

# 2) 配置与提示词（小文件直接拷贝）
$copied = 0
foreach ($f in @('bilive-docker\settings.toml', 'prompt.txt', 'keep.txt', 'panel\queue.json')) {
    if (Test-Path $f) {
        $dstDir = Join-Path $Dest (Split-Path $f)
        New-Item -ItemType Directory -Force $dstDir | Out-Null
        Copy-Item -LiteralPath $f -Destination (Join-Path $dstDir (Split-Path $f -Leaf)) -Force
        $copied++
    }
}
Get-ChildItem -File -Filter 'prompt*.txt' | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Dest $_.Name) -Force
    $copied++
}
Log "[ok] 配置/提示词拷贝 $copied 项"

# 3) 汇总
if ($rcVideos -ge 8) {
    Log "[FAIL] 备份存在失败项"
    try { & (Join-Path $PSScriptRoot 'notify.ps1') -Title '元数据备份失败' -Text "robocopy exit=$rcVideos，详见 logs\backup.log" -Level bad } catch {}
    exit 1
}
Log "=== 备份完成"
exit 0
