#!/usr/bin/env python3
# Shared logic — single source, used by windows/mac/linux
import time, logging
logger = logging.getLogger("StayAwake")
def log(msg, level="info"): getattr(logger, level)(msg)

import core.state as state
import core.config as config

def capture_original_lid(platform):
    ac, dc = platform.read_lid_values()
    if ac is None or dc is None:
        log("Failed to capture original lid values — defaulting to Sleep (1,1)", "warning")
        ac, dc = 1, 1
    state.original_ac, state.original_dc = ac, dc
    log(f"Captured original lid AC={ac} DC={dc}")
    state.save_state()
    return ac, dc

def apply_override(platform):
    if state.override_active:
        return True
    log("Applying override: lid=Do nothing (0,0) + hold awake", "warning")
    ok = platform.write_lid_values(0, 0)
    if not ok:
        log("Failed to set lid to Do nothing — hold awake still applied but lid may still sleep!", "error")
        platform.beep_error()
    platform.hold_awake()
    state.override_active = True
    state.save_state()
    log("Override ACTIVE")
    return ok

def restore_original(platform):
    if not state.override_active:
        platform.release_awake()
        return True
    ac = state.original_ac if state.original_ac is not None else 1
    dc = state.original_dc if state.original_dc is not None else 1
    log(f"Restoring original lid AC={ac} DC={dc} + releasing awake", "warning")
    ok = platform.write_lid_values(ac, dc)
    platform.release_awake()
    state.override_active = False
    state.pause_start_ts = None
    state.save_state()
    if not ok:
        log("Restore lid FAILED", "error")
        platform.beep_error()
    else:
        log("Restore succeeded — lid action back to original")
    return ok

def handle_poll_result(platform, is_playing: bool):
    # Get raw status to distinguish app-closed vs paused
    try:
        raw_status, app_running = platform.get_spotify_raw_status()
    except:
        raw_status, app_running = None, platform.is_spotify_running() if hasattr(platform, 'is_spotify_running') else True

    if not state.enabled:
        if state.override_active:
            restore_original(platform)
        return

    if is_playing:
        if not state.spotify_was_playing:
            log("Spotify first playing detected this session", "info")
        state.spotify_was_playing = True
        state.pause_start_ts = None
        state.save_state()
        if not state.override_active:
            apply_override(platform)
        return

    # Not playing
    if not state.spotify_was_playing:
        if state.override_active:
            log("Spotify never played but override active — restoring (should not happen)", "warning")
            restore_original(platform)
        return

    # Was playing before, now not playing
    # App closed → immediate (no grace)
    if raw_status is None and not app_running:
        log("Spotify app closed — restoring immediately (code stops)", "info")
        if state.override_active:
            restore_original(platform)
            if state.lid_closed and state.lid_known:
                log("App closed + lid CLOSED — forcing sleep (normal sleep)", "warning")
                time.sleep(1)
                platform.do_sleep()
            else:
                log(f"App closed + lid OPEN — not sleeping, back to default (code stopped)", "info")
        state.pause_start_ts = None
        state.spotify_was_playing = False
        state.save_state()
        return

    if not app_running:
        if state.override_active:
            restore_original(platform)
        state.pause_start_ts = None
        state.save_state()
        return

    # App open, flap shut + no playback → immediate
    if state.lid_closed and state.lid_known:
        log("No playback + flap shut (lid CLOSED) — restoring immediately, normal sleep (code stops)", "info")
        if state.override_active:
            restore_original(platform)
            time.sleep(1)
            platform.do_sleep()
        state.pause_start_ts = None
        state.save_state()
        return

    # App open, flap open + no playback → silent stop
    if state.override_active:
        log("No playback + flap open — restoring lid to default, code stops (no sleep, will sleep on next lid close)", "info")
        restore_original(platform)
    state.pause_start_ts = None
    state.save_state()
    return

def self_healing_check(platform):
    try:
        cur_ac, cur_dc = platform.read_lid_values()
        if cur_ac is None:
            return
        if state.override_active:
            if cur_ac != 0 or cur_dc != 0:
                log(f"Self-healing: override should be ACTIVE but lid is AC={cur_ac} DC={cur_dc} — re-applying", "warning")
                platform.write_lid_values(0, 0)
                platform.hold_awake()
        else:
            if state.original_ac is not None and (cur_ac != state.original_ac or cur_dc != state.original_dc):
                if cur_ac == 0 and state.original_ac != 0:
                    log(f"Self-healing: lid stuck at Do nothing (0) while override INACTIVE (original AC={state.original_ac}) — restoring", "warning")
                    platform.write_lid_values(state.original_ac, state.original_dc)
                    platform.release_awake()
                else:
                    if cur_ac != state.original_ac or cur_dc != state.original_dc:
                        log(f"Self-healing: lid differs from captured original (cur AC={cur_ac} DC={cur_dc} vs orig AC={state.original_ac} DC={state.original_dc}) — updating", "info")
                        state.original_ac = cur_ac
                        state.original_dc = cur_dc
                        state.save_state()
    except Exception as e:
        log(f"self_healing_check error: {e}", "error")
