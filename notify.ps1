# bilive 轻量通知：Windows Toast（落动作中心，错过也能查）+ notify.log 持久留痕
# 用法: .\notify.ps1 -Title "..." -Text "..." [-Level info|warn|bad]
# 行为: 同一 Title+Text 30 分钟节流（SHA256 稳定键，GetHashCode 跨会话不稳定已弃用）；
#       info 级不弹气泡仅落动作中心；非交互会话（计划任务/SYSTEM）自动降级仅日志；
#       Toast 失败降级仅日志；%TEMP% 戳文件 24 小时自动清理。
param(
  [Parameter(Mandatory=$true)][string]$Title,
  [Parameter(Mandatory=$true)][string]$Text,
  [ValidateSet('info','warn','bad')][string]$Level = 'info'
)
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$logFile = Join-Path $PSScriptRoot 'logs\notify.log'
New-Item -ItemType Directory -Force (Split-Path $logFile) | Out-Null

# 稳定节流键：SHA256(UTF8) 前 16 hex
$sha = [System.Security.Cryptography.SHA256]::Create()
$key = ([BitConverter]::ToString(
    $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Title + '|' + $Text))
)).Replace('-', '').Substring(0, 16)
$sha.Dispose()
$stampFile = Join-Path $env:TEMP ("bilive_notify_$key.stamp")

# 日志永远记录（无论是否节流/是否有桌面）
Add-Content -LiteralPath $logFile -Value ("{0}`t{1}`t{2}`t{3}" -f (Get-Date -Format s), $Level.ToUpper(), $Title, $Text) -Encoding UTF8

# 节流：30 分钟内同键不重复弹窗
if (Test-Path $stampFile) {
    if (((Get-Date) - (Get-Item $stampFile).LastWriteTime).TotalMinutes -lt 30) { exit 0 }
}

# 戳文件卫生：清理超 24h 的旧戳（防 %TEMP% 无限累积）
Get-ChildItem $env:TEMP -Filter 'bilive_notify_*.stamp' -ErrorAction SilentlyContinue |
    Where-Object { ((Get-Date) - $_.LastWriteTime).TotalHours -gt 24 } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# 非交互会话（计划任务"不管是否登录"/SYSTEM）无桌面 → Toast 必败，降级为仅日志
if (-not [Environment]::UserInteractive) { exit 0 }

Set-Content -LiteralPath $stampFile -Value (Get-Date -Format s) -Encoding UTF8

try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $xml.GetElementsByTagName('text')
    $t.Item(0).AppendChild($xml.CreateTextNode("bilive · $Title")) | Out-Null
    $t.Item(1).AppendChild($xml.CreateTextNode($Text)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    if ($Level -eq 'info') { $toast.SuppressPopup = $true }              # 仅落动作中心，不抢注意力
    if ($Level -eq 'bad')  { $toast.ExpirationTime = (Get-Date).AddMinutes(10) }  # 告警长驻留
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell').Show($toast)
} catch {
    Add-Content -LiteralPath $logFile -Value "$(Get-Date -Format s)`tTOAST-FAIL`t$($_.Exception.Message)" -Encoding UTF8
}
exit 0
