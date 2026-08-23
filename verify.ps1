# bilive 一键回归验证（AI/人工改码后必跑；任何一项 FAIL 退出码非 0）
# 覆盖今日踩坑面：py 编译 / ps 解析 / ps1 BOM 存在性(WinPS中文前提) /
# 关键运行时产物 / settings.toml 可解析 / 面板冒烟(未运行则跳过)
param([switch]$Quiet)
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$fail = @()

# 1) Python 编译
foreach ($f in 'panel.py','panel\main.py','panel\services.py',
               'transcribe_host.py','summarize_host.py','qa_check.py',
               'report_gen.py','bilibili_transcribe.py') {
    python -m py_compile $f 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $fail += "py_compile: $f" }
}

# 2) PowerShell 解析 + BOM
foreach ($f in 'process_all.ps1','cleanup.ps1','status.ps1','verify.ps1') {
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

# 5) 面板冒烟（在跑才测）
try {
    $r = Invoke-WebRequest 'http://127.0.0.1:9090/api/recording' -UseBasicParsing -TimeoutSec 10
    if ($r.StatusCode -ne 200) { $fail += "面板 /api/recording HTTP $($r.StatusCode)" }
} catch { if (-not $Quiet) { Write-Host '[warn] 面板未运行，跳过冒烟' } }

if ($fail) {
    Write-Host '[FAIL]' -ForegroundColor Red
    $fail | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
Write-Host '[PASS] 全部检查通过' -ForegroundColor Green
exit 0
