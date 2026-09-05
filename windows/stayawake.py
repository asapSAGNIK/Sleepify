#!/usr/bin/env python3
# StayAwake — Spotify-aware lid-close sleep control (Windows)
# See PROJECT_SPEC.md for full spec.
# Single-file, silent background utility. Run elevated (Administrator).
# Requires: winsdk, keyboard (optional fallback), psutil

# ─── CONFIG ──────────────────────────────────────────────────────────────────
HOTKEY_MOD = 0x0002 | 0x0004          # MOD_CONTROL | MOD_SHIFT
HOTKEY_VK = 0x20                       # VK_SPACE
HOTKEY_STR = "ctrl+shift+space"       # for keyboard lib fallback
POLL_INTERVAL_SEC = 12                # spec: 10–15s
GRACE_MINUTES = 5
GRACE_SECONDS = GRACE_MINUTES * 60
BEEP_ENABLED = True
LOG_TO_FILE = True
# powercfg GUIDs
SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
LIDACTION   = "5ca83367-6e45-459f-a27b-476b1d01c936"
# execution state flags
ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
ES_AWAYMODE_REQUIRED= 0x00000040
ES_HOLD   = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
ES_RELEASE= ES_CONTINUOUS
# paths
import os, sys, pathlib
# Ensure core is importable when running as windows/stayawake.py directly
try:
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
except: pass
APPDIR = pathlib.Path(os.environ.get("APPDATA", str(pathlib.Path.home()))) / "StayAwake"
STATE_FILE = APPDIR / "state.json"
LOG_FILE   = APPDIR / "stayawake.log"
# Spotify AUMID
SPOTIFY_AUMID = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
# ─── CLI overrides (e.g., --grace 1 --poll 5 --no-beep) ───────────────────────
for _a in sys.argv[1:]:
    if _a.startswith("--grace="):
        try:
            GRACE_MINUTES = int(_a.split("=",1)[1])
            GRACE_SECONDS = GRACE_MINUTES * 60
        except: pass
    elif _a.startswith("--poll="):
        try: POLL_INTERVAL_SEC = int(_a.split("=",1)[1])
        except: pass
    elif _a == "--no-beep":
        BEEP_ENABLED = False
if "--grace" in sys.argv:
    try: 
        idx = sys.argv.index("--grace")
        GRACE_MINUTES = int(sys.argv[idx+1]); GRACE_SECONDS = GRACE_MINUTES*60
    except: pass
if "--poll" in sys.argv:
    try:
        idx = sys.argv.index("--poll")
        POLL_INTERVAL_SEC = int(sys.argv[idx+1])
    except: pass
# ─────────────────────────────────────────────────────────────────────────────

import ctypes
from ctypes import wintypes
import json
import time
import threading
import logging
import atexit
import signal
import subprocess
import traceback

# ─── LOGGING ─────────────────────────────────────────────────────────────────
APPDIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("StayAwake")
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
# file handler
if LOG_TO_FILE:
    fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
# also console when running interactively (will be hidden in pythonw)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(fmt)
logger.addHandler(ch)

def log(msg, level="info"):
    getattr(logger, level)(msg)

# ─── GLOBAL STATE ────────────────────────────────────────────────────────────
enabled = True                          # toggle via hotkey
lid_closed = False                      # updated by power notification thread
lid_known = False                       # becomes True after first notification
override_active = False
original_ac = None
original_dc = None
spotify_was_playing = False             # true once Spotify has played this session
pause_start_ts = None                   # when paused/stopped after playing
_hotkey_hwnd = None
_exit_flag = threading.Event()

# ─── ELEVATION ───────────────────────────────────────────────────────────────
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def ensure_admin():
    if is_admin():
        return True
    # if --no-elevate passed, skip (for testing without UAC)
    if "--no-elevate" in sys.argv:
        log("Running without admin (test mode) — powercfg writes will fail", "warning")
        return False
    log("Not elevated — relaunching with runas...", "warning")
    try:
        params = " ".join(f'"{a}"' if " " in a else a for a in sys.argv)
        # Use pythonw if available to stay silent; else python
        exe = sys.executable
        # If running via python.exe, restart same
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        sys.exit(0)
    except Exception as e:
        log(f"Elevation failed: {e}", "error")
        return False

def hide_console():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass

# ─── POWER UTILITIES ─────────────────────────────────────────────────────────
# Use powrprof.dll directly; fallback to powercfg subprocess

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]

def _guid_from_str(s: str) -> GUID:
    import uuid
    u = uuid.UUID(s)
    g = GUID()
    g.Data1 = u.time_low
    g.Data2 = u.time_mid
    g.Data3 = u.time_hi_version
    b = u.bytes[8:]
    for i in range(8):
        g.Data4[i] = b[i]
    return g

def _guid_to_str(g: GUID) -> str:
    import uuid
    data4 = bytes(g.Data4)
    # Rebuild uuid from fields (GUID uses little-endian for first 3)
    # uuid.UUID fields param expects big-endian components
    u = uuid.UUID(fields=(g.Data1, g.Data2, g.Data3, data4[0], data4[1], int.from_bytes(data4[2:8], "big")))
    # Actually constructing via bytes_le is simpler: GUID memory layout is exactly uuid bytes_le
    # Use from bytes_le
    # Workaround: pack as bytes_le
    import struct
    packed = struct.pack("<IHH8s", g.Data1, g.Data2, g.Data3, data4)
    return str(uuid.UUID(bytes_le=packed))

