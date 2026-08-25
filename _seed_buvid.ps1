# 采集 B 站设备 cookie（buvid3/buvid4）并写入 settings.toml
$r = Invoke-WebRequest 'https://www.bilibili.com' -UseBasicParsing -TimeoutSec 10 -SessionVariable sess
$cookies = $sess.Cookies.GetCookies('https://www.bilibili.com')
$pairs = @()
foreach ($c in $cookies) {
    if ($c.Name -in @('buvid3', 'buvid4', 'b_nut', '_uuid')) {
        $pairs += "$($c.Name)=$($c.Value)"
    }
}
$cookieStr = $pairs -join '; '
Write-Host "采集到设备 cookie 项: $($pairs.Count)"
if ($pairs.Count -eq 0) {
    Write-Host '未采集到 buvid 系 cookie，列出全部:'
    $cookies | ForEach-Object { Write-Host "  $($_.Name)" }
    exit 2
}
# 写入 settings.toml 的 cookie 行
$p = 'D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools\bilive-docker\settings.toml'
$lines = [System.IO.File]::ReadAllLines($p)
$out = @()
$done = $false
foreach ($ln in $lines) {
    if ($ln -match '^\s*cookie\s*=') {
        $out += ('cookie = "' + $cookieStr + '"')
        $done = $true
    } else {
        $out += $ln
    }
}
[System.IO.File]::WriteAllLines($p, $out)
Write-Host "settings.toml cookie 已写入（$done）: $($cookieStr.Substring(0, [Math]::Min(40, $cookieStr.Length)))..."
