# StayAwake — Spotify-aware lid-close sleep control (Cross-Platform)

Silent background utility that keeps your laptop awake with the lid closed **only** while Spotify is playing. Otherwise it behaves exactly like stock OS.

## How it works

| Condition | Lid closed | Behavior |
|-----------|------------|----------|
| Spotify **playing** | closed | **Stay awake indefinitely** — hold `caffeinate`/`systemd-inhibit`/`SetThreadExecutionState` + lid `Do nothing` (0) |
| Spotify **never played** this session | closed | **Sleep immediately** — stock OS, never touches power settings |
| Spotify **app closed** | closed | **Immediate restore + sleep** — code stops, lid `Sleep` (1), normal sleep |
| Spotify **paused/stopped + lid closed + no playback** | closed | **Immediate restore + sleep** — flap shut + no playback = normal sleep (code stops) |
| Spotify **paused + lid open + no playback** | open | **Immediate restore, no sleep** — back to default, will sleep on next lid close |

Tool is **idle** when Spotify not running (`poll 60s`, ~0% CPU), **active** when Spotify runs (`poll 12s`). Hotkey `Ctrl+Shift+Space` (Win/Linux) / `Cmd+Shift+Space` (Mac) toggles on/off with custom sounds `assets/sounds/on.wav`/`off.wav`.

## Structure (same logic on all OS, separated dirs)

```
spotifysleeptool/
  core/                     # shared config (HOTKEY, POLL, GRACE) — single source
  windows/                  # Windows working dir (powrprof, GSMTC, RegisterHotKey)
    stayawake.py            # main entry for Windows
    install.ps1 / uninstall.ps1
    requirements.txt
  mac/                      # macOS working dir (caffeinate, osascript, ioreg, launchd)
    stayawake.py
    install.sh / uninstall.sh
    requirements.txt
  linux/                    # Linux working dir (systemd-inhibit, playerctl, /proc/acpi, systemd)
    stayawake.py
    install.sh / uninstall.sh
    requirements.txt
  assets/sounds/            # shared custom sounds (on.wav/off.wav for all OS)
    on.wav  # enabled
    off.wav # disabled
```

## Requirements

- **Windows:** 10/11, Python 3.10+ (python + pythonw), Spotify Desktop, Admin (Task Scheduler)
- **Mac:** 13+, Python 3.10+, Spotify Desktop, `pip install -r mac/requirements.txt` (psutil, pynput, pyobjc)
- **Linux:** Python 3.10+, Spotify Desktop, `playerctl` (`sudo apt install playerctl`), `pip install -r linux/requirements.txt` + `systemd --user`

## Install

**Windows (Admin PowerShell):**
```powershell
cd D:\spotifysleeptool
pip install -r windows\requirements.txt
powershell -ExecutionPolicy Bypass -File windows\install.ps1
# Verify:
powercfg /q SCHEME_CURRENT SUB_BUTTONS  # Lid should be 1 (Sleep) when idle
Get-ScheduledTask -TaskName StayAwake
Get-Content $env:APPDATA\StayAwake\stayawake.log -Tail 20
```

**Mac:**
```bash
cd /path/to/spotifysleeptool
pip3 install -r mac/requirements.txt
bash mac/install.sh  # launchd LaunchAgent
launchctl list | grep StayAwake
cat ~/Library/Application\ Support/StayAwake/stayawake.log
```

**Linux:**
```bash
cd /path/to/spotifysleeptool
pip3 install -r linux/requirements.txt
# sudo apt install playerctl  # for Spotify MPRIS
bash linux/install.sh  # systemd --user
systemctl --user status stayawake
journalctl --user -u stayawake -f
```

**Direct run (no install, any OS):**
```bash
# Windows:
python windows/stayawake.py --no-elevate
# Mac:
python3 mac/stayawake.py --no-elevate
# Linux:
python3 linux/stayawake.py --no-elevate
# Fast test (any):
python windows/stayawake.py --no-elevate --grace 1 --poll 5
```

The task/service runs at logon with `KeepAlive`/`Restart` and `hotkey ctrl+shift+space` to toggle.

## Manual run (testing)