_powrprof = ctypes.WinDLL("powrprof.dll")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Function signatures
_powrprof.PowerGetActiveScheme.argtypes = [wintypes.HKEY, ctypes.POINTER(ctypes.POINTER(GUID))]
_powrprof.PowerGetActiveScheme.restype = wintypes.DWORD
_powrprof.PowerReadACValueIndex.argtypes = [wintypes.HKEY, ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(wintypes.DWORD)]
_powrprof.PowerReadACValueIndex.restype = wintypes.DWORD
_powrprof.PowerReadDCValueIndex.argtypes = [wintypes.HKEY, ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(wintypes.DWORD)]
_powrprof.PowerReadDCValueIndex.restype = wintypes.DWORD
_powrprof.PowerWriteACValueIndex.argtypes = [wintypes.HKEY, ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(GUID), wintypes.DWORD]
_powrprof.PowerWriteACValueIndex.restype = wintypes.DWORD
_powrprof.PowerWriteDCValueIndex.argtypes = [wintypes.HKEY, ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(GUID), wintypes.DWORD]
_powrprof.PowerWriteDCValueIndex.restype = wintypes.DWORD
_powrprof.PowerSetActiveScheme.argtypes = [wintypes.HKEY, ctypes.POINTER(GUID)]
_powrprof.PowerSetActiveScheme.restype = wintypes.DWORD

def _local_free(ptr):
    # LocalFree is in kernel32
    try:
        ctypes.windll.kernel32.LocalFree(ptr)
    except Exception:
        pass

def get_active_scheme_guid():
    """Returns POINTER(GUID) that caller must LocalFree, and the GUID str."""
    pguid = ctypes.POINTER(GUID)()
    res = _powrprof.PowerGetActiveScheme(None, ctypes.byref(pguid))
    if res != 0 or not pguid:
        log(f"PowerGetActiveScheme failed: {res}", "error")
        return None, None
    # copy string for logging
    guid_str = _guid_to_str(pguid.contents)
    return pguid, guid_str

def read_lid_values():
    """Read current AC/DC lid action index values. Returns (ac, dc) or (None,None)."""
    pguid, guid_str = get_active_scheme_guid()
    if not pguid:
        return None, None
    try:
        sub = _guid_from_str(SUB_BUTTONS)
        lid = _guid_from_str(LIDACTION)
        ac_val = wintypes.DWORD()
        dc_val = wintypes.DWORD()
        r1 = _powrprof.PowerReadACValueIndex(None, pguid, ctypes.byref(sub), ctypes.byref(lid), ctypes.byref(ac_val))
        r2 = _powrprof.PowerReadDCValueIndex(None, pguid, ctypes.byref(sub), ctypes.byref(lid), ctypes.byref(dc_val))
        if r1 != 0 or r2 != 0:
            log(f"PowerRead lid failed AC={r1} DC={r2}", "error")
            # fallback to powercfg parsing
            return _read_lid_via_powercfg()
        log(f"Read lid values AC={ac_val.value} DC={dc_val.value} (scheme {guid_str})")
        return int(ac_val.value), int(dc_val.value)
    finally:
        _local_free(pguid)

def _read_lid_via_powercfg():
    try:
        r = subprocess.run(["powercfg", "/q", "SCHEME_CURRENT", "SUB_BUTTONS"], capture_output=True, text=True, timeout=8)
        # After -attributes -ATTRIB_HIDE, lid should be visible. Parse.
        # Find LIDACTION section
        txt = r.stdout
        # simplistic parse: find "Lid close action" then next "Current AC" lines
        ac = dc = None
        lines = txt.splitlines()
        in_lid = False
        for line in lines:
            if "5ca83367" in line or "Lid close action" in line or "LIDACTION" in line:
                in_lid = True
            if in_lid:
                if "Current AC" in line:
                    try:
                        ac = int(line.split(":")[-1].strip(), 16)
                    except:
                        pass
                if "Current DC" in line:
                    try:
                        dc = int(line.split(":")[-1].strip(), 16)
                    except:
                        pass
                    break
        if ac is not None and dc is not None:
            log(f"Fallback powercfg read AC={ac} DC={dc}")
            return ac, dc
    except Exception as e:
        log(f"powercfg fallback read failed: {e}", "error")
    return None, None

