# bilive 最小监控 v2：红黄绿状态（含 fake-ip 劫持指纹检测 + 尺寸增长采样）
# 用法: .\status.ps1   （末尾输出一行 JSON 供面板/脚本复用）
$ErrorActionPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
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

# 3. fake-ip 劫持指纹（Clash TUN 开启的铁证，比进程名可靠）
$dnsIp = (Resolve-DnsName api.live.bilibili.com -Type A -ErrorAction SilentlyContinue |
          Where-Object IPAddress | Select-Object -First 1).IPAddress
if ($dnsIp -match '^198\.18\.') { W R "DNS 被劫持($dnsIp ∈ fake-ip网段)——Clash TUN 在运行! 录制必失败,请退出 Clash"; $issues += 'fakeip' }
elseif ($dnsIp) { W G "DNS 正常 ($dnsIp)" }
else { W Y 'DNS 解析失败(网络异常?)'; $issues += 'dns' }

# 4. 最新分段尺寸增长采样（对比上次缓存）
$newest = Get-ChildItem "$Videos\*\*.flv","$Videos\*\*.mp4" -File -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
$prev = if (Test-Path $CacheFile) { Get-Content $CacheFile -Raw | ConvertFrom-Json } else { $null }
if (-not $newest) { W R '没有任何录像文件'; $issues += 'nofiles' }
else {
    $ageMin = [int]((Get-Date) - $newest.LastWriteTime).TotalMinutes
    $sizeMB = [math]::Round($newest.Length/1MB)
    if ($ageMin -gt 45) { W R "最新文件 ${ageMin} 分钟未写入——疑似停摆!"; $issues += 'stall' }
    elseif ($prev -and $prev.name -eq $newest.Name -and $prev.size -eq $newest.Length -and $ageMin -gt 10) {
        W Y "最新文件尺寸与上次检查相同——主播可能下播"; $issues += 'nogrow' }
    else { W G "最新分段: $($newest.Name) (${sizeMB}MB, ${ageMin}分钟前)" }
    @{name=$newest.Name; size=$newest.Length; checkAge=$ageMin; ts=(Get-Date -Format s)} |
        ConvertTo-Json | Set-Content $CacheFile -Encoding UTF8
}

# 5. 磁盘
$freeGB = [math]::Round((Get-PSDrive D).Free/1GB, 1)
$days = [math]::Round($freeGB / 72, 1)
if ($days -gt 2) { W G "磁盘 ${freeGB}GB (可录≈${days}天)" }
elseif ($days -gt 1) { W Y "磁盘 ${freeGB}GB (仅${days}天!) 跑 cleanup.ps1"; $issues += 'disk' }
else { W R "磁盘 ${freeGB}GB (不足1天!) 立即归档!"; $issues += 'disk' }

# 6. 全房间积压
$rooms = 0; $mp4Total = 0; $srtTotal = 0; $orphanFlv = 0
Get-ChildItem $Videos -Directory | ForEach-Object {
    $rooms++
    $mp4Names = @{}
    Get-ChildItem $_.FullName -Filter *.mp4 -File -ErrorAction SilentlyContinue | ForEach-Object { $mp4Total++; $mp4Names[$_.BaseName]=$true }
    Get-ChildItem $_.FullName -Filter *.srt -File -ErrorAction SilentlyContinue | ForEach-Object { $srtTotal++ }
    Get-ChildItem $_.FullName -Filter *.flv -File -ErrorAction SilentlyContinue | Where-Object { -not $mp4Names.ContainsKey($_.BaseName) } | ForEach-Object { $orphanFlv++ }
}
W Y "积压: ${mp4Total}段已转写${srtTotal} (待处理 $($mp4Total-$srtTotal), 含孤儿flv ${orphanFlv}) → process_all.ps1"

Write-Host ''
if ($issues.Count -eq 0) { Write-Host '=== 全部正常 ===' -ForegroundColor Green } else { Write-Host ("=== 问题: " + ($issues -join ', ') + " ===") -ForegroundColor Red }
[pscustomobject]@{ ok=($issues.Count -eq 0); issues=$issues; container=$ctn; free_gb=$freeGB;
                   days=$days; rooms=$rooms; segments=$mp4Total; transcribed=$srtTotal;
                   backlog=($mp4Total-$srtTotal); orphan_flv=$orphanFlv; newest_age_min=$ageMin } |
    ConvertTo-Json -Compress | Write-Output