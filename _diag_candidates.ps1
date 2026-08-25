# 调试：复现 Get-Candidates 逻辑，看 1790093449 的 mp4 为何被排除
$ErrorActionPreference = 'Continue'
Set-Location 'D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools'
$Videos = 'D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools\bilive-docker\Videos'

Write-Host '=== 逐房间诊断 ==='
Get-ChildItem $Videos -Directory | ForEach-Object {
    $room = $_.FullName
    Write-Host "房间目录: $($_.Name)"
    $mp4Names = @{}
    Get-ChildItem $room -Filter *.mp4 -File -ErrorAction SilentlyContinue | ForEach-Object { $mp4Names[$_.BaseName] = $true }
    Write-Host "  mp4 数: $($mp4Names.Count)"
    $found = Get-ChildItem $room -Include *.mp4,*.flv -File -Recurse -Depth 0 -ErrorAction SilentlyContinue
    Write-Host "  -Include 匹配数: $(@($found).Count)"
    foreach ($f in $found) {
        $age = ((Get-Date) - $f.LastWriteTime).TotalMinutes
        $isFlvOrphan = ($f.Extension -eq '.flv') -and (-not $mp4Names.ContainsKey($f.BaseName))
        $isCand = ($f.Extension -eq '.mp4' -or $isFlvOrphan) -and ($age -gt 10)
        Write-Host ("  {0}  ext={1} age={2:n0}min isCand={3}" -f $f.Name, $f.Extension, $age, $isCand)
    }
}