def write_lid_values(ac_val: int, dc_val: int) -> bool:
    """Write AC/DC lid values for active scheme. Returns True on success."""
    pguid, guid_str = get_active_scheme_guid()
    if not pguid:
        return _write_lid_via_powercfg(ac_val, dc_val)
    try:
        sub = _guid_from_str(SUB_BUTTONS)
        lid = _guid_from_str(LIDACTION)
        r1 = _powrprof.PowerWriteACValueIndex(None, pguid, ctypes.byref(sub), ctypes.byref(lid), wintypes.DWORD(ac_val))
        r2 = _powrprof.PowerWriteDCValueIndex(None, pguid, ctypes.byref(sub), ctypes.byref(lid), wintypes.DWORD(dc_val))
        if r1 != 0 or r2 != 0:
            log(f"PowerWrite lid failed AC={r1} DC={r2}, trying powercfg fallback", "warning")
            _local_free(pguid)
            return _write_lid_via_powercfg(ac_val, dc_val)
        # Apply
        r3 = _powrprof.PowerSetActiveScheme(None, pguid)
        if r3 != 0:
            log(f"PowerSetActiveScheme failed {r3}", "warning")
        else:
            log(f"Wrote lid AC={ac_val} DC={dc_val} and re-activated scheme {guid_str}")
        return True
    finally:
        try:
            _local_free(pguid)
        except:
            pass

def _write_lid_via_powercfg(ac_val: int, dc_val: int) -> bool:
    try:
        # powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION <val>
        for val, flag in [(ac_val, "/setacvalueindex"), (dc_val, "/setdcvalueindex")]:
            cmd = ["powercfg", flag, "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION", str(val)]
            # also try with raw GUIDs if alias fails
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if r.returncode != 0:
                # try GUID form
                cmd2 = ["powercfg", flag, "SCHEME_CURRENT", SUB_BUTTONS, LIDACTION, str(val)]
                r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=8)
                if r2.returncode != 0:
                    log(f"powercfg {flag} failed: {r.stderr} / {r2.stderr}", "error")
                    return False
        # activate
        subprocess.run(["powercfg", "/setactive", "SCHEME_CURRENT"], capture_output=True, timeout=8)
        log(f"powercfg fallback wrote AC={ac_val} DC={dc_val}")
        return True
    except Exception as e:
        log(f"powercfg write exception: {e}", "error")
        return False

# execution state
def hold_awake():
    res = _kernel32.SetThreadExecutionState(ES_HOLD)
    log(f"SetThreadExecutionState HOLD -> {hex(res & 0xFFFFFFFF) if res else '0'}")
    return res

def release_awake():
    res = _kernel32.SetThreadExecutionState(ES_RELEASE)
    log(f"SetThreadExecutionState RELEASE -> {hex(res & 0xFFFFFFFF) if res else '0'}")
    return res

def do_sleep():
    """Force sleep via SetSuspendState. Returns True if attempted."""
    try:
        # BOOL SetSuspendState(BOOL Hibernate, BOOL ForceCritical, BOOL DisableWakeEvent)
        powr = ctypes.WinDLL("powrprof.dll")
        powr.SetSuspendState.argtypes = [wintypes.BOOL, wintypes.BOOL, wintypes.BOOL]
        powr.SetSuspendState.restype = wintypes.BOOL
        log("Forcing sleep via SetSuspendState(0,0,0)...", "warning")
        res = powr.SetSuspendState(False, False, False)
        if not res:
            err = ctypes.get_last_error()
            log(f"SetSuspendState returned 0, last error {err}", "error")
            # fallback: powercfg?
            subprocess.run(["powercfg", "/hibernate", "off"], capture_output=True)
            # try rundll32
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], capture_output=True)
        return bool(res)
    except Exception as e:
        log(f"do_sleep exception: {e}\n{traceback.format_exc()}", "error")
        return False

# ─── STATE PERSISTENCE (crash recovery) ──────────────────────────────────────
def save_state():
    try:
        APPDIR.mkdir(parents=True, exist_ok=True)
        data = {
            "original_ac": original_ac,
            "original_dc": original_dc,
            "override_active": override_active,
            "enabled": enabled,
            "spotify_was_playing": spotify_was_playing,
            "timestamp": time.time(),
        }
        # write atomically
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
        log(f"State saved: {data}")
    except Exception as e:
        log(f"save_state failed: {e}", "error")

def load_state():
    global original_ac, original_dc, override_active, enabled, spotify_was_playing
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        log(f"Loaded state file: {data}")
        return data
    except Exception as e:
        log(f"load_state failed: {e}", "error")
        return None

def clear_state_marker():
    try:
        if STATE_FILE.exists():
            # update to override_active=False but keep original for audit
            if original_ac is not None:
                save_state()  # will save override_active=False
            else:
                STATE_FILE.unlink()
                log("State marker cleared (file removed)")
    except Exception as e:
        log(f"clear_state_marker failed: {e}", "error")

