# watchdog.ps1 - Bot va Dashboardni ORQA FONDA (ko'rinmas) doimiy ishga tushiradi.
#
#   • Yagona nusxa: bir vaqtda FAQAT bitta watchdog ishlaydi (exclusive lock).
#   • Bot va Dashboard `pythonw.exe` orqali ishga tushiriladi - oyna yo'q.
#   • Chiqishlar state\ fayllariga yo'naltiriladi.
#   • Crash bo'lsa avtomatik qayta ishga tushiradi (backoff bilan).
#   • Kompyuter qayta ochilganda Start menyudagi BM_Watchdog.vbs uni ishga tushiradi.
#
# Ishga tushirish:
#   powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File watchdog.ps1

param(
    [int]$CheckInterval = 15,
    [int]$MaxRestarts = 20,
    [int]$CooldownSec = 30
)

# ---------------------------------------------------------------- asosiy yo'llar
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptDir)) { $scriptDir = (Get-Location).Path }
$root = $scriptDir

$pythonDir = "C:\Users\User\AppData\Local\Python\pythoncore-3.14-64"
$pythonw = Join-Path $pythonDir "pythonw.exe"
$python  = Join-Path $pythonDir "python.exe"
if (Test-Path $pythonw) { $exe = $pythonw } else { $exe = $python }

$stateDir = Join-Path $root "state"
if (!(Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
$logFile    = Join-Path $stateDir "watchdog.log"
$bootFile   = Join-Path $stateDir "watchdog_boot.log"
$lockFile   = Join-Path $stateDir "watchdog.lock"
$botPidFile = Join-Path $stateDir "bot.pid"
$botOutLog  = Join-Path $stateDir "watchdog_bot.out.log"
$botErrLog  = Join-Path $stateDir "watchdog_bot.err.log"
$dashOutLog = Join-Path $stateDir "watchdog_dash.out.log"
$dashErrLog = Join-Path $stateDir "watchdog_dash.err.log"
$dashboardPort = 8080

# ------------------------------------------------- boot-log (har qadam yoziladi)
function Write-Boot($msg) {
    try {
        [System.IO.File]::AppendAllText($bootFile,
            ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $msg + "`r`n"))
    } catch {}
}

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    try {
        if (Test-Path $logFile) {
            $len = (Get-Item $logFile).Length
            if ($len -gt 2MB) {
                $keep = Get-Content $logFile -Tail 300 -ErrorAction SilentlyContinue
                Set-Content -Path $logFile -Value $keep -ErrorAction SilentlyContinue
            }
        }
        [System.IO.File]::AppendAllText($logFile, ($line + "`r`n"))
    } catch {}
}

Write-Boot "watchdog boshlanmoqda: scriptDir=$scriptDir exe=$([System.IO.Path]::GetFileName($exe))"

# ------------------------------------------------- yagona nusxa (exclusive lock)
$lockHandle = $null
$sw = $null
try {
    $lockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate,
                                         [System.IO.FileAccess]::ReadWrite,
                                         [System.IO.FileShare]::None)
    $sw = New-Object System.IO.StreamWriter($lockHandle)
    $sw.WriteLine($PID)
    $sw.Flush()
    Write-Boot "lock olindi pid=$PID"
} catch {
    Write-Log "Boshqa watchdog allaqachon ishlamoqda ($lockFile). Bu nusxa chiqadi."
    Write-Boot "lock band - chiqilmoqda"
    exit 0
}

Write-Log "========================================="
Write-Log "Watchdog started (interval=${CheckInterval}s, exe=$([System.IO.Path]::GetFileName($exe)))"
Write-Log "========================================="
Write-Boot "sikl boshlanmoqda"

# ---------------------------------------------------------------- yordamchilar
function Test-PortOpen($port) {
    try {
        $conn = [System.Net.Sockets.TcpClient]::new()
        $conn.Connect("127.0.0.1", $port)
        $conn.Close()
        return $true
    } catch { return $false }
}

function Get-BmProcs($type) {
    # Faqat haqiqiy python/pythonw jarayonlari
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^python(w)?\.exe$' -and
            $_.CommandLine -and $_.CommandLine -match ("bm_automation\s+" + $type)
        })
}

