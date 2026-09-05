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

# 2.5 录制状态探测（blrec 本地 API，零外网依赖——红牌恰在网络被劫坏时触发，此时外网最不可信）
# 三态：recording（任一任务 recording）/ idle（确认全部未在录）/ unknown（API 不可达，含容器挂）
# 陷阱：PS5.1 必须 -TimeoutSec 显式（默认 0=无限会拖死面板 20s 刷新线程）、@() 包裹防单元素解标量
$recState = 'unknown'
try {
    $tasks = @(Invoke-RestMethod 'http://127.0.0.1:22333/api/v1/tasks/data' `
               -Headers @{'X-API-KEY'='Bil1veLocal2026'} -TimeoutSec 4 -ErrorAction Stop)
    $recCount = @($tasks | Where-Object { $_.task_status.running_status -eq 'recording' }).Count
    $recState = if ($recCount -ge 1) { 'recording' } else { 'idle' }
} catch { $recState = 'unknown' }

# 3. fake-ip 劫持检测（劫持在位=黄牌常态，README 定案"不影响录制"；红牌=劫持+在录+停摆的实锤故障签名）
$dnsIp = (Resolve-DnsName api.live.bilibili.com -Type A -ErrorAction SilentlyContinue |
          Where-Object IPAddress | Select-Object -First 1).IPAddress
$fakeip = ($dnsIp -match '^198\.18\.')
if ($fakeip -and $recState -eq 'recording') { W Y "Clash TUN 检测到($dnsIp)——录制中，观察文件增长（停摆将红牌）" }
elseif ($fakeip) { W Y "Clash TUN 检测到($dnsIp)——当前未影响录制（未在录），勿切全局模式" }
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
    if ($ageMin -gt 45) {
        # 停摆语义分态（2026-09-05：原"一律红牌"在无直播时是常态误报）
        if ($recState -eq 'recording') { W R "最新文件 ${ageMin} 分钟未写入——录制中却无写入，疑似停摆!"; $issues += 'stall'; $stall = $true }
        elseif ($recState -eq 'idle') { W Y "最新文件 ${ageMin} 分钟未写入——未在录，属下播待机" }
        else { W Y "最新文件 ${ageMin} 分钟未写入——blrec 状态不可知，疑似下播" }
    }
    elseif ($prev -and $prev.name -eq $newest.Name -and $prev.size -eq $newest.Length -and $ageMin -gt 10) {
        W Y "最新文件尺寸与上次检查相同——主播可能下播"; $issues += 'nogrow' }
    else { W G "最新分段: $($newest.Name) (${sizeMB}MB, ${ageMin}分钟前)" }
    @{name=$newest.Name; size=$newest.Length; checkAge=$ageMin; ts=(Get-Date -Format s)} |
        ConvertTo-Json | Set-Content $CacheFile -Encoding UTF8
}
# 红牌=劫持+确认在录+写入停滞 三者同现（2026-09-05 定案：今天实锤的故障签名；
# fake-ip 单独/在录无停滞=黄牌常态，README 定案"不影响录制"）
if ($fakeip -and $recState -eq 'recording' -and $stall) {
    W R '劫持+录制中+停摆同时出现——立即退出 Clash 并重启容器!'; $issues += 'fakeip'
    # 红牌落盘留痕（辩论定案：无人值守时仪表盘没人看，持久 alert 供下次排查/会话发现）
    # 30 分钟节流：面板每次刷新都会跑 status.ps1，不节流会刷屏
    $alertFile = Join-Path $PSScriptRoot 'logs\pipeline\alert.log'
    $last = Get-Content $alertFile -Tail 1 -ErrorAction SilentlyContinue
    $lastTs = $null
    if ($last) { try { $lastTs = [datetime]::ParseExact((($last -split "`t")[0]), 'yyyy-MM-ddTHH:mm:ss', $null) } catch {} }
    if (-not $lastTs -or ((Get-Date) - $lastTs).TotalMinutes -ge 30) {
        Add-Content -LiteralPath $alertFile `
            -Value ("{0}`tFAKEIP+STALL`t{1}" -f (Get-Date -Format s), $newest.Name) -Encoding UTF8
        try { & (Join-Path $PSScriptRoot 'notify.ps1') -Title '录制风险' -Text 'Clash 劫持+录制停摆同时出现：立即退出 Clash 并重启容器' -Level bad } catch {}
    }
}
# 5. 磁盘
$freeGB = [math]::Round((Get-PSDrive D).Free/1GB, 1)
$days = [math]::Round($freeGB / 72, 1)
if ($days -gt 2) { W G "磁盘 ${freeGB}GB (可录≈${days}天)" }
elseif ($days -gt 1) { W Y "磁盘 ${freeGB}GB (仅${days}天!) 跑 cleanup.ps1"; $issues += 'disk' }
else { W R "磁盘 ${freeGB}GB (不足1天!) 立即归档!"; $issues += 'disk'
    # Text 用固定模板：动态数值会让节流键失效导致反复弹窗（对抗评审[高]）；精确值见状态输出与缓存
    try { & (Join-Path $PSScriptRoot 'notify.ps1') -Title '磁盘告急' -Text 'D 盘可用容量不足 1 天，立即归档清理' -Level bad } catch {} }

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
                   fakeip_state=$recState; newest_age_min=$ageMin } | ConvertTo-Json -Compress | Write-Output
