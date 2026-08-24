# 录像归档清理 v2 —— 安全闸门 + 垃圾桶暂存 + keep 白名单 + 锁重试
# 用法:
#   .\cleanup.ps1                # 预览模式（默认）
#   .\cleanup.ps1 -Apply         # 执行删除（经 _trash 暂存）
# 策略: D盘 <150GB 触发；仅删「已转写(srt>1KB非占位)+已总结+超48h+非写入中」且不在 keep.txt 的最旧分段，删至 200GB
# 安全: 与 process_all 共用 run.lock（重试×4/15分钟，锁龄>120min强抢）；候选先移入 _trash\，下次运行清除超7天 trash
param([switch]$Apply, [switch]$Force)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Videos = Join-Path $PSScriptRoot 'bilive-docker\Videos'
$LockFile = Join-Path $PSScriptRoot 'run.lock'
$TrashDir = Join-Path $Videos '_trash'
$KeepFile = Join-Path $PSScriptRoot 'keep.txt'
$DelLog = Join-Path $PSScriptRoot 'logs\pipeline\deleted.log'
New-Item -ItemType Directory -Force $TrashDir, (Split-Path $DelLog) | Out-Null

function Log($m) {
    $line = "{0} {1}" -f (Get-Date -Format 'MM-dd HH:mm:ss'), $m
    Write-Host $line
    Add-Content -LiteralPath $DelLog -Value $line -Encoding UTF8
}

# ---- 锁：重试×4(间隔15s)；锁龄>120min 强抢；仍败写 fail 日志并退出 3 ----
$got = $false; $lockStream = $null
for ($i = 1; $i -le 4; $i++) {
    try { $lockStream = [IO.File]::Open($LockFile, 'OpenOrCreate', 'ReadWrite', 'None'); $got = $true; break }
    catch {
        $ageMin = if (Test-Path $LockFile) { [int]((Get-Date) - (Get-Item $LockFile).LastWriteTime).TotalMinutes } else { -1 }
        if ($ageMin -gt 120 -or $Force) {
            try { Remove-Item $LockFile -Force } catch {}
            continue
        }
        Write-Host "[锁] 处理进程运行中(锁龄${ageMin}分)，15分钟后重试($i/4)…"
        Start-Sleep -Seconds 900
    }
}
if (-not $got) {
    $msg = "cleanup 无法获取锁，本轮放弃（连续4次）"
    Write-Host "[FAIL] $msg"
    Add-Content -LiteralPath (Join-Path $PSScriptRoot 'logs\pipeline\cleanup_fail.log') `
        -Value "$(Get-Date -Format s)`t$msg" -Encoding UTF8
    exit 3
}
# 锁戳（评审[高]：原 cleanup 拿锁后不写内容，长跑>2h 会被 process_all 误判死锁强抢→双进程）
$lb = [Text.Encoding]::UTF8.GetBytes("PID=$PID time=$(Get-Date -Format s) role=cleanup")
$lockStream.Write($lb, 0, $lb.Length)
$lockStream.Flush()

