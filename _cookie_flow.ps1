# 步骤化完成 cookie 传递与写入（每步验证）
$ErrorActionPreference = 'Continue'
Set-Location 'D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools'

Write-Host '=== 1. 启动接收器 ==='
Start-Process -FilePath 'D:\system\pyhone\python.exe' `
  -ArgumentList 'D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools\_catcher.py' `
  -WindowStyle Hidden
Start-Sleep 2
try {
  $r = Invoke-WebRequest 'http://127.0.0.1:18923/save?c=step1-selftest' -UseBasicParsing -TimeoutSec 5
  Write-Host "接收器自检: $($r.Content)"
} catch { Write-Host "接收器失败: $_"; exit 1 }

Write-Host '=== 2. 文件自检（应为 step1-selftest）==='
$f1 = Get-Content '.\bili_cookies.txt' -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
Write-Host "文件内容: $f1"

Write-Host '=== 3. 等待 Playwright 送达（60 秒轮询文件长度 > 500）==='
$got = $false
foreach ($i in 1..30) {
  Start-Sleep 2
  $raw = Get-Content '.\bili_cookies.txt' -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
  if ($raw -and $raw.Length -gt 500) { Write-Host "第 $($i*2) 秒收到 cookie（$($raw.Length) 字符）"; $got = $true; break }
}
if (-not $got) { Write-Host '未收到 Playwright 送达'; exit 1 }

Write-Host '=== 4. 写入 settings.toml ==='
$cookie = (Get-Content '.\bili_cookies.txt' -Raw -Encoding UTF8).Trim()
$p = 'D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools\bilive-docker\settings.toml'
$lines = [System.IO.File]::ReadAllLines($p)
$out = @()
foreach ($ln in $lines) {
  if ($ln -match '^\s*cookie\s*=') { $out += ('cookie = "' + $cookie + '"') } else { $out += $ln }
}
[System.IO.File]::WriteAllLines($p, $out)
$check = (Select-String -Path $p -Pattern 'SESSDATA' -Encoding UTF8).Count
Write-Host "settings.toml 含 SESSDATA: $($check -gt 0)"

Write-Host '=== 5. 停接收器、删临时文件 ==='
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match '_catcher' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item '.\bili_cookies.txt' -Force -ErrorAction SilentlyContinue
Write-Host '完成。cookie 已写入 settings.toml'
