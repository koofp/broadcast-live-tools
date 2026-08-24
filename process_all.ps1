# bilive 批量处理编排：转写 + AI 总结（幂等 + 崩溃安全锁 + 低优先级）
# 用法:
#   process_all.ps1                # 处理所有房间全部待处理分段
#   process_all.ps1 -One <mp4/flv> # 只处理单个文件（面板用）
#   process_all.ps1 -Force         # 强抢超时锁
param([string]$One = "", [switch]$Force)
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = '1'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}   # 防 GBK 控制台吃掉 Python UTF-8 输出（乱码曾破坏面板进度正则）
try { (Get-Process -Id $PID).PriorityClass = 'BelowNormal' } catch {}
$Videos = Join-Path $PSScriptRoot 'bilive-docker\Videos'
$LockFile = Join-Path $PSScriptRoot 'run.lock'
$LogDir = Join-Path $PSScriptRoot 'logs\pipeline'
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir ("{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))

function Log($m) {
    $line = "{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
    Write-Host $line
    Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
}
# 日志轮转：删14天前
Get-ChildItem $LogDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } | Remove-Item -Force

# 崩溃安全锁：FileStream 独占；锁龄>2h 视为死锁强抢（除非 -Force 已表态）
try {
    $script:lockStream = [IO.File]::Open($LockFile, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    $ageMin = if (Test-Path $LockFile) { [int]((Get-Date) - (Get-Item $LockFile).LastWriteTime).TotalMinutes } else { -1 }
    if ($Force -or $ageMin -gt 120) { Log "强抢死锁锁(锁龄${ageMin}分钟)"; try { Remove-Item $LockFile -Force } catch {}; $script:lockStream = [IO.File]::Open($LockFile, 'OpenOrCreate', 'ReadWrite', 'None') }
    else { Write-Host "[锁] 其他处理进程运行中(锁龄${ageMin}分钟)，退出。加 -Force 可强抢"; exit 3 }
}
try {
    $_lb = [Text.Encoding]::UTF8.GetBytes("PID=$PID time=$(Get-Date -Format s)")
    $script:lockStream.Write($_lb, 0, $_lb.Length)   # 三参写法兼容 WinPS5.1/pwsh7
    $script:lockStream.Flush()
} catch { Write-Host "[warn] 锁戳写入失败(不影响锁持有): $_" }

function Unlock { $script:lockStream.Close(); Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }

# 候选集：各房间的 mp4 + 无同名 mp4 的孤儿 flv；跳过 10 分钟内仍写入的文件
function Get-Candidates {
    $list = @()
    Get-ChildItem $Videos -Directory | ForEach-Object {
        $room = $_.FullName
        $mp4Names = @{}
        Get-ChildItem $room -Filter *.mp4 -File -ErrorAction SilentlyContinue | ForEach-Object { $mp4Names[$_.BaseName] = $true }
        Get-ChildItem $room -Include *.mp4,*.flv -File -Recurse -Depth 0 -ErrorAction SilentlyContinue | ForEach-Object {
            $isFlvOrphan = ($_.Extension -eq '.flv') -and (-not $mp4Names.ContainsKey($_.BaseName))
            if ($_.Extension -eq '.mp4' -or $isFlvOrphan) {
                if (((Get-Date) - $_.LastWriteTime).TotalMinutes -gt 10) { $list += $_.FullName }
            }
        }
    }
    return $list | Sort-Object
}

try {
    if ($One) {
        $targets = @(Get-Item -LiteralPath $One -ErrorAction Stop | Select-Object -ExpandProperty FullName)
    } else {
        $targets = Get-Candidates
    }
    if (-not $targets -or $targets.Count -eq 0) { Log '没有待处理文件'; exit 0 }
    Log "开始批量处理: $($targets.Count) 个文件"

    $failList = @()
    $toTranscribe = @(); $toSummarize = @()
    foreach ($v in $targets) {
        $name = Split-Path $v -Leaf
        # 锁心跳：持续刷新 mtime，防止长批次(>2h)被下一轮计划任务误判死锁强抢
        try {
            $script:lockStream.SetLength(0)
            $hb = [Text.Encoding]::UTF8.GetBytes("PID=$PID time=$(Get-Date -Format s)")
            $script:lockStream.Write($hb, 0, $hb.Length)
            $script:lockStream.Flush()
        } catch {}
        $srt = [IO.Path]::ChangeExtension($v, '.srt')
        $sum = [IO.Path]::ChangeExtension($v, '.summary.md')
        if ((Test-Path $sum) -and ((Get-Item $sum).Length -gt 0)) { Log "[skip] $name 全流程已完成"; continue }

        if (-not (Test-Path $srt) -or (Get-Item $srt).Length -eq 0) {
            Log "[plan] 待转写 $name"
            $toTranscribe += $v
            continue
        }
        # -Encoding UTF8 必须：WinPS5.1 默认按 GBK 读无 BOM 文件，占位符曾永远匹配不上（实测踩坑）
        if ((Get-Content $srt -Raw -Encoding UTF8 -ErrorAction SilentlyContinue) -match '\[无语音内容\]') {
            Log "[skip] $name 占位srt(无语音)，跳过总结"
            [IO.File]::WriteAllText($sum, "（该分段无语音内容，未生成总结）", (New-Object Text.UTF8Encoding($false)))
            continue
        }
        Log "[plan] 待总结 $name"
        $toSummarize += $srt
    }

    # 批量转写：单进程一次加载 Whisper 权重（审计采纳——逐段调用曾每段重载 ~15s）
    if ($toTranscribe.Count -gt 0) {
        Log ("[1/2] 批量转写 {0} 段（模型仅加载一次）" -f $toTranscribe.Count)
        python transcribe_host.py $toTranscribe --model (Join-Path $PSScriptRoot 'models\faster-whisper-small') 2>&1 |
            ForEach-Object { Log "  $_" }
        if ($LASTEXITCODE -ne 0) { Log "[warn] transcribe_host exit=$LASTEXITCODE" }
        # 转写产出复核：新 srt 归入总结队列；仍无产出的记失败
        foreach ($v in $toTranscribe) {
            $name = Split-Path $v -Leaf
            $srt = [IO.Path]::ChangeExtension($v, '.srt')
            if ((Test-Path $srt) -and ((Get-Item $srt).Length -gt 0)) {
                $sum = [IO.Path]::ChangeExtension($v, '.summary.md')
                if (Test-Path $sum) { continue }
                if ((Get-Content $srt -Raw -Encoding UTF8 -ErrorAction SilentlyContinue) -match '\[无语音内容\]') {
                    [IO.File]::WriteAllText($sum, "（该分段无语音内容，未生成总结）", (New-Object Text.UTF8Encoding($false)))
                    Log "[skip] $name 占位srt(无语音)，跳过总结"
                    continue
                }
                $toSummarize += $srt
            } else { Log "[fail] 转写无产出 $name"; $failList += $srt }
        }
    }

    # 批量总结：同进程顺序处理全部待总结段（含按房间自动选择的提示词）
    if ($toSummarize.Count -gt 0) {
        Log ("[2/2] 批量总结 {0} 段" -f $toSummarize.Count)
        python summarize_host.py $toSummarize 2>&1 | ForEach-Object { Log "  $_" }
        if ($LASTEXITCODE -ne 0) { Log "[warn] summarize_host exit=$LASTEXITCODE" }
        foreach ($s in $toSummarize) {
            if (-not (Test-Path ([IO.Path]::ChangeExtension($s, '.summary.md')))) { $failList += $s }
        }
    }
    # retry.txt 对账：已产出 summary 的条目剔除 + 去重；清零则删除文件
    $retryFile = Join-Path $PSScriptRoot 'retry.txt'
    if (Test-Path $retryFile) {
        $kept = @()
        Get-Content $retryFile -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_ -match '^\s*$') { return }
            $sumPath = [IO.Path]::ChangeExtension(($_ -split "`t")[0], '.summary.md')
            if (-not (Test-Path $sumPath)) { $kept += $_ }
        }
        $kept = @($kept | Select-Object -Unique)
        if ($kept.Count -gt 0) { Set-Content -LiteralPath $retryFile -Value $kept -Encoding UTF8 }
        else { Remove-Item $retryFile -Force -ErrorAction SilentlyContinue }
        Log ("[retry] 对账完成，保留 {0} 条待重试" -f $kept.Count)
    }
    if ($failList) {
        Log ("失败清单(下轮自动重试): " + ($failList -join '; '))
        try { & (Join-Path $PSScriptRoot 'notify.ps1') -Title '处理失败' -Text ("{0} 段处理失败，下轮自动重试（详见流水线日志）" -f $failList.Count) -Level warn } catch {}
    }
    Log "批量处理结束"
} finally {
    Unlock
}
