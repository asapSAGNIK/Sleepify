#!/usr/bin/env python3
# StayAwake — Spotify-aware lid-close sleep control (Linux)
# Mirrors Windows logic exactly; platform layer uses systemd-inhibit + playerctl + /proc/acpi

HOTKEY_STR = "ctrl+shift+space"
POLL_INTERVAL_SEC = 12
GRACE_MINUTES = 5
GRACE_SECONDS = GRACE_MINUTES * 60
BEEP_ENABLED = True
LOG_TO_FILE = True

import os, sys, pathlib
try: sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
except: pass
xdg = os.environ.get("XDG_CONFIG_HOME", str(pathlib.Path.home() / ".config"))
APPDIR = pathlib.Path(xdg) / "StayAwake"
STATE_FILE = APPDIR / "state.json"
LOG_FILE = APPDIR / "stayawake.log"

for _a in sys.argv[1:]:
    if _a.startswith("--grace="):
        try: GRACE_MINUTES = int(_a.split("=",1)[1]); GRACE_SECONDS = GRACE_MINUTES*60
        except: pass
    elif _a.startswith("--poll="):
        try: POLL_INTERVAL_SEC = int(_a.split("=",1)[1])
        except: pass
    elif _a == "--no-beep": BEEP_ENABLED = False
if "--grace" in sys.argv:
    try: GRACE_MINUTES = int(sys.argv[sys.argv.index("--grace")+1]); GRACE_SECONDS=GRACE_MINUTES*60
    except: pass
if "--poll" in sys.argv:
    try: POLL_INTERVAL_SEC = int(sys.argv[sys.argv.index("--poll")+1])
    except: pass

import json, time, threading, logging, atexit, signal, subprocess, traceback

APPDIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("StayAwake")
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
if LOG_TO_FILE:
    fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    fh.setFormatter(fmt); logger.addHandler(fh)
ch = logging.StreamHandler(sys.stdout); ch.setFormatter(fmt); logger.addHandler(ch)
def log(msg, level="info"): getattr(logger, level)(msg)

enabled = True
lid_closed = False
lid_known = False
override_active = False
original_ac = None
original_dc = None
spotify_was_playing = False
pause_start_ts = None
_exit_flag = threading.Event()
_inhibit_proc = None

def is_admin(): return True
def ensure_admin(): return True
def hide_console(): pass

# ─── POWER (Linux) ───
def read_lid_values():
    return (0,0) if _inhibit_proc and _inhibit_proc.poll() is None else (1,1)
def write_lid_values(ac_val, dc_val): return True
def hold_awake():
    global _inhibit_proc
    if _inhibit_proc and _inhibit_proc.poll() is None: return True
    try:
        # systemd-inhibit for lid switch + idle + suspend
        _inhibit_proc = subprocess.Popen(
            ["systemd-inhibit", "--what=handle-lid-switch:handle-lid-switch:sleep:idle", "--who=StayAwake", "--why=Spotify playing", "--mode=block", "sleep", "infinity"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        log(f"systemd-inhibit started pid={_inhibit_proc.pid}")
        return True
    except Exception as e:
        log(f"hold_awake systemd-inhibit failed: {e}, trying xdg-screensaver", "warning")
        try:
            _inhibit_proc = subprocess.Popen(["sleep", "infinity"])
            log("fallback hold via sleep infinity")
            return True
        except Exception as e2:
            log(f"hold_awake fallback failed: {e2}", "error")
            return False

def release_awake():
    global _inhibit_proc
    try:
        if _inhibit_proc and _inhibit_proc.poll() is None:
            _inhibit_proc.terminate()
            try: _inhibit_proc.wait(timeout=2)
            except: _inhibit_proc.kill()
            log("systemd-inhibit stopped")
        _inhibit_proc=None
        return True
    except Exception as e:
        log(f"release_awake failed: {e}", "error")
        return False

def do_sleep():
    try:
        log("Forcing sleep via systemctl suspend", "warning")
        subprocess.run(["systemctl", "suspend"], timeout=5)
        return True
    except Exception as e:
        log(f"do_sleep failed: {e}", "error")
        try: subprocess.run(["loginctl", "suspend"], timeout=5)
        except: pass
        return False

def save_state():
    try:
        APPDIR.mkdir(parents=True, exist_ok=True)
        data = {"original_ac": original_ac, "original_dc": original_dc, "override_active": override_active, "enabled": enabled, "spotify_was_playing": spotify_was_playing, "timestamp": time.time()}
        tmp = STATE_FILE.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=2), encoding="utf-8"); tmp.replace(STATE_FILE)
        log(f"State saved: {data}")
    except Exception as e: log(f"save_state failed: {e}", "error")

