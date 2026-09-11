# servislar.ps1 - Bot va Dashboard servislarini boshqarish.
# Ishlatilishi:
#   powershell -NoProfile -ExecutionPolicy Bypass -File servislar.ps1 -Action start
#   powershell -NoProfile -ExecutionPolicy Bypass -File servislar.ps1 -Action stop
#   powershell -NoProfile -ExecutionPolicy Bypass -File servislar.ps1 -Action restart
#   powershell -NoProfile -ExecutionPolicy Bypass -File servislar.ps1 -Action status

param(
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action = "status"
)

$root = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($root)) { $root = (Get-Location).Path }
$vbs  = Join-Path $root "run_hidden.vbs"
$bot  = Join-Path $root "bot.bat"
$dash = Join-Path $root "dashboard.bat"

function Get-BmProcs {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'bm_automation\s+(bot|dashboard)' } |
        Sort-Object CommandLine
}

function Get-Who($line) {
    if ($line -match 'dashboard') { return "dashboard" }
    return "bot"
}

function Test-Running($who) {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match ("bm_automation " + $who) } |
        Select-Object -First 1
    return ($null -ne $p)
}

function Show-Status {
    $procs = @(Get-BmProcs)
    if ($procs.Count -eq 0) {
        Write-Host "[HOLAT] Barcha servislar o'chirilgan." -ForegroundColor Yellow
        return
    }
    Write-Host "[HOLAT] Ishlamoqda:" -ForegroundColor Green
    foreach ($p in $procs) {
        Write-Host ("  [{0}] PID {1}" -f (Get-Who $p.CommandLine).ToUpper(), $p.ProcessId)
    }
}

function Stop-BmServices {
    $procs = @(Get-BmProcs)
    if ($procs.Count -eq 0) {
        Write-Host "[STOP] Servis topilmadi (allaqachon o'chirilgan)." -ForegroundColor Yellow
        return
    }
    foreach ($p in $procs) {
        Write-Host ("[STOP] {0} PID {1}" -f (Get-Who $p.CommandLine), $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

function Start-One($who, $bat) {
    if (Test-Running $who) {
        Write-Host ("[START] {0} allaqachon ishlamoqda, o'tkazib yuborildi." -f $who) -ForegroundColor Yellow
        return
    }
    Start-Process -FilePath "$env:WINDIR\System32\wscript.exe" `
        -ArgumentList ('"{0}" "{1}"' -f $vbs, $bat)
    Write-Host ("[START] {0} ishga tushirilmoqda (vbs)..." -f $who)
}

function Start-BmServices {
    Start-One "bot" $bot
    Start-One "dashboard" $dash
    Start-Sleep -Seconds 8
    Show-Status
}

switch ($Action.ToLower()) {
    "start"   { Start-BmServices }
    "stop"    { Stop-BmServices; Show-Status }
    "restart" { Stop-BmServices; Start-BmServices }
    "status"  { Show-Status }
}