def startup_recovery():
    """If previous run died mid-override (marker says override_active), restore original.
    MUST be called BEFORE capture_original_lid so we don't overwrite the saved original
    with the stuck '0' value. Returns True if recovery was performed."""
    global original_ac, original_dc, override_active, enabled
    if not STATE_FILE.exists():
        log("No previous state file — clean start")
        return False
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        log(f"Loaded state for recovery: {data}")
    except Exception as e:
        log(f"startup_recovery load failed: {e}", "error")
        return False
    prev_override = data.get("override_active")
    prev_ac = data.get("original_ac")
    prev_dc = data.get("original_dc")
    if prev_override and prev_ac is not None and prev_dc is not None:
        log(f"Crash recovery: previous run died with override active (lid was 0). Restoring original AC={prev_ac} DC={prev_dc}", "warning")
        success = write_lid_values(prev_ac, prev_dc)
        release_awake()
        if success:
            log("Crash recovery restore succeeded")
        else:
            log("Crash recovery restore FAILED — will retry periodically", "error")
        original_ac = prev_ac
        original_dc = prev_dc
        override_active = False
        enabled = True  # always restart enabled, not stuck disabled
        try:
            APPDIR.mkdir(parents=True, exist_ok=True)
            new_data = {
                "original_ac": prev_ac,
                "original_dc": prev_dc,
                "override_active": False,
                "enabled": True,
                "spotify_was_playing": False,
                "timestamp": time.time(),
            }
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
            tmp.replace(STATE_FILE)
        except Exception as e:
            log(f"recovery save failed: {e}", "error")
        return True
    else:
        log("Previous state showed no override — no recovery needed")
        # Ensure enabled is True for fresh session even if no recovery
        if not data.get("enabled", True):
            log("Resetting enabled from persisted False to True for fresh session", "info")
            enabled = True
            try:
                data["enabled"] = True
                data["timestamp"] = time.time()
                tmp = STATE_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                tmp.replace(STATE_FILE)
            except Exception as e:
                log(f"enabled reset save failed: {e}", "error")
        return False

# ─── BEEP / FEEDBACK ─────────────────────────────────────────────────────────
def _play_sound(path):
    """Try custom wav via winsound PlaySound, fallback to Beep."""
    try:
        p = pathlib.Path(path)
        if p.exists():
            import winsound
            winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            log(f"Played custom sound {p}")
            return True
    except Exception as e:
        log(f"custom sound failed {path}: {e}", "warning")
    return False

def beep_on():
    if not BEEP_ENABLED:
        return
    # Try custom on.wav first (assets/sounds/on.wav) — handle core import failure separately
    try:
        from core.config import ON_SOUND
        if _play_sound(ON_SOUND):
            return
    except Exception as e:
        log(f"beep_on core import failed, trying alt path: {e}", "warning")
    try:
        alt = pathlib.Path(__file__).parent.parent / "assets" / "sounds" / "on.wav"
        if _play_sound(alt):
            return
    except Exception as e:
        log(f"beep_on alt path failed: {e}", "warning")
    try:
        import winsound
        winsound.Beep(1000, 150)
        time.sleep(0.08)
        winsound.Beep(1500, 150)
    except Exception as e:
        log(f"beep_on failed: {e}", "error")

def beep_off():
    if not BEEP_ENABLED:
        return
    try:
        from core.config import OFF_SOUND
        if _play_sound(OFF_SOUND):
            return
    except Exception as e:
        log(f"beep_off core import failed, trying alt path: {e}", "warning")
    try:
        alt = pathlib.Path(__file__).parent.parent / "assets" / "sounds" / "off.wav"
        if _play_sound(alt):
            return
    except Exception as e:
        log(f"beep_off alt path failed: {e}", "warning")
    try:
        import winsound
        winsound.Beep(600, 400)
    except Exception as e:
        log(f"beep_off failed: {e}", "error")

def beep_error():
    try:
        import winsound
        winsound.Beep(400, 600)
    except:
        pass

# ─── SPOTIFY DETECTION (GSMTC) ───────────────────────────────────────────────
# Use winsdk. Poll async.

_playback_status_cache = None

async def _async_spotify_status():
    try:
        import winsdk.windows.media.control as smtc
        mgr = await smtc.GlobalSystemMediaTransportControlsSessionManager.request_async()
        if mgr is None:
            return None
        # Filter to Spotify session specifically
        sessions = mgr.get_sessions()
        for sess in sessions:
            try:
                aumid = sess.source_app_user_model_id
                if aumid and "spotify" in aumid.lower():
                    info = sess.get_playback_info()
                    if info:
                        return info.playback_status  # enum int 0-5
            except Exception:
                continue
        # No Spotify session found -> not playing (paused/stopped/never opened)
        return None
    except Exception as e:
        log(f"Spotify detection async error: {e}", "error")
        return None

def get_spotify_playback_status_sync():
    """Synchronous wrapper. Returns playback_status enum value or None."""
    try:
        import asyncio
        # Need to handle that asyncio.run may fail if loop already exists
        # Use new loop run
        return asyncio.run(_async_spotify_status())
    except RuntimeError as e:
        # fallback: create new loop
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            res = loop.run_until_complete(_async_spotify_status())
            loop.close()
            return res
        except Exception as e2:
            log(f"Spotify sync fallback error: {e2}", "error")
            return None
    except Exception as e:
        log(f"get_spotify_playback_status_sync error: {e}", "error")
        return None

def is_spotify_running():
    """Check if Spotify.exe process exists (app open vs closed)."""
    try:
        import psutil
        for p in psutil.process_iter(['name']):
            try:
                n = (p.info.get('name') or '').lower()
                if 'spotify' in n:
                    return True
            except:
                continue
        return False
    except Exception:
        # fallback: assume running if we can't check
        return True

