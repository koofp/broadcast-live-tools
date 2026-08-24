# bilive 端到端自测：合成静音视频驱动「候选扫描→批量转写→占位分支→场次聚类→报告」全链路
# 用法: .\selftest.ps1 [-Keep]   （-Keep 保留测试目录供人工检查；默认跑完自动清理）
# 不消耗 API（静音→VAD 占位分支）；真实总结路径测试见 runbook §5.98 步骤5
# 加固（评审）：入口预清+finally 兜底清理 / 步骤1失败即止 / process_all 退出码与锁冲突识别 /
#              summary 内容断言（非空+含占位说明）/ report_gen 退出码校验
param([switch]$Keep)
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$fail = @()
$room = '_selftest'
$rdir = Join-Path $PSScriptRoot 'bilive-docker\Videos\_selftest'
$names = @('st_20260824-10-00-00', 'st_20260824-10-30-00', 'st_20260824-11-00-00')

function Step($m) { Write-Host "▶ $m" -ForegroundColor Cyan }
function OK($m)  { Write-Host "  ✓ $m" -ForegroundColor Green }
function BAD($m) { $script:fail += $m; Write-Host "  ✗ $m" -ForegroundColor Red }

# 入口预清理：杜绝上次运行残留导致的跨次假通过
if (Test-Path $rdir) { Remove-Item $rdir -Recurse -Force -ErrorAction SilentlyContinue }

try {
  Step '1/6 生成 3 个静音测试视频（间隔 30 分钟，容器 ffmpeg）'
  New-Item -ItemType Directory -Force $rdir | Out-Null
  $ffmpegFail = 0
  foreach ($n in $names) {
    docker exec bilive_docker bash -c "ffmpeg -y -v error -f lavfi -i testsrc=size=320x240:rate=10 -f lavfi -i anullsrc=r=16000:cl=mono -shortest -t 5 -c:v libx264 -preset ultrafast -c:a aac /app/Videos/_selftest/$n.mp4"
    if ($LASTEXITCODE -ne 0) { BAD "ffmpeg 生成失败: $n"; $ffmpegFail++ }
  }
  $made = (Get-ChildItem $rdir -Filter *.mp4 -ErrorAction SilentlyContinue).Count
  if ($made -eq 3) { OK '3 个测试视频就绪' }
  if ($made -lt 3) {
    BAD "仅生成 $made/3（docker 未运行或 ffmpeg 异常）——终止自测，请先检查容器"
    exit 1
  }

  Step '2/6 回拨 mtime（绕过 10 分钟写入保护，并使场次满足 closed）'
  Get-ChildItem $rdir -Filter *.mp4 | ForEach-Object { $_.LastWriteTime = (Get-Date).AddMinutes(-90) }
  OK 'mtime → 90 分钟前'

  Step '3/6 跑批（process_all 两阶段：规划→批量转写→占位分支）'
  $out = .\process_all.ps1 2>&1
  $rc = $LASTEXITCODE
  if ($rc -eq 3) {
    BAD 'process_all 退出码 3 = run.lock 被占用（与计划任务撞锁）——本次结果无效，稍后重跑'
    exit 1
  }
  $out | Where-Object { $_ -match '_selftest' } | ForEach-Object { Write-Host "  $_" }
  foreach ($n in $names) {
    $srt = Join-Path $rdir "$n.srt"; $sum = Join-Path $rdir "$n.summary.md"
    if (-not (Test-Path $srt)) { BAD "缺 srt: $n"; continue }
    $raw = Get-Content $srt -Raw -Encoding UTF8
    if (-not ($raw -match '\[无语音内容\]')) { BAD "srt 非占位: $n" }
    elseif ($raw.Length -lt 20) { BAD "srt 异常偏小: $n" }
    if (-not (Test-Path $sum)) { BAD "缺 summary: $n"; continue }
    $sumTxt = Get-Content $sum -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if (-not $sumTxt -or $sumTxt.Trim().Length -lt 10) { BAD "summary 空或过短: $n" }
    elseif ($sumTxt -notmatch '无语音') { BAD "summary 未含占位说明: $n" }
  }
  if ($fail.Count -eq 0) { OK '批量转写 + 占位分支（srt/summary 内容断言）全部正确' }

  Step '4/6 场次聚类（session.py：3 段 30min 间隔 → 1 场，已关闭）'
  $sOut = python session.py --room _selftest 2>&1
  if ($LASTEXITCODE -ne 0) { BAD 'session.py 退出码非 0'; $sOut | Select-Object -Last 3 | ForEach-Object { BAD "  $_" } }
  $sj = Join-Path $rdir '_sessions\sessions.json'
  if (-not (Test-Path $sj)) { BAD '缺 sessions.json' }
  else {
    $data = Get-Content $sj -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($data.sessions.Count -ne 1) { BAD "场次数应为 1，实际 $($data.sessions.Count)" }
    else {
      $s = $data.sessions[0]
      if ($s.segment_count -ne 3) { BAD "场次段数应为 3，实际 $($s.segment_count)" }
      elseif (-not $s.closed) { BAD '场次应为 closed（末段 90 分钟前）' }
      else { OK '聚类正确：1 场 · 3 段 · 已关闭' }
    }
  }

  Step '5/6 REPORT 包含测试房间'
  $rgOut = python report_gen.py 2>&1
  if ($LASTEXITCODE -ne 0) { BAD "report_gen 退出码 $LASTEXITCODE"; $rgOut | Select-Object -Last 2 | ForEach-Object { BAD "  $_" } }
  $rep = Get-Content '.\REPORT.md' -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
  if ($rep -match '_selftest') { OK 'REPORT 含测试房间' } else { BAD 'REPORT 未含测试房间（可能为陈旧文件）' }

  Step '6/6 清理现场'
  if (-not $Keep) {
    Remove-Item $rdir -Recurse -Force -ErrorAction SilentlyContinue
    python report_gen.py 2>&1 | Out-Null
    OK '测试目录已清理，REPORT 已还原'
  } else { OK "保留 $rdir 供人工检查" }
} finally {
  if (-not $Keep -and (Test-Path $rdir)) {
    # finally 兜底：任何中途异常/失败路径都不留脏目录（评审[高]）
    Remove-Item $rdir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host '  [finally] 测试目录已兜底清理' -ForegroundColor DarkGray
  }
}

if ($fail) {
  Write-Host '[SELFTEST FAIL]' -ForegroundColor Red
  $fail | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
  exit 1
}
Write-Host '[SELFTEST PASS] 端到端全链路正常' -ForegroundColor Green
exit 0
