#Requires -RunAsAdministrator
# StayAwake uninstaller

param()

$TaskName = "StayAwake"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $AppDir "stayawake.py"
$LogDir = "$env:APPDATA\StayAwake"
$StateFile = Join-Path $LogDir "state.json"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not elevated - relaunching as admin..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit 0
}

Write-Host "Stopping StayAwake process..." -ForegroundColor Cyan
# Kill python processes running stayawake.py (best effort)
try {
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*stayawake.py*" } | ForEach-Object {
        Write-Host "Killing PID $($_.ProcessId) : $($_.CommandLine)" -ForegroundColor Yellow
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
} catch { Write-Warning $_ }

# Also try taskkill via Task Scheduler
try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
} catch { Write-Verbose "Stop-ScheduledTask ignored: $_" }

# Restore lid setting if we have a saved original and it's stuck at 0
Write-Host "Checking lid-action restore..." -ForegroundColor Cyan
if (Test-Path $StateFile) {
    try {
        $data = Get-Content $StateFile -Raw | ConvertFrom-Json
        $origAc = $data.original_ac
        $origDc = $data.original_dc
        $wasOverride = $data.override_active
        Write-Host "Saved state: original AC=$origAc DC=$origDc override=$wasOverride" -ForegroundColor Gray
        if ($null -ne $origAc -and $origDc -ne $null) {
            # Check current lid value
            $powCheck = powercfg /q SCHEME_CURRENT SUB_BUTTONS 2>&1 | Out-String
            # Try powrprof fallback? Just attempt restore regardless
            Write-Host "Restoring lid to AC=$origAc DC=$origDc ..." -ForegroundColor Cyan
            # Use powrprof via python if available
            $py = (Get-Command python -ErrorAction SilentlyContinue).Source
            if ($py) {
                $restoreCmd = @"
import ctypes, uuid
from ctypes import wintypes
class GUID(ctypes.Structure):
    _fields_=[('Data1',wintypes.DWORD),('Data2',wintypes.WORD),('Data3',wintypes.WORD),('Data4',wintypes.BYTE*8)]
def g(s):
    import uuid
    u=uuid.UUID(s)
    x=GUID(); x.Data1=u.time_low; x.Data2=u.time_mid; x.Data3=u.time_hi_version
    b=u.bytes[8:]
    [setattr(x.Data4,i,b[i]) or None for i in range(8)]
    return x
powr=ctypes.WinDLL('powrprof.dll')
k=ctypes.WinDLL('kernel32')
pg=ctypes.POINTER(GUID)()
powr.PowerGetActiveScheme(None, ctypes.byref(pg))
sub=g('4f971e89-eebd-4455-a8de-9e59040e7347')
lid=g('5ca83367-6e45-459f-a27b-476b1d01c936')
powr.PowerWriteACValueIndex(None, pg, ctypes.byref(sub), ctypes.byref(lid), int($origAc))
powr.PowerWriteDCValueIndex(None, pg, ctypes.byref(sub), ctypes.byref(lid), int($origDc))
powr.PowerSetActiveScheme(None, pg)
ctypes.windll.kernel32.LocalFree(pg)
k.SetThreadExecutionState(0x80000000)
print('restored')
"@
                $tmpPy = Join-Path $env:TEMP "stay_restore.py"
                Set-Content -Path $tmpPy -Value $restoreCmd -Encoding UTF8
                & $py $tmpPy 2>&1 | Write-Host
                Remove-Item $tmpPy -ErrorAction SilentlyContinue
            } else {
                # fallback powercfg
                powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION $origAc 2>&1 | Write-Host
                powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION $origDc 2>&1 | Write-Host
                powercfg /setactive SCHEME_CURRENT 2>&1 | Write-Host
            }
            # Verify
            powercfg /q SCHEME_CURRENT SUB_BUTTONS 2>&1 | Select-String -Pattern "Lid|Current" | Write-Host
        }
    } catch {
        Write-Warning "Restore check failed: $_"
    }
}
# Release execution state
try {
    $k = Add-Type -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint es);' -Name StayKernel -Namespace Win32 -PassThru -ErrorAction SilentlyContinue
    [Win32.StayKernel]::SetThreadExecutionState(0x80000000) | Out-Null
} catch { Write-Verbose "Release execution state ignored: $_" }

# Remove scheduled task
Write-Host "Removing Task Scheduler task..." -ForegroundColor Cyan
try {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Task $TaskName removed." -ForegroundColor Green
    } else {
        # try schtasks
        schtasks /delete /tn $TaskName /f 2>&1 | Write-Host
    }
} catch { Write-Warning $_ }

# Optionally keep log/state for audit, or remove? Keep logs, remove state marker to prevent false recovery
Write-Host "Cleaning state marker (keeping logs)..." -ForegroundColor Cyan
try {
    if (Test-Path $StateFile) {
        Remove-Item $StateFile -Force
        Write-Host "Removed $StateFile" -ForegroundColor Gray
    }
    # Also unhide lid attribute for visibility (optional)
    powercfg -attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE 2>&1 | Out-Null
    Write-Host "Lid action visibility ensured (powercfg -attributes -ATTRIB_HIDE)" -ForegroundColor Gray
} catch { Write-Verbose "Cleanup ignored: $_" }

Write-Host ""
Write-Host "StayAwake uninstalled."
Write-Host ""
Write-Host "- Task Scheduler entry removed."
Write-Host "- Lid-action restored to original (if it was stuck)."
Write-Host "- Logs kept at $LogDir\stayawake.log for audit (delete manually if desired)."
Write-Host ""