def is_spotify_playing():
    """Returns True if Spotify is actively playing, False if paused/stopped/not found, None on error.
    Also logs raw status for debugging."""
    try:
        status = get_spotify_playback_status_sync()
        if status is None:
            # No session - check if app is actually closed vs just paused
            running = is_spotify_running()
            log(f"Spotify no session (app_running={running}) => is_playing=False")
            return False
        val = int(status)
        try:
            import winsdk.windows.media.control as smtc
            PLAYING = int(smtc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING)
            PAUSED = int(smtc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PAUSED)
            STOPPED = int(smtc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.STOPPED)
            is_play = (val == PLAYING)
            # log with name
            try:
                name = smtc.GlobalSystemMediaTransportControlsSessionPlaybackStatus(val).name
            except:
                name = str(val)
            log(f"Spotify status {name} ({val}) => is_playing={is_play} app_running={is_spotify_running()}")
            return is_play
        except Exception:
            is_play = (val == 4)
            log(f"Spotify status val {val} => is_playing={is_play} (hardcoded) app_running={is_spotify_running()}")
            return is_play
    except Exception as e:
        log(f"is_spotify_playing exception: {e}", "error")
        return None

def get_spotify_raw_status():
    """Returns raw playback_status int or None if no session. Also returns app_running."""
    try:
        status = get_spotify_playback_status_sync()
        if status is None:
            return None, is_spotify_running()
        return int(status), is_spotify_running()
    except:
        return None, is_spotify_running()

# ─── LID MONITOR (RegisterPowerSettingNotification) ──────────────────────────
# Hidden window that receives WM_POWERBROADCAST / WM_HOTKEY

GUID_LIDSWITCH_STATE_CHANGE_STR = "BA3E0F4D-B817-4094-A2D1-D56379E6A0F3"
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
WM_HOTKEY = 0x0312
DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000
HOTKEY_ID = 0x5AFE  # arbitrary

# For ctypes WNDPROC — must use LRESULT (LONG_PTR) on 64-bit
WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [
        ("PowerSetting", GUID),
        ("DataLength", wintypes.DWORD),
        ("Data", wintypes.BYTE * 1),  # variable
    ]

