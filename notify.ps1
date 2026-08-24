# bilive 轻量通知：Windows Toast（落动作中心，错过也能查）+ notify.log 持久留痕
# 用法: .\notify.ps1 -Title "录制风险" -Text "Clash 劫持+停摆" [-Level info|warn|bad]
# 节流: 同一 Title+Text 30 分钟内只弹一次（日志仍记录）；Toast 失败自动降级为仅日志
param(
  [Parameter(Mandatory=$true)][string]$Title,
  [Parameter(Mandatory=$true)][string]$Text,
  [ValidateSet('info','warn','bad')][string]$Level = 'info'
)
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$logFile = Join-Path $PSScriptRoot 'logs\notify.log'
New-Item -ItemType Directory -Force (Split-Path $logFile) | Out-Null

# 节流戳：同内容 30 分钟内不重复弹窗
$stampFile = Join-Path $env:TEMP ('bilive_notify_' + [Math]::Abs(($Title + $Text).GetHashCode()) + '.stamp')
$throttled = $false
if (Test-Path $stampFile) {
    if (((Get-Date) - (Get-Item $stampFile).LastWriteTime).TotalMinutes -lt 30) { $throttled = $true }
}
Set-Content -LiteralPath $stampFile -Value (Get-Date -Format s) -Encoding UTF8

Add-Content -LiteralPath $logFile -Value ("{0}`t{1}`t{2}`t{3}" -f (Get-Date -Format s), $Level.ToUpper(), $Title, $Text) -Encoding UTF8

if ($throttled) { exit 0 }

try {
    # WinRT Toast（WinPS 5.1 原生可用，无需外部模块；AppId 借用 PowerShell 自身）
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $xml.GetElementsByTagName('text')
    $t.Item(0).AppendChild($xml.CreateTextNode("bilive · $Title")) | Out-Null
    $t.Item(1).AppendChild($xml.CreateTextNode($Text)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell').Show($toast)
} catch {
    Add-Content -LiteralPath $logFile -Value "$(Get-Date -Format s)`tTOAST-FAIL`t$($_.Exception.Message)" -Encoding UTF8
}
exit 0