try {
    $ErrorActionPreference = 'Stop'   # 主流程收紧
    $freeGB = [math]::Round((Get-PSDrive D).Free/1GB, 1)
    Write-Host "D 盘剩余 ${freeGB}GB（阈值150 → 删至200）"
    if ($freeGB -ge 150) { Write-Host '磁盘充足，无需清理。'; exit 0 }

    # keep.txt 白名单（通配模式，#注释）
    $keepPatterns = @()
    if (Test-Path $KeepFile) {
        $keepPatterns = Get-Content $KeepFile -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') }
    }

    # 清理上轮 trash（>7天）
    Get-ChildItem $TrashDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    # 候选：四条件 AND + keep 白名单排除
    $candidates = @()
    Get-ChildItem $Videos -Directory | Where-Object { $_.Name -ne '_trash' } | ForEach-Object {
        Get-ChildItem $_.FullName -Filter *.mp4 -File -ErrorAction SilentlyContinue | ForEach-Object {
            $base = $_.FullName.Substring(0, $_.FullName.Length - 4)
            $srt = "$base.srt"; $sum = "$base.summary.md"
            # v3(辩论定案)：占位段(无语音)写入 summary 后即视为已处理，放行清理，
            # 否则其 mp4 永久滞留=无人值守下的磁盘静默泄漏。
            # 护栏：真实段仍要求 srt>1KB；所有候选 mp4>=1MB 防损坏文件误入；
            #       仍保留超48h+垃圾桶7天回滚+keep白名单保护。
            $srtOk = (Test-Path $srt) -and ((Get-Item $srt).Length -gt 1KB)
            $isPlaceholder = $false
            if (Test-Path $srt) {
                # -Encoding UTF8：WinPS 默认 GBK 读无 BOM 文件，占位符检测曾永远为 False（实测踩坑）
                $isPlaceholder = ((Get-Content $srt -Raw -Encoding UTF8 -ErrorAction SilentlyContinue) -match '\[无语音内容\]')
            }
            $processed = (Test-Path $sum) -and ($srtOk -or $isPlaceholder)
            $cond = $processed -and ($_.Length -ge 1MB) -and `
                    ((Get-Date) - $_.LastWriteTime).TotalHours -gt 48 -and `
                    ((Get-Date) - $_.LastWriteTime).TotalMinutes -gt 15
            if ($cond) { $candidates += $_ }
        }
    }
    if ($keepPatterns) {
        $candidates = $candidates | Where-Object {
            $n = $_.Name; -not ($keepPatterns | Where-Object { $n -like $_ })
        }
    }
    $candidates = $candidates | Sort-Object LastWriteTime
    if (-not $candidates) { Write-Host '没有满足条件的可删分段。'; exit 0 }

    # 执行：移入 _trash（7天回滚窗口），单次限量 20 段
    $target = 200; $freedGB = 0; $movedCount = 0; $maxSegmentsPerRun = 20
    Write-Output "=== 将清理以下分段 ==="
    foreach ($c in $candidates) {
        if ($freeGB + $freedGB -ge $target) { break }
        if ($movedCount -ge $maxSegmentsPerRun) { Write-Host '（单次限量已达，下轮继续）'; break }
        $stem = $c.Name -replace '\.mp4$', ''
        $peers = Get-ChildItem (Split-Path $c.FullName) -File | Where-Object { $_.BaseName -eq $stem -or $_.Name -like "$stem.*" }
        $gb = [math]::Round(($peers | Measure-Object Length -Sum).Sum/1GB, 2)
        Write-Host ("  {0} ({1}GB, {2}文件)" -f $c.Name, $gb, $peers.Count)
        if ($Apply) {
            $tsub = Join-Path $TrashDir (Get-Date -Format 'yyyyMMdd_HHmmss')
            New-Item -ItemType Directory -Force $tsub | Out-Null
            foreach ($p in $peers) {
                Move-Item -LiteralPath $p.FullName -Destination (Join-Path $tsub $p.Name) -Force
                Add-Content -LiteralPath $DelLog -Value "$(Get-Date -Format s)`tDELETED`t$($p.FullName)`t$($p.Length)" -Encoding UTF8
            }
            $movedCount++
        }
        $freedGB += $gb
    }
    if (-not $Apply) {
        Write-Host "`n预览模式。确认后加 -Apply 执行。"
    } else {
        Write-Host ("完成：清理 {0} 段，释放约 {1}GB" -f $movedCount, [math]::Round($freedGB,1))
        try { & (Join-Path $PSScriptRoot 'notify.ps1') -Title '归档清理已执行' -Text ("清理 {0} 段，释放约 {1}GB（_trash 保留 7 天可回滚）" -f $movedCount, [math]::Round($freedGB,1)) -Level info } catch {}
        $after = [math]::Round((Get-PSDrive D).Free/1GB, 1)
        if ($after -lt 160) {
            Write-Host "[警告] 清理后仍仅 ${after}GB——候选耗尽但未达标！考虑外迁或降低录制画质。" -ForegroundColor Yellow
            Add-Content -LiteralPath $DelLog -Value "$(Get-Date -Format s)`tWARN`t清理后仍不足160GB" -Encoding UTF8
            try { & (Join-Path $PSScriptRoot 'notify.ps1') -Title '空间仍不足' -Text "清理后仅 ${after}GB：候选耗尽，考虑外迁或降低画质" -Level warn } catch {}
        }
    }
} finally {
    if ($lockStream) {
        $lockStream.Close()
        Remove-Item $LockFile -Force -ErrorAction SilentlyContinue   # 与 process_all 的 Unlock 行为对齐
    }
}