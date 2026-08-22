# bilive 一键管线：录播 → srt 转写 → AI 总结
# 用法示例：
#   .\bilive_pipeline.ps1 -Video "D:\...\Videos\8139918\xxx.flv"
#   .\bilive_pipeline.ps1 -VideoDir "D:\...\Videos\8139918" -Model small -SkipExisting
param(
    [string]$Video,
    [string]$VideoDir,
    [ValidateSet('tiny','base','small','medium')] [string]$Model = 'small',
    [switch]$SkipExisting,
    [string]$SummaryPrompt = "",
    [int]$MaxTokens = 2000
)
$ErrorActionPreference = 'Stop'
$Container = 'bilive_docker'
$OpenRouterKey = $env:OPENROUTER_API_KEY
if (-not $OpenRouterKey) { Write-Error '请先设置 $env:OPENROUTER_API_KEY'; exit 1 }

# 收集目标文件
if ($VideoDir) { $files = Get-ChildItem $VideoDir -Include *.flv,*.mp4 -Recurse -File | Sort-Object Name }
elseif ($Video) { $files = Get-ChildItem $Video }
else { Write-Error '需要 -Video 或 -VideoDir'; exit 1 }

foreach ($f in $files) {
    $base = $f.FullName.Substring(0, $f.FullName.LastIndexOf('.'))
    $srt = "$base.srt"; $sum = "$base.summary.md"
    if ($SkipExisting -and (Test-Path $srt)) { Write-Host "[skip] $($f.Name) 已有 srt"; continue }

    Write-Host "=== [1/2] 转写($Model): $($f.Name) ==="
    # 复制进容器（避免直接读宿主机路径），whisper 输出与视频同目录名
    docker cp "$($f.FullName)" ${Container}:/tmp/pipeline_input$( [IO.Path]::GetExtension($f.FullName) )
    docker exec $Container bash -c "whisper /tmp/pipeline_input.* --model $Model --language zh --output_format srt --output_dir /tmp/ >/dev/null 2>&1"
    docker cp "${Container}:/tmp/pipeline_input.srt" $srt
    Write-Host "  -> $srt"

    Write-Host "=== [2/2] AI 总结 ==="
    $prompt = if ($SummaryPrompt) { $SummaryPrompt } else {
        "你是资深直播内容分析师。以下字幕来自语音识别，可能含同音误听，请结合语境自行纠正（如游戏术语、英雄名、装备名）。`n`n字幕：`n{SRT}`n`n请输出：## 一句话总结`n## 核心主题（不超过20字）`n## 讨论要点（按时间顺序，标注[mm:ss]，每条≤25字）`n## 金句/名场面（如有，含时间戳）`n## 疑似识别错误对照表（原文→推测正确词）"
    }
    $srtText = Get-Content $srt -Raw
    $payload = @{
        model = 'stealth/ox-alpha'; max_tokens = $MaxTokens
        messages = @(@{ role = 'user'; content = $prompt.Replace('{SRT}', $srtText) })
    } | ConvertTo-Json -Depth 5
    $resp = Invoke-RestMethod -Uri 'https://openrouter.ai/api/v1/chat/completions' -Method Post `
        -Headers @{ Authorization = "Bearer $OpenRouterKey"; 'Content-Type' = 'application/json' } `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 600
    [IO.File]::WriteAllText($sum, $resp.choices[0].message.content, [Text.Encoding]::UTF8)
    Write-Host "  -> $sum"
}
Write-Host '全部完成。'