def _lid_monitor_thread():
    global lid_closed, lid_known, enabled, override_active, original_ac, original_dc, _hotkey_hwnd
    import uuid

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Fix 64-bit DefWindowProc signature (LPARAM is LONG_PTR, not int)
    try:
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.RegisterClassW.argtypes = [ctypes.c_void_p]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]
        user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
    except Exception:
        pass

    # Define window class
    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROCTYPE),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HANDLE),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    def wndproc(hwnd, msg, wparam, lparam):
        global lid_closed, lid_known, enabled, override_active, pause_start_ts, _last_toggle_ts, _fallback_active
        # If fallback is active, ignore WM_HOTKEY to avoid double toggle
        if _fallback_active and msg == WM_HOTKEY:
            return 0
        if msg == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE:
            try:
                pbs = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                guid_str = _guid_to_str(pbs.PowerSetting)
                if guid_str.lower() == GUID_LIDSWITCH_STATE_CHANGE_STR.lower():
                    data_byte = pbs.Data[0] if pbs.DataLength >= 1 else 0
                    new_closed = (data_byte == 0)
                    old = lid_closed
                    lid_closed = new_closed
                    lid_known = True
                    log(f"Lid state change: Data={data_byte} => lid_closed={lid_closed} (was {old})", "info")
            except Exception as e:
                log(f"WndProc power setting error: {e}", "error")
            return 1  # TRUE
        elif msg == WM_HOTKEY and wparam == HOTKEY_ID:
            try:
                now = time.time()
                if now - _last_toggle_ts < 0.7:
                    return 0
                _last_toggle_ts = now
                enabled = not enabled
                log(f"Hotkey toggle: enabled now {enabled}", "warning")
                save_state()
                if enabled:
                    beep_on()
                else:
                    beep_off()
                    if override_active:
                        log("Hotkey disabled — restoring lid & releasing awake", "warning")
                        if original_ac is not None:
                            write_lid_values(original_ac, original_dc)
                        release_awake()
                        override_active = False
                        pause_start_ts = None
                        save_state()
            except Exception as e:
                log(f"Hotkey handling error: {e}\n{traceback.format_exc()}", "error")
            return 0
        elif msg == 0x0010:  # WM_CLOSE
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wndproc_cb = WNDPROCTYPE(wndproc)

    hInstance = kernel32.GetModuleHandleW(None)
    className = "StayAwakeHiddenWindow"
    wndClass = WNDCLASS()
    wndClass.style = 0
    wndClass.lpfnWndProc = wndproc_cb
    wndClass.cbClsExtra = 0
    wndClass.cbWndExtra = 0
    wndClass.hInstance = hInstance
    wndClass.hIcon = 0
    wndClass.hCursor = 0
    wndClass.hbrBackground = 0
    wndClass.lpszMenuName = None
    wndClass.lpszClassName = className

    # Register class
    atom = user32.RegisterClassW(ctypes.byref(wndClass))
    if not atom and ctypes.get_last_error() != 1410:  # ERROR_CLASS_ALREADY_EXISTS
        log(f"RegisterClassW failed: {ctypes.get_last_error()}", "error")
        # continue anyway
    # Create window (message-only window: HWND_MESSAGE = -3)
    HWND_MESSAGE = wintypes.HWND(-3)
    hwnd = user32.CreateWindowExW(
        0, className, "StayAwake",
        0, 0, 0, 0, 0,
        HWND_MESSAGE, 0, hInstance, None
    )
    if not hwnd:
        # fallback to invisible overlapped
        hwnd = user32.CreateWindowExW(0, className, "StayAwake", 0, 0, 0, 0, 0, None, 0, hInstance, None)
    if not hwnd:
        log(f"CreateWindowExW failed: {ctypes.get_last_error()}", "error")
        # Fallback keyboard hotkey only thread
        _fallback_hotkey_thread()
        return
    _hotkey_hwnd = hwnd
    log(f"Hidden window created hwnd={hwnd}")

    # Register for lid notification
    try:
        guid_lid = _guid_from_str(GUID_LIDSWITCH_STATE_CHANGE_STR)
        hnotify = ctypes.windll.user32.RegisterPowerSettingNotification(hwnd, ctypes.byref(guid_lid), DEVICE_NOTIFY_WINDOW_HANDLE)
        if not hnotify:
            log(f"RegisterPowerSettingNotification failed: {ctypes.get_last_error()}", "error")
        else:
            log(f"Registered for lid notifications: {hnotify}")
    except Exception as e:
        log(f"RegisterPowerSettingNotification exception: {e}", "error")

    # Register hotkey
    try:
        ok = user32.RegisterHotKey(hwnd, HOTKEY_ID, HOTKEY_MOD, HOTKEY_VK)
        if not ok:
            err = ctypes.get_last_error()
            log(f"RegisterHotKey failed: {err} — falling back to keyboard lib", "warning")
            # fallback thread
            threading.Thread(target=_fallback_hotkey_thread, daemon=True).start()
        else:
            log(f"Registered hotkey {HOTKEY_STR} (id {HOTKEY_ID}) on hwnd {hwnd}")
    except Exception as e:
        log(f"RegisterHotKey exception: {e}", "error")
        threading.Thread(target=_fallback_hotkey_thread, daemon=True).start()

    # Also attempt to get initial lid state via undocumented call or assume open.
    # We could try to query via CallNtPowerInformation? For now leave as False/open until notification.
    # Optionally, try to infer from powercfg /battery? Not.

    # Message loop
    msg = wintypes.MSG()
    log("Lid monitor message loop starting")
    while not _exit_flag.is_set():
        # Use PeekMessage-style with timeout to allow exit flag check
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret == 0:  # WM_QUIT
            break
        if ret == -1:
            log(f"GetMessageW error {ctypes.get_last_error()}", "error")
            time.sleep(0.5)
            continue
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    log("Lid monitor thread exiting, cleaning up")
    try:
        user32.UnregisterHotKey(hwnd, HOTKEY_ID)
    except:
        pass
    try:
        if 'hnotify' in locals() and hnotify:
            user32.UnregisterPowerSettingNotification(hnotify)
    except:
        pass
    user32.DestroyWindow(hwnd)
    user32.UnregisterClassW(className, hInstance)

_last_toggle_ts = 0
_fallback_active = False
def _fallback_hotkey_thread():
    """Fallback using `keyboard` library if RegisterHotKey fails (e.g., no hwnd or conflict)."""
    global _fallback_active
    _fallback_active = True
    try:
        import keyboard
        log("Fallback hotkey thread using `keyboard` library")

        def on_hotkey():
            global enabled, override_active, pause_start_ts, _last_toggle_ts
            now = time.time()
            if now - _last_toggle_ts < 0.7:
                return
            _last_toggle_ts = now
            enabled = not enabled
            log(f"[fallback] Hotkey toggle enabled={enabled}", "warning")
            save_state()
            if enabled:
                beep_on()
            else:
                beep_off()
                if override_active:
                    if original_ac is not None:
                        write_lid_values(original_ac, original_dc)
                    release_awake()
                    override_active = False
                    pause_start_ts = None
                    save_state()

        keyboard.add_hotkey(HOTKEY_STR, on_hotkey, suppress=True, trigger_on_release=False)
        log(f"keyboard.add_hotkey registered: {HOTKEY_STR} (suppress=True)")
        keyboard.wait()
    except Exception as e:
        log(f"Fallback hotkey thread failed (keyboard lib): {e}\n{traceback.format_exc()}", "error")

def start_lid_monitor():
    t = threading.Thread(target=_lid_monitor_thread, name="LidMonitor", daemon=True)
    t.start()
    # also start fallback if needed after short delay — lid thread itself does it
    return t

# ─── CORE LOGIC ───────────────────────────────────────────────────────────────
def capture_original_lid():
    global original_ac, original_dc
    ac, dc = read_lid_values()
    if ac is None or dc is None:
        log("Failed to capture original lid values — defaulting to Sleep (1,1)", "warning")
        ac, dc = 1, 1  # default Sleep
    original_ac, original_dc = ac, dc
    log(f"Captured original lid AC={ac} DC={dc}")
    save_state()
    return ac, dc