function Test-BotAlive {
    # Bot o'zi yozgan PID fayli (state/bot.pid = "pid|time") bo'yicha tekshiradi.
    # Bu WMI o'rniga ishonchli: bot qulagan bo'lsa fayldagi PID o'lik bo'ladi.
    try {
        if (Test-Path $botPidFile) {
            $raw = (Get-Content $botPidFile -TotalCount 1 -ErrorAction Stop).Trim()
            $pidNum = 0
            $ok = [int]::TryParse(($raw -split '\|')[0], [ref]$pidNum)
            if ($ok -and $pidNum -gt 0) {
                $proc = Get-Process -Id $pidNum -ErrorAction SilentlyContinue
                if ($proc) { return $true }
            }
        }
    } catch {}
    # Zaxira: WMI bo'yicha jarayon qidirish
    return ((Get-BmProcs "bot").Count -gt 0)
}

function Start-BmService($type) {
    $argList = if ($type -eq "bot") {
        @("-X", "utf8", "-u", "-m", "bm_automation", "bot", "--watchdog")
    } else {
        @("-X", "utf8", "-u", "-m", "bm_automation", "dashboard", "--port", "$dashboardPort", "--no-browser")
    }
    $outLog = if ($type -eq "bot") { $botOutLog } else { $dashOutLog }
    $errLog = if ($type -eq "bot") { $botErrLog } else { $dashErrLog }
    Write-Log "START $type"
    $p = Start-Process -FilePath $exe -ArgumentList $argList `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
    Write-Log "START $type launch_pid=$($p.Id)"
    $null = $p
}

# ---------------------------------------------------------------- asosiy sikl
$restartCounts = @{"bot" = 0; "dashboard" = 0}
$lastRestart = @{"bot" = [datetime]::MinValue; "dashboard" = [datetime]::MinValue}
$lastOkAt = @{"bot" = Get-Date; "dashboard" = Get-Date}

while ($true) {
    try {
        foreach ($svc in @("bot", "dashboard")) {
            $alive = $false
            if ($svc -eq "dashboard") {
                $alive = Test-PortOpen $dashboardPort
                if (!$alive) {
                    $procs = @(Get-BmProcs "dashboard")
                    if ($procs.Count -gt 0) {
                        Write-Log "dashboard: jarayon bor lekin port yopiq, qayta ishga tushiriladi"
                        foreach ($pr in $procs) {
                            Stop-Process -Id $pr.ProcessId -Force -ErrorAction SilentlyContinue
                        }
                        Start-Sleep -Seconds 3
                    }
                }
            } else {
                $alive = Test-BotAlive
            }

            if ($alive) {
                # Barqaror ishlagan vaqtdan keyin restart hisobini nollash
                $stable = (Get-Date) - $lastRestart[$svc]
                if ($stable.TotalMinutes -ge 15) { $restartCounts[$svc] = 0 }
                $lastOkAt[$svc] = Get-Date
                continue
            }

            # DOWN - qayta ishga tushirish (cooldown + cheklov bilan)
            $sinceDown = (Get-Date) - $lastOkAt[$svc]
            if ($sinceDown.TotalSeconds -lt $CooldownSec) {
                continue
            }
            $restartCounts[$svc]++
            if ($restartCounts[$svc] -gt $MaxRestarts) {
                Write-Log "${svc}: juda ko'p restart ($MaxRestarts), 5 daqiqa tanaffus"
                Start-Sleep -Seconds 300
                $restartCounts[$svc] = 0
                continue
            }
            $since = (Get-Date) - $lastRestart[$svc]
            if ($since.TotalSeconds -ge $CooldownSec) {
                $lastRestart[$svc] = Get-Date
                Start-BmService $svc
                Start-Sleep -Seconds 8
            }
        }

        $bOK = if (Test-BotAlive) { "OK" } else { "DOWN" }
        $dOK = if (Test-PortOpen $dashboardPort) { "OK" } else { "DOWN" }
        Write-Log "STATUS bot=$bOK dashboard=$dOK restarts=bot=$($restartCounts['bot']) dash=$($restartCounts['dashboard'])"
    } catch {
        Write-Log "Sikl xatosi: $($_.Exception.Message)"
        Write-Boot "sikl xatosi: $($_.Exception.ToString())"
    }
    Start-Sleep -Seconds $CheckInterval
}

if ($lockHandle) {
    try { if ($sw) { $sw.Dispose() }; $lockHandle.Dispose() } catch {}
}
