#!/usr/bin/env python3
# Shared state — single source for all platforms
import json, time, pathlib
from .config import STATE_FILE, APPDIR
import logging
logger = logging.getLogger("StayAwake")

# Global state (per-process, not persisted except via state.json)
enabled = True
lid_closed = False
lid_known = False
override_active = False
original_ac = None
original_dc = None
spotify_was_playing = False
pause_start_ts = None
_hotkey_hwnd = None
import threading as _threading
_exit_flag = _threading.Event()

def log(msg, level="info"):
    getattr(logger, level)(msg)

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

def startup_recovery(platform):
    """Crash recovery — must be called BEFORE capture_original_lid."""
    global original_ac, original_dc, override_active, enabled
    if not STATE_FILE.exists():
        log("No previous state file — clean start")
        return False
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        log(f"Loaded state for recovery: {data}")
    except Exception as e:
        log(f"startup_recovery load failed: {e}", "error")
        return False
    prev_override = data.get("override_active")
    prev_ac = data.get("original_ac")
    prev_dc = data.get("original_dc")
    if prev_override and prev_ac is not None and prev_dc is not None:
        log(f"Crash recovery: previous run died with override active (lid was 0). Restoring original AC={prev_ac} DC={prev_dc}", "warning")
        try:
            platform.write_lid_values(prev_ac, prev_dc)
        except Exception as e:
            log(f"Crash recovery write failed: {e}", "error")
        try:
            platform.release_awake()
        except: pass
        original_ac = prev_ac
        original_dc = prev_dc
        override_active = False
        enabled = True
        try:
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