def apply_override():
    global override_active
    if override_active:
        return True
    log("Applying override: lid=Do nothing (0,0) + hold awake", "warning")
    ok = write_lid_values(0, 0)
    if not ok:
        log("Failed to set lid to Do nothing — hold awake still applied but lid may still sleep!", "error")
        beep_error()
        # still set execution state though
    hold_awake()
    override_active = True
    save_state()
    log("Override ACTIVE")
    return ok

def restore_original():
    global override_active, pause_start_ts
    if not override_active:
        # ensure execution state released anyway
        release_awake()
        return True
    ac = original_ac if original_ac is not None else 1
    dc = original_dc if original_dc is not None else 1
    log(f"Restoring original lid AC={ac} DC={dc} + releasing awake", "warning")
    ok = write_lid_values(ac, dc)
    release_awake()
    override_active = False
    pause_start_ts = None
    save_state()
    if not ok:
        log("Restore lid FAILED", "error")
        beep_error()
    else:
        log("Restore succeeded — lid action back to original")
    return ok

def handle_poll_result(is_playing: bool):
    global spotify_was_playing, pause_start_ts, override_active, enabled
    if not enabled:
        if override_active:
            restore_original()
        return

    # Get raw status to distinguish app-closed vs paused
    raw_status, app_running = get_spotify_raw_status()

    if is_playing:
        if not spotify_was_playing:
            log("Spotify first playing detected this session", "info")
        spotify_was_playing = True
        pause_start_ts = None
        save_state()
        if not override_active:
            apply_override()
        return

    # Not playing
    if not spotify_was_playing:
        if override_active:
            log("Spotify never played but override active — restoring (should not happen)", "warning")
            restore_original()
        return

    # Was playing before, now not playing
    # App closed → immediate restore (no grace) — user wants code to stop when Spotify closed
    if raw_status is None and not app_running:
        log("Spotify app closed — restoring immediately (code stops)", "info")
        if override_active:
            restore_original()
            if lid_closed and lid_known:
                log("App closed + lid CLOSED — forcing sleep (normal sleep)", "warning")
                time.sleep(1)
                do_sleep()
            else:
                log(f"App closed + lid OPEN — not sleeping, back to default (code stopped)", "info")
        pause_start_ts = None
        # Reset was_playing so next Spotify launch is fresh session
        spotify_was_playing = False
        save_state()
        return

    # App open but no playback (paused/stopped) — user wants immediate sleep if flap shut
    if not app_running:
        # Should not happen (already handled above), but fallback
        if override_active:
            restore_original()
        pause_start_ts = None
        save_state()
        return

    # App open, flap shut + no playback → immediate normal sleep (no 3-min grace)
    if lid_closed and lid_known:
        log("No playback + flap shut (lid CLOSED) — restoring immediately, normal sleep (code stops)", "info")
        if override_active:
            restore_original()
            time.sleep(1)
            do_sleep()
        pause_start_ts = None
        save_state()
        return

    # App open, flap open + no playback → silent stop (restore lid, no sleep, back to default)
    # No grace when flap open — just restore and let Windows behave normally
    if override_active:
        log("No playback + flap open — restoring lid to default, code stops (no sleep, will sleep on next lid close)", "info")
        restore_original()
    pause_start_ts = None
    save_state()
    return

def self_healing_check():
    """Periodic check that current lid matches expected state. Fix if stuck."""
    try:
        cur_ac, cur_dc = read_lid_values()
        if cur_ac is None:
            return
        # If override should be active but lid is not 0: re-apply
        if override_active:
            if cur_ac != 0 or cur_dc != 0:
                log(f"Self-healing: override should be ACTIVE but lid is AC={cur_ac} DC={cur_dc} — re-applying override", "warning")
                write_lid_values(0, 0)
                hold_awake()
        else:
            # Not overridden: lid should be original
            if original_ac is not None and (cur_ac != original_ac or cur_dc != original_dc):
                # Only fix if it's stuck at 0 (Do nothing) — that is the dangerous stuck case
                # If user manually changed lid setting while we are idle, we should NOT overwrite their change
                # But if we crashed previously and state file was lost, cur may be 0 unexpectedly.
                # Heuristic: if cur==0 and original !=0, it's likely our stuck override -> fix
                if cur_ac == 0 and original_ac != 0:
                    log(f"Self-healing: lid stuck at Do nothing (0) while override INACTIVE (original AC={original_ac}) — restoring", "warning")
                    write_lid_values(original_ac, original_dc)
                    release_awake()
                else:
                    # User changed setting, update our captured original to match their new preference?
                    # Spec says capture original at startup, not continuously. But if user changes while idle, we should respect it.
                    # Update captured original to current, so future restore uses new value.
                    if cur_ac != original_ac or cur_dc != original_dc:
                        log(f"Self-healing: lid differs from captured original (cur AC={cur_ac} DC={cur_dc} vs orig AC={original_ac} DC={original_dc}) — user may have changed. Updating captured original.", "info")
                        # Do not auto-overwrite; just update our memory so next override/restore is correct
                        # globals
                        globals()["original_ac"] = cur_ac
                        globals()["original_dc"] = cur_dc
                        save_state()
    except Exception as e:
        log(f"self_healing_check error: {e}", "error")