def startup_recovery():
    global original_ac, original_dc, override_active, enabled
    if not STATE_FILE.exists():
        log("No previous state file — clean start"); return False
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        log(f"Loaded state for recovery: {data}")
    except Exception as e:
        log(f"startup_recovery load failed: {e}", "error"); return False
    prev_override = data.get("override_active"); prev_ac = data.get("original_ac"); prev_dc = data.get("original_dc")
    if prev_override and prev_ac is not None and prev_dc is not None:
        log(f"Crash recovery: restoring inhibit, lid was 0", "warning")
        release_awake()
        original_ac=prev_ac; original_dc=prev_dc; override_active=False; enabled=True
        try:
            new_data={"original_ac":prev_ac,"original_dc":prev_dc,"override_active":False,"enabled":True,"spotify_was_playing":False,"timestamp":time.time()}
            tmp=STATE_FILE.with_suffix(".tmp"); tmp.write_text(json.dumps(new_data,indent=2),encoding="utf-8"); tmp.replace(STATE_FILE)
        except Exception as e: log(f"recovery save failed: {e}", "error")
        return True
    else:
        log("Previous state showed no override — no recovery needed")
        if not data.get("enabled", True):
            log("Resetting enabled from persisted False to True for fresh session", "info")
            enabled=True
            try:
                data["enabled"]=True; data["timestamp"]=time.time()
                tmp=STATE_FILE.with_suffix(".tmp"); tmp.write_text(json.dumps(data,indent=2),encoding="utf-8"); tmp.replace(STATE_FILE)
            except Exception as e: log(f"enabled reset save failed: {e}", "error")
        return False

