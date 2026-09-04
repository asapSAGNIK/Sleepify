# StayAwake — Spotify-aware lid-close sleep control (Windows)

Silent Windows background utility that keeps your laptop awake with the lid closed **only** while Spotify is playing. Otherwise it behaves exactly like stock Windows.

## How it works

| Condition | Lid closed | Behavior |
|-----------|------------|----------|
| Spotify **playing** | closed | **Stay awake indefinitely** — `SetThreadExecutionState(ES_AWAYMODE)` + lid-action set to *Do nothing* (0) |
| Spotify **never played** this session | closed | **Sleep immediately** — stock Windows, tool never touches power settings |
| Spotify **paused/stopped** after playing | closed | 5-minute grace period → if still paused & lid still closed, **restore lid setting + force sleep** |
| Spotify **paused**, lid **open** | open | Grace expires → restore lid setting but **do NOT force sleep** |

Also: `Ctrl+Alt+S` toggles the whole tool on/off (beep confirms).

## Requirements

- Windows 10/11
- Python 3.10+ (python + pythonw)
- Spotify Desktop app (not web)
- Administrator (Task Scheduler runs elevated)

## Install

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Task Scheduler entry (run as Administrator)
powershell -ExecutionPolicy Bypass -File install.ps1

# Verify:
powercfg /q SCHEME_CURRENT SUB_BUTTONS
# Lid close action should show 1 (Sleep) before playing
Get-ScheduledTask -TaskName StayAwake
Get-Content $env:APPDATA\StayAwake\stayawake.log -Tail 20
```

The task runs at logon with **Run with highest privileges**, `pythonw stayawake.py` silently.

## Manual run (testing)

```powershell
# Run in foreground with logging (no elevation needed for basic read, but writes need admin)
python stayawake.py --no-elevate

# Custom grace/poll for quick testing
python stayawake.py --no-elevate --grace 1 --poll 5   # 1-min grace, 5s poll
python stayawake.py --grace=1 --poll=5 --no-beep

# Check lid value: 0=Do nothing, 1=Sleep, 2=Hibernate, 3=Shut down
powercfg /q SCHEME_CURRENT SUB_BUTTONS   # needs -attributes -ATTRIB_HIDE once, installer does it
# or via registry:
reg query "HKLM\SYSTEM\CurrentControlSet\Control\Power\PowerSettings\4f971e89-eebd-4455-a8de-9e59040e7347\5ca83367-6e45-459f-a27b-476b1d01c936" /s
```

## Hotkey

- `Ctrl+Alt+S` anywhere → toggle enabled/disabled.
- Two ascending beeps = **ON**, single low beep = **OFF**.
- Toast notifications were unreliable (Python AppUserModelID issue) so beep is the primary feedback; no window/tray needed.
- Implementation uses `RegisterHotKey` + `RegisterPowerSettingNotification` in a hidden window; falls back to `keyboard` lib if registration conflicts.

## Files

- `stayawake.py` — main utility (single file, all config at top)
- `requirements.txt`
- `install.ps1` — Task Scheduler installer (highest privileges, logon trigger, restart on failure)
- `uninstall.ps1` — removes task, restores lid, kills process
- `test_simulation.py` — fast state-machine simulation (mock sleep/power, 10s grace)

## Testing checklist (must pass all)

Run `python test_simulation.py` for automated simulation, plus manual steps:

```powershell
python test_simulation.py        # automated, <1 min
```

Manual (requires actual lid + Spotify):

- [ ] **Spotify never opened, lid closed → sleeps immediately, same as without tool.**
  - Start tool (`python stayawake.py --no-elevate`), verify `powercfg /q SCHEME_CURRENT SUB_BUTTONS` shows `0x00000001` (Sleep). Close lid — should sleep.

- [ ] **Spotify playing, lid closed → stays awake indefinitely.**
  - Play Spotify, wait ~12s for poll, verify `powercfg` shows `0x00000000` (Do nothing) and `SetThreadExecutionState` is held (check log: "Override ACTIVE"). Close lid — stays awake.

- [ ] **Playing then paused, lid closed, wait 5+ min → sleeps and lid restored.**
  - Start playing → pause, close lid, wait 5 min untouched. Should see log "Grace expired... forcing sleep" and `powercfg` back to `0x00000001`.

- [ ] **Playing then paused, lid OPEN, wait 5+ min → does NOT force sleep.**
  - Same but leave lid open — should restore lid but not sleep (log: "Lid is OPEN ... NOT sleeping").

- [ ] **Kill process while override active → lid not stuck on Do nothing.**
  - Play Spotify (override active, lid=0), kill via Task Manager, run `powercfg /q ...` or `python test_simulation.py` crash-recovery test — should auto-restore on next start (check log "Crash recovery"). Also `uninstall.ps1` restores as fallback.

- [ ] **Hotkey toggles on/off reliably with beep.**
  - Press `Ctrl+Alt+S` → beep, log "Hotkey toggle: enabled now False/True", state.json reflects. With disabled, lid should be restored even if Spotify playing.

## Architecture notes

- **Playback detection**: `winsdk` `GlobalSystemMediaTransportControlsSessionManager`, filtered to `SpotifyAB...!Spotify`, polled 12s. No API keys/OAuth.
- **Lid state**: `RegisterPowerSettingNotification` with `GUID_LIDSWITCH_STATE_CHANGE (BA3E0F4D-...)`, tracked in hidden window (`WM_POWERBROADCAST`). Initial state assumed open until first notification (Data=1=open, 0=closed).
- **Power**: `powrprof.dll` `PowerReadACValueIndex` / `PowerWriteACValueIndex` + `PowerSetActiveScheme` (fallback to `powercfg`). Original values captured at startup via `PowerGetActiveScheme`, persisted to `%APPDATA%\StayAwake\state.json` for crash recovery.
- **Self-healing**: Periodic 60s check — if override active but lid !=0, re-apply; if inactive but stuck at 0, restore. Startup recovery reads marker and restores if previous run died mid-override.
- **Silent**: `pythonw.exe` via Task Scheduler, `ShowWindow(SW_HIDE)` if console, no tray icon. Logging to `%APPDATA%\StayAwake\stayawake.log` + state.json.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File uninstall.ps1  # as admin
```

Removes task, restores lid to original, kills process, keeps logs (delete `%APPDATA%\StayAwake` manually if desired).

## Troubleshooting

- `powercfg /q` doesn't show Lid? Run `powercfg -attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE` (installer does this).
- Hotkey 1409 error? Another app registered Ctrl+Alt+S — fallback to `keyboard` lib still works.
- Lid Data inverted? Some hardware reports swapped — check log "Lid state change: Data=..." and invert `new_closed = (data_byte == 1)` if needed.
- `SetSuspendState` fails? Needs admin; installer runs as admin. `--no-elevate` mode will log but not actually sleep.

## Config (top of stayawake.py)

```python
HOTKEY_STR = "ctrl+alt+s"
POLL_INTERVAL_SEC = 12
GRACE_MINUTES = 5
BEEP_ENABLED = True
```
Override via CLI: `python stayawake.py --grace 1 --poll 5 --no-beep`.
