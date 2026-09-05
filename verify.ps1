# bilive 一键回归验证（AI/人工改码后必跑；任何一项 FAIL 退出码非 0）
# 覆盖今日踩坑面：py 编译 / ps 解析 / ps1 BOM 存在性(WinPS中文前提) /
# 关键运行时产物 / settings.toml 可解析 / 面板冒烟(未运行则跳过)
param([switch]$Quiet)
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$fail = @()

# 1) Python 编译
foreach ($f in 'panel.py','panel\main.py','panel\services.py','session.py',
               'transcribe_host.py','summarize_host.py','qa_check.py',
               'report_gen.py','bilibili_transcribe.py','provider_config.py') {
    python -m py_compile $f 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $fail += "py_compile: $f" }
}

# 1.5) 单元测试（无需 API/网络的纯逻辑回归；失败保留细节便于排查）
foreach ($t in 'tests\test_merge_archived.py','tests\test_provider_config.py','tests\test_run_lock.py','tests\test_summaries_list.py') {
    $uOut = python $t 2>&1
    if ($LASTEXITCODE -ne 0) { $fail += ("unit: $t → " + ($uOut | Select-Object -Last 2) -join ' ') }
}

# 2) PowerShell 解析 + BOM（含全部接线/测试脚本——评审：漏一个都可能静默坏掉）
foreach ($f in 'process_all.ps1','cleanup.ps1','status.ps1','verify.ps1','notify.ps1','backup_metadata.ps1','selftest.ps1','bilive-unlock-check.ps1') {
    $raw = Get-Content $f -Raw
    $errs = $null
    [System.Management.Automation.PSParser]::Tokenize($raw, [ref]$errs) | Out-Null
    if ($errs.Count -gt 0) { $fail += ("parse {0}: {1}" -f $f, $errs[0].Message) }
    $b = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot $f))
    if (-not ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)) {
        $fail += "BOM 缺失: $f"
    }
}

# 3) 关键运行时产物
foreach ($f in 'models\faster-whisper-small', 'bilive-docker\settings.toml', 'prompt.txt') {
    if (-not (Test-Path $f)) { $fail += "缺失: $f" }
}

# 4) settings.toml 可解析（tomllib + 容错读取，与 panel 同口径）
python -c "import tomllib,pathlib; tomllib.loads(pathlib.Path(r'bilive-docker/settings.toml').read_bytes().decode('utf-8-sig'))" 2>$null
if ($LASTEXITCODE -ne 0) { $fail += 'settings.toml 解析失败' }

# 5) 面板冒烟：未运行则尝试经桌面启动器拉起一次（评审[中]：面板起不来不能绿灯）
$up = $false
try {
    $r = Invoke-WebRequest 'http://127.0.0.1:9090/api/recording' -UseBasicParsing -TimeoutSec 10
    $up = ($r.StatusCode -eq 200)
} catch { $up = $false }
if (-not $up) {
    if (-not $Quiet) { Write-Host '[info] 面板未运行，尝试经启动器拉起…' }
    if (Test-Path "$env:USERPROFILE\Desktop\启动面板.cmd") {
        Start-Process -FilePath "$env:USERPROFILE\Desktop\启动面板.cmd" -WindowStyle Minimized
    } else { Start-Process -FilePath (Join-Path $PSScriptRoot '启动面板.cmd') -WindowStyle Minimized }
    foreach ($i in 1..15) {
        Start-Sleep 1
        try {
            $r = Invoke-WebRequest 'http://127.0.0.1:9090/api/recording' -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { $up = $true; break }
        } catch {}
    }
    if (-not $up) { $fail += '面板拉起失败（15 秒内未上线），详见 logs\panel-stdout.log' }
}
if ($up) {
    # 页面渲染冒烟：/api 200 不代表 Jinja 页面能渲染（曾出现模板 UndefinedError 500 探不到）
    try {
        $r2 = Invoke-WebRequest 'http://127.0.0.1:9090/settings' -UseBasicParsing -TimeoutSec 10
        if ($r2.StatusCode -ne 200 -or $r2.Content -notmatch 'AI') { $fail += '面板 /settings 渲染异常' }
    } catch { $fail += "面板 /settings 请求失败: $($_.Exception.Message)" }
}

if ($fail) {
    Write-Host '[FAIL]' -ForegroundColor Red
    $fail | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
Write-Host '[PASS] 全部检查通过' -ForegroundColor Green
exit 0