def _play_custom(sound_path):
    for player in [["paplay", str(sound_path)], ["aplay", str(sound_path)], ["ffplay", "-nodisp", "-autoexit", str(sound_path)]]:
        try:
            p = pathlib.Path(sound_path)
            if p.exists():
                subprocess.run(player, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log(f"Played custom sound {p} via {player[0]}")
                return True
        except Exception as e:
            continue
    return False

def beep_on():
    if not BEEP_ENABLED: return
    try:
        from core.config import ON_SOUND
        if _play_custom(ON_SOUND): return
    except Exception as e:
        log(f"beep_on core import failed, trying alt: {e}", "warning")
    try:
        if _play_custom(pathlib.Path(__file__).parent.parent / "assets" / "sounds" / "on.wav"): return
    except Exception as e:
        log(f"beep_on alt failed: {e}", "warning")
    try: subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"], timeout=1)
    except: print("\a", end="")
def beep_off():
    if not BEEP_ENABLED: return
    try:
        from core.config import OFF_SOUND
        if _play_custom(OFF_SOUND): return
    except Exception as e:
        log(f"beep_off core import failed, trying alt: {e}", "warning")
    try:
        if _play_custom(pathlib.Path(__file__).parent.parent / "assets" / "sounds" / "off.wav"): return
    except Exception as e:
        log(f"beep_off alt failed: {e}", "warning")
    try: subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/suspend-error.oga"], timeout=1)
    except: print("\a", end="")
def beep_error():
    try: subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"], timeout=1)
    except: pass

def is_spotify_running():
    try:
        import psutil
        for p in psutil.process_iter(['name']):
            try:
                n = (p.info.get('name') or '').lower()
                if n == "spotify" or n == "spotify.exe":
                    return True
            except: continue
        return False
    except: return False

def get_spotify_raw_status():
    try:
        # playerctl
        r = subprocess.run(["playerctl", "-p", "spotify", "status"], capture_output=True, text=True, timeout=3)
        out = (r.stdout or "").strip().lower()
        running = is_spotify_running()
        if r.returncode==0 and out:
            if out=="playing": return 4, running
            if out=="paused": return 5, running
            if out=="stopped": return 3, running
        # fallback: check via psutil only
        if not running: return None, False
        # try dbus
        r2 = subprocess.run(["dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties.Get", "string:org.mpris.MediaPlayer2.Player", "string:PlaybackStatus"], capture_output=True, text=True, timeout=3)
        if "Playing" in r2.stdout: return 4, running
        if "Paused" in r2.stdout: return 5, running
        return None, running
    except Exception as e:
        log(f"Spotify detection error: {e}", "error")
        return None, is_spotify_running()

def is_spotify_playing():
    try:
        raw, running = get_spotify_raw_status()
        if raw is None:
            log(f"Spotify no session (app_running={running}) => is_playing=False")
            return False
        is_play = (raw == 4)
        log(f"Spotify status {raw} => is_playing={is_play} app_running={running}")
        return is_play
    except Exception as e:
        log(f"is_spotify_playing exception: {e}", "error")
        return None

def _read_lid_proc():
    for p in ["/proc/acpi/button/lid/LID/state", "/proc/acpi/button/lid/LID0/state", "/proc/acpi/button/lid/LID1/state"]:
        try:
            if os.path.exists(p):
                txt = pathlib.Path(p).read_text()
                # contains "state:      open" or "closed"
                if "closed" in txt.lower(): return True
                if "open" in txt.lower(): return False
        except: continue
    return False

def _lid_monitor_thread():
    global lid_closed, lid_known
    log("Lid monitor (procfs poll) starting")
    while not _exit_flag.is_set():
        try:
            closed = _read_lid_proc()
            if lid_known and closed != lid_closed:
                log(f"Lid state change: lid_closed={closed} (was {lid_closed})", "info")
            lid_closed = closed
            lid_known = True
        except Exception as e:
            log(f"Lid poll error: {e}", "error")
        for _ in range(50):
            if _exit_flag.is_set(): break
            time.sleep(0.1)
    log("Lid monitor exiting")

_last_toggle_ts = 0
def _fallback_hotkey_thread():
    try:
        import keyboard
        log("Hotkey thread using keyboard lib")
        def on_hotkey():
            global enabled, override_active, pause_start_ts, _last_toggle_ts
            now = time.time()
            if now - _last_toggle_ts < 0.7:
                return
            _last_toggle_ts = now
            enabled = not enabled
            log(f"[fallback] Hotkey toggle enabled={enabled}", "warning")
            save_state()
            if enabled: beep_on()
            else:
                beep_off()
                if override_active:
                    release_awake()
                    override_active=False; pause_start_ts=None; save_state()
        keyboard.add_hotkey(HOTKEY_STR, on_hotkey, suppress=True, trigger_on_release=False)
        keyboard.wait()
    except Exception as e:
        log(f"Hotkey thread failed: {e}", "error")

def start_lid_monitor():
    t = threading.Thread(target=_lid_monitor_thread, name="LidMonitor", daemon=True)
    t.start()
    threading.Thread(target=_fallback_hotkey_thread, daemon=True).start()
    return t

# ─── CORE LOGIC — IDENTICAL TO WINDOWS ───
def capture_original_lid():
    global original_ac, original_dc
    ac, dc = read_lid_values()
    if ac is None or dc is None: ac, dc = 1,1
    original_ac, original_dc = ac, dc
    log(f"Captured original lid AC={ac} DC={dc}")
    save_state()
    return ac, dc
def apply_override():
    global override_active
    if override_active: return True
    log("Applying override: lid=Do nothing (0,0) + hold awake", "warning")
    ok = write_lid_values(0,0)
    hold_awake()
    override_active=True; save_state(); log("Override ACTIVE")
    return ok
def restore_original():
    global override_active, pause_start_ts
    if not override_active:
        release_awake()
        return True
    ac = original_ac if original_ac is not None else 1
    dc = original_dc if original_dc is not None else 1
    log(f"Restoring original lid AC={ac} DC={dc} + releasing awake", "warning")
    ok = write_lid_values(ac, dc)
    release_awake()
    override_active=False; pause_start_ts=None; save_state()
    log("Restore succeeded" if ok else "Restore FAILED")
    return ok
def handle_poll_result(is_playing: bool):
    global spotify_was_playing, pause_start_ts, override_active, enabled
    if not enabled:
        if override_active: restore_original()
        return
    raw_status, app_running = get_spotify_raw_status()
    if is_playing:
        if not spotify_was_playing: log("Spotify first playing detected this session", "info")
        spotify_was_playing=True; pause_start_ts=None; save_state()
        if not override_active: apply_override()
        return
    if not spotify_was_playing:
        if override_active: restore_original()
        return
    if raw_status is None and not app_running:
        log("Spotify app closed — restoring immediately (code stops)", "info")
        if override_active:
            restore_original()
            if lid_closed and lid_known:
                log("App closed + lid CLOSED — forcing sleep (normal sleep)", "warning")
                time.sleep(1); do_sleep()
            else: log(f"App closed + lid OPEN — not sleeping, back to default (code stopped)", "info")
        pause_start_ts=None
        spotify_was_playing=False
        save_state()
        return
    if not app_running:
        if override_active: restore_original()
        pause_start_ts=None; save_state(); return
    if lid_closed and lid_known:
        log("No playback + flap shut (lid CLOSED) — restoring immediately, normal sleep (code stops)", "info")
        if override_active:
            restore_original()
            time.sleep(1); do_sleep()
        pause_start_ts=None; save_state(); return
    if override_active:
        log("No playback + flap open — restoring lid to default, code stops (no sleep, will sleep on next lid close)", "info")
        restore_original()
    pause_start_ts=None; save_state(); return

def self_healing_check():
    try:
        cur_ac, cur_dc = read_lid_values()
        if cur_ac is None: return
        if override_active:
            if cur_ac!=0 or cur_dc!=0:
                log(f"Self-healing: override should be ACTIVE but lid is AC={cur_ac} DC={cur_dc} — re-applying", "warning")
                write_lid_values(0,0); hold_awake()
        else:
            if original_ac is not None and (cur_ac!=original_ac or cur_dc!=original_dc):
                if cur_ac==0 and original_ac!=0:
                    log(f"Self-healing: lid stuck at Do nothing (0) while override INACTIVE (original AC={original_ac}) — restoring", "warning")
                    write_lid_values(original_ac, original_dc); release_awake()
                else:
                    if cur_ac!=original_ac or cur_dc!=original_dc:
                        log(f"Self-healing: lid differs from captured original (cur AC={cur_ac} DC={cur_dc} vs orig AC={original_ac} DC={original_dc}) — updating", "info")
                        globals()["original_ac"]=cur_ac; globals()["original_dc"]=cur_dc; save_state()
    except Exception as e: log(f"self_healing_check error: {e}", "error")

def cleanup_on_exit():
    log("cleanup_on_exit called", "warning")
    _exit_flag.set()
    try:
        if override_active:
            log("Exit: restoring lid before exit", "warning")
            if original_ac is not None: write_lid_values(original_ac, original_dc)
            release_awake()
            globals()["override_active"]=False; save_state()
    except Exception as e: log(f"cleanup failed: {e}", "error")

def signal_handler(sig, frame):
    log(f"Signal {sig} received", "warning")
    cleanup_on_exit()
    sys.exit(0)

def main():
    log("="*60)
    log("StayAwake starting — Spotify-aware lid-close control (Linux)")
    log(f"Config: poll={POLL_INTERVAL_SEC}s grace={GRACE_MINUTES}min hotkey={HOTKEY_STR} beep={BEEP_ENABLED}")
    log(f"AppDir: {APPDIR} State: {STATE_FILE} Log: {LOG_FILE}")
    hide_console()
    startup_recovery()
    capture_original_lid()
    global spotify_was_playing, pause_start_ts, enabled
    if spotify_was_playing:
        log(f"Resetting spotify_was_playing from persisted {spotify_was_playing} to False for fresh session", "info")
        spotify_was_playing=False; pause_start_ts=None; save_state()
    if not enabled:
        log(f"Resetting enabled from persisted False to True for fresh session (press {HOTKEY_STR} to toggle)", "info")
        enabled=True; save_state(); beep_on()
        log("Tool re-enabled on startup — was previously disabled via hotkey", "warning")
    atexit.register(cleanup_on_exit)
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        if hasattr(signal, "SIGBREAK"): signal.signal(signal.SIGBREAK, signal_handler)
    except Exception as e: log(f"signal setup failed: {e}", "error")
    lid_thread = start_lid_monitor()
    time.sleep(0.5)
    log(f"Entering main poll loop (silent) — hotkey {HOTKEY_STR} to toggle")
    log("Initial state: enabled=True, lid_closed=False (assumed open until notification), override=False")
    release_awake()
    poll_count=0; last_heal=time.time()
    try:
        while not _exit_flag.is_set():
            poll_count+=1
            try:
                if not is_spotify_running():
                    if poll_count % 5 == 1:
                        log("Spotify not running — tool idle (code stopped), normal sleep, polling every 60s", "info")
                    if override_active: restore_original()
                    for _ in range(60*10):
                        if _exit_flag.is_set(): break
                        if _ % 50 == 0 and is_spotify_running():
                            log("Spotify detected — resuming active poll", "info")
                            break
                        time.sleep(0.1)
                    continue
                is_playing = is_spotify_playing()
                if is_playing is None:
                    log("Spotify polling returned None (error) — treating as not playing", "warning")
                    is_playing=False
                handle_poll_result(bool(is_playing))
            except Exception as e: log(f"Poll error: {e}\n{traceback.format_exc()}", "error")
            if time.time()-last_heal>60:
                self_healing_check()
                last_heal=time.time()
            for _ in range(POLL_INTERVAL_SEC*10):
                if _exit_flag.is_set(): break
                time.sleep(0.1)
    except KeyboardInterrupt: log("KeyboardInterrupt — exiting")
    finally: cleanup_on_exit(); log("StayAwake exited"); logging.shutdown()

if __name__ == "__main__": main()
