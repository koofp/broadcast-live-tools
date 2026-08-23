# bilive 一键"换账号/出口 → 验证风控 → 重启容器"助手
# 用法（PowerShell）：
#   1) 先把目标房间写进 bilive-docker/settings.toml 的 [[tasks]]，并在 [header] 填入新账号完整 cookie
#   2) .\bilive-unlock-check.ps1 [-RoomId 1832485943] [-Cookie "SESSDATA=...; buvid3=..."]
#      （也可不传 -Cookie，自动从 bilive-docker/settings.toml 读取）
#   3) 输出：getDanmuInfo code 是否为 0（解除风控）→ 是则自动重启容器开录
param(
    [string]$RoomId = "1832485943",
    [string]$Cookie = "",
    [string]$DockerDir = (Join-Path $PSScriptRoot 'bilive-docker')   # 修复：原硬编码绝对路径
)

$ErrorActionPreference = "Stop"
$settingsPath = Join-Path $DockerDir "settings.toml"

if (-not $Cookie) {
    # 从 settings.toml 的 [header] cookie = "..." 提取
    $content = Get-Content $settingsPath -Raw
    if ($content -match 'cookie\s*=\s*"([^"]*)"') {
        $Cookie = $Matches[1]
        Write-Host "从 settings.toml 读取 cookie 前缀: $($Cookie.Substring(0, [Math]::Min(24, $Cookie.Length)))..."
    } else {
        Write-Error "未传 -Cookie 且 settings.toml 里没有 cookie，请先填写"
        exit 1
    }
}

Write-Host "=== 1/3 测试目标房间 getDanmuInfo（风控判定）==="
$headers = @{ 'Referer' = "https://live.bilibili.com/$RoomId"; 'User-Agent' = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36" }
if ($Cookie) { $headers['Cookie'] = $Cookie }

try {
    $resp = Invoke-RestMethod -Uri "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?id=$RoomId&type=0" -Headers $headers -TimeoutSec 15
    $code = $resp.code
    $token = $resp.data.token
    Write-Host "getDanmuInfo code=$code token_len=$(if($token){$token.Length}else{0})"
    if ($code -ne 0) {
        Write-Host "❌ 仍被风控（code=$code）。请换老账号/手机热点后再试。参考 bilive-runbook.md。"
        exit 2
    }
} catch {
    Write-Host "❌ 请求失败: $($_.Exception.Message)"
    exit 2
}

Write-Host "✅ 风控已解除（code=0）！"
Write-Host "=== 2/3 目标房间在线状态 ==="
$rinfo = Invoke-RestMethod -Uri "https://api.live.bilibili.com/room/v1/Room/get_info?room_id=$RoomId" -TimeoutSec 15
if ($rinfo.data.live_status -eq 1) {
    Write-Host "房间在直播（$($rinfo.data.title)）→ 即将开录"
} else {
    Write-Host "⚠️ 房间当前未直播（live_status=$($rinfo.data.live_status)），容器会待开播自动录"
}

Write-Host "=== 3/3 重启容器应用新 cookie ==="
docker rm -f bilive_docker 2>$null
docker run -itd --name bilive_docker --restart unless-stopped `
    -e RECORD_KEY=bilive2024 -p 22333:2233 `
    -v "$DockerDir\bilive.toml:/app/bilive.toml" `
    -v "$DockerDir\settings.toml:/app/settings.toml" `
    -v "$DockerDir\Videos:/app/Videos" `
    -v "$DockerDir\logs:/app/logs" `
    ghcr.io/timerring/bilive:0.3.1
Start-Sleep -Seconds 15
Write-Host "容器状态: $(docker ps --filter name=bilive_docker --format '{{.Status}}')"
Write-Host "完成。去 http://localhost:22333 查看任务；录制品在 $DockerDir\Videos\<roomid>\"