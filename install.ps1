#Requires -RunAsAdministrator
# StayAwake installer - creates Task Scheduler entry with "Run with highest privileges"
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1
# Optional: -NoStart to not start immediately

param(
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$TaskName = "StayAwake"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $AppDir "stayawake.py"
$LogDir = "$env:APPDATA\StayAwake"

if (-not (Test-Path $ScriptPath)) {
    Write-Error "stayawake.py not found at $ScriptPath"
    exit 1
}

# Check admin (requires -RunAsAdministrator, but double-check for scheduled task)
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not elevated - relaunching as admin..." -ForegroundColor Yellow
    $argList = '-ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    if ($MyInvocation.UnboundArguments) { $argList += ' ' + ($MyInvocation.UnboundArguments -join ' ') }
    Start-Process powershell -ArgumentList $argList -Verb RunAs
    exit 0
}

# Find Python
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
$pyw = $null
if ($py) {
    $pywCandidate = Join-Path (Split-Path $py) "pythonw.exe"
    if (Test-Path $pywCandidate) { $pyw = $pywCandidate }
}
if (-not $pyw) {
    # try py launcher
    $pyw = $py
    Write-Host "pythonw.exe not found, using $pyw (console window will flash briefly)" -ForegroundColor Yellow
}

if (-not $py -and -not $pyw) {
    Write-Error "Python not found on PATH. Install Python 3.10+ and add to PATH."
    exit 1
}

# Install dependencies (robust to pip warnings like invalid distribution ~nyio)
Write-Host "Installing dependencies..." -ForegroundColor Cyan
# Clean stale pip temp dirs that cause "~nyio" warnings (failed anyio install) - use correct Lib path
$pyDir = Split-Path $py
$sitePkgs = Join-Path $pyDir "Lib\site-packages"
if (Test-Path $sitePkgs) {
    Get-ChildItem -LiteralPath $sitePkgs -Directory -Filter "~*" -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "Removing stale $($_.FullName) ..." -ForegroundColor Yellow
        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}
$oldEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $pipOut = & $py -m pip install -r (Join-Path $AppDir "requirements.txt") --quiet --disable-pip-version-check 2>&1 | Out-String
    if ($pipOut) { Write-Host $pipOut }
    if ($LASTEXITCODE -ne 0) { Write-Warning "pip install exited $LASTEXITCODE (may still be ok if deps already installed)" }
} catch {
    Write-Warning "pip install warning (continuing): $_"
} finally {
    $ErrorActionPreference = $oldEAP
}

# Ensure log dir
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Remove existing task if present
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Removing existing task $TaskName..." -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
} catch { Write-Verbose "No existing task to remove: $_" }

# Build task
# Use logon trigger (at user logon) with highest privileges, run only when user is logged on
# For silent background, use pythonw; for debugging you can switch to python
$Exe = $pyw
$Args = '"' + $ScriptPath + '"'
$WorkingDir = $AppDir

# Create action - Args is quoted path for scheduled task
$Action = New-ScheduledTaskAction -Execute $Exe -Argument $Args -WorkingDirectory $WorkingDir

# Trigger: at logon
$Trigger = New-ScheduledTaskTrigger -AtLogOn

# Principal: current user, highest privileges
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType S4U -RunLevel Highest
# Note: S4U = Service For User, not interactive, but for "run with highest privileges" we need interactive.
# Alternative: use -UserId $env:USERNAME consistently; try interactive logon type if S4U fails for pythonw with UI (hotkey/beep needs interactive).
# We'll try to use Interactive grouping: LogonType Interactive.
try {
    $Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Highest -Id "Author"
} catch {
    Write-Verbose "Interactive principal failed, falling back to S4U: $_"
    $Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType S4U -RunLevel Highest
}

# Settings: allow start on demand, not hidden if possible, wake to run false, disallow battery restrictions
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden:$false -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
# MultipleInstances: IgnoreNew
$Settings.MultipleInstances = 2 # IgnoreNew

# Register
Write-Host "Registering Task Scheduler task '$TaskName' as $UserId (highest privileges)..." -ForegroundColor Cyan
Write-Host "  Execute: $Exe $Args" -ForegroundColor Gray
Write-Host "  WorkingDir: $WorkingDir" -ForegroundColor Gray

try {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description "StayAwake - Spotify-aware lid-close sleep control (runs silent, Ctrl+Alt+S to toggle)" | Out-Null
    Write-Host "Task registered successfully." -ForegroundColor Green
} catch {
    Write-Host "Failed to register task with principal $UserId : $_" -ForegroundColor Red
    Write-Host "Trying fallback with current user interactive..." -ForegroundColor Yellow
    $cmd = 'schtasks /create /tn "{0}" /tr "''{1}'' ''{2}''" /sc onlogon /rl HIGHEST /f' -f $TaskName, $Exe, $ScriptPath
    Write-Host $cmd -ForegroundColor Gray
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create task via schtasks: exit $LASTEXITCODE"
        exit 1
    }
}

# Verify
try {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "Task verified: $($t.TaskName) State=$($t.State)" -ForegroundColor Green
    Write-Host "Trigger: $($t.Triggers[0].ToString())" -ForegroundColor Gray
} catch {
    Write-Warning "Task verification failed: $_"
}

if (-not $NoStart) {
    Write-Host "Starting task now..." -ForegroundColor Cyan
    try {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Task started. Check $LogDir\stayawake.log for output." -ForegroundColor Green
        Start-Sleep -Seconds 2
        $log = Join-Path $LogDir "stayawake.log"
        if (Test-Path $log) {
            Write-Host "--- Last log lines ---" -ForegroundColor Cyan
            Get-Content $log -Tail 20 | Write-Host
        }
    } catch {
        Write-Warning "Start-ScheduledTask failed: $_ (task will still auto-start at next logon)"
    }
}

Write-Host ""
Write-Host "StayAwake installed!"
Write-Host ""
Write-Host "- Runs silently at logon via Task Scheduler (highest privileges)."
Write-Host "- Hotkey Ctrl+Alt+S toggles on/off (beep confirms)."
Write-Host "- Log file: $LogDir\stayawake.log"
Write-Host "- State file: $LogDir\state.json"
Write-Host "- To uninstall: run uninstall.ps1 as admin"
Write-Host "- To test manually: python '$ScriptPath' --no-elevate  (with Spotify playing/paused)"
Write-Host ""
