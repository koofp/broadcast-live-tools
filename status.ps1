# bilive 最小监控 v2：红黄绿状态（含 fake-ip 劫持指纹检测 + 尺寸增长采样）
# 用法: .\status.ps1   （末尾输出一行 JSON 供面板/脚本复用）
$ErrorActionPreference = 'SilentlyContinue'
$Root = $PSScriptRoot   # 修复：原用 $MyInvocation.MyCommand.Path 手动取目录
$Videos = Join-Path $Root 'bilive-docker\Videos'
$CacheFile = Join-Path $Root '.status_cache.json'
$issues = @()
function W($c, $t) { Write-Host "[$c] $t" -ForegroundColor @{R='Red';Y='Yellow';G='Green'}[$c] }

# 1. 容器
$ctn = docker ps --filter name=bilive_docker --format '{{.Status}}'
if ($ctn -match '^Up') { W G "容器: $ctn" } else { W R "容器未运行: '$ctn'"; $issues += 'container' }

# 2. blrec 进程
$blrec = docker exec bilive_docker bash -c 'ps aux | grep [b]lrec | wc -l' 2>$null
if ([int]$blrec -ge 1) { W G 'blrec 进程: 运行中' } else { W R 'blrec 进程缺失'; $issues += 'blrec' }

# 3. fake-ip 劫持检测（黄牌=在运行但未影响；红牌=同时停摆）
$dnsIp = (Resolve-DnsName api.live.bilibili.com -Type A -ErrorAction SilentlyContinue |
          Where-Object IPAddress | Select-Object -First 1).IPAddress
$fakeip = ($dnsIp -match '^198\.18\.')
if ($fakeip) { W Y "Clash TUN 检测到($dnsIp)——当前未影响录制，但切全局模式会断，建议退出" }
elseif ($dnsIp) { W G "DNS 正常 ($dnsIp)" }
else { W Y 'DNS 解析失败'; $issues += 'dns' }

# 4. 最新分段尺寸增长采样（对比上次缓存）
$newest = Get-ChildItem "$Videos\*\*.flv","$Videos\*\*.mp4" -File -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
$prev = if (Test-Path $CacheFile) { Get-Content $CacheFile -Raw | ConvertFrom-Json } else { $null }
$stall = $false
if (-not $newest) { W R '没有任何录像文件'; $issues += 'nofiles' }
else {
    $ageMin = [int]((Get-Date) - $newest.LastWriteTime).TotalMinutes
    $sizeMB = [math]::Round($newest.Length/1MB)
    if ($ageMin -gt 45) { W R "最新文件 ${ageMin} 分钟未写入——疑似停摆!"; $issues += 'stall'; $stall = $true }
    elseif ($prev -and $prev.name -eq $newest.Name -and $prev.size -eq $newest.Length -and $ageMin -gt 10) {
        W Y "最新文件尺寸与上次检查相同——主播可能下播"; $issues += 'nogrow' }
    else { W G "最新分段: $($newest.Name) (${sizeMB}MB, ${ageMin}分钟前)" }
    @{name=$newest.Name; size=$newest.Length; checkAge=$ageMin; ts=(Get-Date -Format s)} |
        ConvertTo-Json | Set-Content $CacheFile -Encoding UTF8
}
if ($fakeip -and $stall) {
    W R '劫持+停摆同时出现——立即退出 Clash 并重启容器!'; $issues += 'fakeip'
    # 红牌落盘留痕（辩论定案：无人值守时仪表盘没人看，持久 alert 供下次排查/会话发现）
    Add-Content -LiteralPath (Join-Path $PSScriptRoot 'logs\pipeline\alert.log') `
        -Value ("{0}`tFAKEIP+STALL`t{1}" -f (Get-Date -Format s), $newest.Name) -Encoding UTF8
}
# 5. 磁盘
$freeGB = [math]::Round((Get-PSDrive D).Free/1GB, 1)
$days = [math]::Round($freeGB / 72, 1)
if ($days -gt 2) { W G "磁盘 ${freeGB}GB (可录≈${days}天)" }
elseif ($days -gt 1) { W Y "磁盘 ${freeGB}GB (仅${days}天!) 跑 cleanup.ps1"; $issues += 'disk' }
else { W R "磁盘 ${freeGB}GB (不足1天!) 立即归档!"; $issues += 'disk' }

# 6. 全房间积压（可靠枚举：mp4 + 孤儿flv；10分钟内活跃不计）
$rooms = 0; $pending = 0; $done = 0
Get-ChildItem $Videos -Directory | ForEach-Object {
    $rooms++
    $mp4Names = @{}
    Get-ChildItem $_.FullName -Filter *.mp4 -File -ErrorAction SilentlyContinue | ForEach-Object { $mp4Names[$_.BaseName] = $true }
    $cands = @(Get-ChildItem $_.FullName -Filter *.mp4 -File -ErrorAction SilentlyContinue)
    $cands += Get-ChildItem $_.FullName -Filter *.flv -File -ErrorAction SilentlyContinue |
              Where-Object { -not $mp4Names.ContainsKey($_.BaseName) }
    foreach ($c in $cands) {
        if (((Get-Date) - $c.LastWriteTime).TotalMinutes -lt 10) { continue }
        $base = $c.FullName.Substring(0, $c.FullName.Length - 4)
        $hasSrt = Test-Path "$base.srt"; $hasSum = Test-Path "$base.summary.md"
        if ($hasSum) { $done++ }
        elseif ($hasSrt) {
            if ((Get-Content "$base.srt" -Raw -ErrorAction SilentlyContinue) -match '\[无语音内容\]') { $done++ } else { $pending++ }
        } else { $pending++ }
    }
}
W Y "积压: 待处理 ${pending} 段（已完成 ${done}）→ process_all.ps1 / 面板流水线页"

Write-Host ''
if ($issues.Count -eq 0) { Write-Host '=== 全部正常 ===' -ForegroundColor Green } else { Write-Host ("=== 问题: " + ($issues -join ', ') + " ===") -ForegroundColor Red }
[pscustomobject]@{ ok=($issues.Count -eq 0); issues=$issues; container=$ctn; free_gb=$freeGB;
                   days=$days; rooms=$rooms; done=$done; backlog=$pending; clash=$fakeip;
                   newest_age_min=$ageMin } | ConvertTo-Json -Compress | Write-Output