# ─── SIGNAL / EXIT HANDLING ──────────────────────────────────────────────────
def cleanup_on_exit():
    log("cleanup_on_exit called", "warning")
    _exit_flag.set()
    # Try to restore if override active - best effort
    try:
        if override_active:
            log("Exit: restoring lid before exit", "warning")
            if original_ac is not None:
                write_lid_values(original_ac, original_dc)
            release_awake()
            globals()["override_active"] = False
            save_state()
    except Exception as e:
        log(f"cleanup failed: {e}", "error")
    # Post quit to lid window if exists
    try:
        if _hotkey_hwnd:
            ctypes.windll.user32.PostMessageW(_hotkey_hwnd, 0x0010, 0, 0)  # WM_CLOSE
    except:
        pass

def signal_handler(sig, frame):
    log(f"Signal {sig} received", "warning")
    cleanup_on_exit()
    sys.exit(0)

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────
def main():
    log("="*60)
    log("StayAwake starting — Spotify-aware lid-close control")
    log(f"Config: poll={POLL_INTERVAL_SEC}s grace={GRACE_MINUTES}min hotkey={HOTKEY_STR} beep={BEEP_ENABLED}")
    log(f"AppDir: {APPDIR} State: {STATE_FILE} Log: {LOG_FILE}")

    # Ensure admin
    if not ensure_admin():
        log("Continuing without admin — lid override will likely fail (testing mode)", "warning")

    hide_console()

    # Crash recovery MUST run before capturing fresh original, otherwise
    # we would overwrite the saved true original with the stuck 0 value.
    startup_recovery()

    # Capture original lid values at startup (spec: read user's actual prior setting)
    # If recovery just restored, this will read the now-correct value.
    capture_original_lid()

    # Fresh session: reset was_playing and enabled (hotkey toggle is per-session)
    global spotify_was_playing, pause_start_ts, enabled
    if spotify_was_playing:
        log(f"Resetting spotify_was_playing from persisted {spotify_was_playing} to False for fresh session", "info")
        spotify_was_playing = False
        pause_start_ts = None
        save_state()
    if not enabled:
        log(f"Resetting enabled from persisted False to True for fresh session (press {HOTKEY_STR} to toggle)", "info")
        enabled = True
        save_state()
        beep_on()
        log("Tool re-enabled on startup — was previously disabled via hotkey", "warning")

    # Set up exit handlers
    atexit.register(cleanup_on_exit)
    # SIGINT/SIGTERM
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        # SIGBREAK on Windows
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, signal_handler)
    except Exception as e:
        log(f"signal setup failed: {e}", "error")

    # Start lid monitor + hotkey thread
    lid_thread = start_lid_monitor()
    time.sleep(0.5)  # let window register

    # Ensure lid attribute is visible for powercfg debugging (optional)
    try:
        subprocess.run(["powercfg", "-attributes", "SUB_BUTTONS", LIDACTION, "-ATTRIB_HIDE"], capture_output=True, timeout=3)
    except:
        pass

    # Initial beep to confirm start? Spec: toggle beep only. Don't beep on start to stay silent.
    log(f"Entering main poll loop (silent, no window) — hotkey {HOTKEY_STR} to toggle")
    log("Initial state: enabled=True, lid_closed=False (assumed open until notification), override=False")

    # Also ensure execution state is released at start
    release_awake()

    poll_count = 0
    last_heal = time.time()
    try:
        while not _exit_flag.is_set():
            poll_count += 1
            try:
                # Spotify lifecycle: tool is idle when Spotify not running (code stopped, no cost)
                # Adaptive poll: 12s when Spotify running, 60s when not — saves power
                if not is_spotify_running():
                    if poll_count % 5 == 1:  # log occasionally when idle
                        log("Spotify not running — tool idle (code stopped), normal sleep, polling every 60s", "info")
                    # Ensure no override when idle
                    if override_active:
                        restore_original()
                    # Sleep 60s but check every 5s for quick resume when Spotify starts
                    for _ in range(60*10):
                        if _exit_flag.is_set():
                            break
                        if _ % 50 == 0 and is_spotify_running():
                            log("Spotify detected — resuming active poll", "info")
                            break
                        time.sleep(0.1)
                    continue

                is_playing = is_spotify_playing()
                if is_playing is None:
                    log("Spotify polling returned None (error) — treating as not playing", "warning")
                    is_playing = False
                handle_poll_result(bool(is_playing))
            except Exception as e:
                log(f"Poll error: {e}\n{traceback.format_exc()}", "error")

            if time.time() - last_heal > 60:
                self_healing_check()
                last_heal = time.time()

            for _ in range(POLL_INTERVAL_SEC * 10):
                if _exit_flag.is_set():
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        log("KeyboardInterrupt — exiting")
    finally:
        cleanup_on_exit()
        log("StayAwake exited")
        # Ensure file handlers flushed
        logging.shutdown()

if __name__ == "__main__":
    main()