```bash
# Windows foreground:
python windows/stayawake.py --no-elevate
# Mac:
python3 mac/stayawake.py --no-elevate
# Linux:
python3 linux/stayawake.py --no-elevate
# Custom poll/grace:
python windows/stayawake.py --no-elevate --grace 1 --poll 5 --no-beep
```

## Hotkey

- `Ctrl+Shift+Space` (Win/Linux) / `Cmd+Shift+Space` (Mac) anywhere → toggle.
- `on.wav` (1.6s) = ON, `off.wav` (1.6s) = OFF (from `assets/sounds/`). Fallback to system beep if files missing.
- Implementation: `RegisterHotKey` + `RegisterPowerSettingNotification` on Windows, `pynput`/`keyboard` fallback with debounce 0.7s on all OS.

## Files — Working dirs are `windows/` `mac/` `linux/` (no root dispatcher)

- `core/config.py` — shared HOTKEY/POLL/GRACE (single source, all OS)
- `windows/stayawake.py` — Windows working dir, `windows/install.ps1` Task Scheduler
- `mac/stayawake.py` — Mac working dir, `mac/install.sh` launchd
- `linux/stayawake.py` — Linux working dir, `linux/install.sh` systemd
- `assets/sounds/on.wav`/`off.wav` — shared custom sounds (all OS)
- `windows/test_simulation.py` — state-machine simulation

## Testing checklist

```bash
python windows/test_simulation.py  # automated, <1 min
```

Manual (requires lid + Spotify):
- [ ] Never opened, lid closed → sleeps immediately
- [ ] Playing, lid closed → stays awake (lid 0,0, caffeinate/systemd-inhibit held)
- [ ] App closed + lid closed → immediate sleep (lid 1,1, code stopped)
- [ ] Paused + lid closed (no playback) → immediate sleep (lid 1,1)
- [ ] Paused + lid open → immediate restore, no sleep
- [ ] Kill process while override active → lid not stuck (crash recovery via state.json)
- [ ] Hotkey `Ctrl+Shift+Space` toggles with `on.wav`/`off.wav` (debounced, suppress=True)

## Architecture notes

- **Playback:** Windows `GSMTC` (`SpotifyAB...!Spotify`), Mac `osascript -e 'tell app "Spotify" to player state'`, Linux `playerctl -p spotify status` / `dbus-send`, polled 12s active / 60s idle. `psutil` `is_spotify_running` distinguishes app closed vs paused.
- **Lid:** Windows `RegisterPowerSettingNotification` (`GUID_LIDSWITCH`), Mac `ioreg AppleClamshellState` poll 3s, Linux `/proc/acpi/button/lid` poll 5s. `lid_known` gating for forced sleep.
- **Power:** Windows `powrprof` + `powercfg` fallback, original lid saved to `%APPDATA%\StayAwake\state.json`; Mac `caffeinate -dimsu` + `pmset sleepnow`; Linux `systemd-inhibit` + `systemctl suspend`. Self-healing every 60s.
- **Silent:** `pythonw`/`launchd`/`systemd --user`, `ShowWindow(SW_HIDE)`, no tray. Logs to `APPDIR/stayawake.log`.

## Uninstall

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File windows\uninstall.ps1  # admin
# Mac
bash mac/uninstall.sh
# Linux
bash linux/uninstall.sh
```

## Config (core/config.py / windows/mac/linux stayawake.py top)

```python
HOTKEY_STR = "ctrl+shift+space"  # Win/Linux, Mac maps to cmd+shift+space
POLL_INTERVAL_SEC = 12
GRACE_MINUTES = 5  # now immediate for flap shut, kept for docs
BEEP_ENABLED = True
```
Override via CLI: `python windows/stayawake.py --grace 1 --poll 5 --no-beep`

## Troubleshooting

- `powercfg /q` no Lid? `powercfg -attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE`
- Hotkey 1409? Already registered — fallback to `keyboard` lib works, now debounced.
- Lid Data inverted? Check log `Lid state change: Data=...`
- `off.wav` silent start trimmed 0.09s, now 1.6s both — double-click `assets/sounds/off.wav` to test volume.
