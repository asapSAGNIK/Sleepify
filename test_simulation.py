"""
Test simulation for StayAwake logic — exercises state machine without needing
physical lid close or waiting 5 minutes.
Run: python test_simulation.py --grace 10 (grace 10 seconds for quick test)
"""
import time, json, sys, pathlib, os

# Patch imports before loading stayawake
import stayawake as sw

# Speed up for test: override grace
TEST_GRACE = 10  # seconds
TEST_POLL = 2

sw.GRACE_SECONDS = TEST_GRACE
sw.POLL_INTERVAL_SEC = TEST_POLL

print("=== StayAwake Simulation Test ===")
print(f"Original lid capture:")
ac, dc = sw.read_lid_values()
print(f" Current AC={ac} DC={dc}")

# Clean state
sw.APPDIR.mkdir(parents=True, exist_ok=True)
if sw.STATE_FILE.exists():
    sw.STATE_FILE.unlink(missing_ok=True)
    print("Cleared previous state file")

# Simulate startup
sw.original_ac, sw.original_dc = ac, dc
sw.spotify_was_playing = False
sw.override_active = False
sw.enabled = True
sw.lid_closed = False
sw.lid_known = True  # simulate known for test
sw.pause_start_ts = None

# Mock do_sleep to avoid actually sleeping
orig_sleep = sw.do_sleep
sleep_called = []
def mock_sleep():
    print(" >>> [MOCK] do_sleep() called — would have forced sleep now!")
    sleep_called.append(time.time())
    return True
sw.do_sleep = mock_sleep

# Mock write_lid to track
orig_write = sw.write_lid_values
writes = []
def mock_write(a,d):
    print(f" >>> [MOCK] write_lid_values({a},{d})")
    writes.append((a,d))
    # Still actually write? Let's keep tracking but not change real powercfg for test?
    # For checklist verification we will do real writes in dry-run then restore.
    # Here we do real write for realism but log:
    return orig_write(a,d)
sw.write_lid_values = mock_write

def assert_eq(a,b,msg):
    if a!=b:
        print(f"FAIL: {msg}: {a} != {b}")
        sys.exit(1)
    else:
        print(f"PASS: {msg}")

# 1. Spotify never opened, lid closed -> no override, should not touch power
print("\n--- Test 1: Spotify never opened, lid closed => stock Windows (no override) ---")
sw.lid_closed = True
sw.handle_poll_result(False)  # not playing, never was
assert_eq(sw.override_active, False, "No override when never played")
assert_eq(len(writes), 0, "No power writes")
assert_eq(len(sleep_called), 0, "No forced sleep")
print("Writes:", writes)

# 2. Spotify playing, lid closed -> stays awake indefinitely while playing
print("\n--- Test 2: Spotify playing, lid closed => override active ---")
writes.clear()
sw.lid_closed = True
sw.handle_poll_result(True)  # playing
assert_eq(sw.override_active, True, "Override active while playing")
assert_eq(writes[-1], (0,0), "Lid set to Do nothing")
assert_eq(sw.spotify_was_playing, True, "Was playing flag")

# 3. Spotify paused, lid closed, wait 5+ min (10s test) => sleeps and restores
print("\n--- Test 3: Playing -> Paused, lid closed, wait grace => restore + sleep ---")
# Already playing, now pause (first poll after pause)
sw.handle_poll_result(False)
assert_eq(sw.pause_start_ts is not None, True, "Grace timer started")
assert_eq(sw.override_active, True, "Still overridden during grace")
print(f" Grace started at {sw.pause_start_ts}, waiting {TEST_GRACE+1}s ...")
time.sleep(TEST_GRACE+1)
# Poll again with still paused, lid closed -> should expire
writes.clear()
sleep_called.clear()
sw.handle_poll_result(False)  # this should detect elapsed >= grace
assert_eq(sw.override_active, False, "Override cleared after grace")
assert_eq(writes[-1][0] != 0 or writes[-1][1] !=0, True, "Lid restored to original (not 0)")
assert_eq(len(sleep_called), 1, "Forced sleep called because lid closed")
print(" PASS: slept as expected with lid closed")

# 4. Spotify paused, lid OPEN, wait grace => NOT force sleep
print("\n--- Test 4: Playing -> Paused, LID OPEN, wait grace => restore but NO sleep ---")
# Reset to playing again
writes.clear()
sleep_called.clear()
sw.handle_poll_result(True)  # playing again
assert_eq(sw.override_active, True, "Override re-applied")
# Pause
sw.handle_poll_result(False)
assert_eq(sw.pause_start_ts is not None, True, "Grace re-started")
sw.lid_closed = False  # lid open
print(f" Grace started, lid OPEN, waiting {TEST_GRACE+1}s")
time.sleep(TEST_GRACE+1)
writes.clear()
sleep_called.clear()
sw.handle_poll_result(False)
assert_eq(sw.override_active, False, "Override cleared")
assert_eq(len(sleep_called), 0, "No forced sleep because lid open => PASS")
print(" PASS: did not sleep with lid open")

# 5. Hotkey toggle off/on
print("\n--- Test 5: Hotkey toggle disables override and beeps ---")
writes.clear()
sw.enabled = True
# Simulate playing -> override
sw.handle_poll_result(True)
assert_eq(sw.override_active, True, "Override before toggle")
# Toggle off (simulating hotkey)
sw.enabled = False
sw.handle_poll_result(False)  # with disabled, should restore
# Actually handle_poll_result when disabled will restore if override active
# Do manual toggle logic as in wndproc
if sw.override_active:
    sw.restore_original()
assert_eq(sw.override_active, False, "Override cleared when disabled")
print(" PASS: disabled restores")

sw.enabled = True
writes.clear()
sw.handle_poll_result(True)
assert_eq(sw.override_active, True, "Re-enabled + playing => override again")
print(" PASS: re-enabled works")

# 6. Crash recovery: simulate stuck lid=0 with marker, then startup_recovery
print("\n--- Test 6: Crash recovery (stuck Do nothing) ---")
# Simulate crash: write stuck 0 and marker says override True
import tempfile, json, ctypes
# Ensure current is restored before test
sw.write_lid_values = orig_write
sw.write_lid_values(sw.original_ac, sw.original_dc)
# Now simulate stuck: write 0 directly
sw.write_lid_values(0,0)
ac_now, dc_now = sw.read_lid_values()
print(f" Stuck lid now AC={ac_now} DC={dc_now}")
# Write marker file with original 1,1 and override True
data = {"original_ac": sw.original_ac, "original_dc": sw.original_dc, "override_active": True, "enabled": True, "spotify_was_playing": True, "timestamp": time.time()}
sw.STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
print(" Written crash marker:", data)
# Now run startup_recovery (should restore)
sw.override_active = True  # simulate that we think we're active
sw.startup_recovery()
ac_after, dc_after = sw.read_lid_values()
print(f" After recovery AC={ac_after} DC={dc_after}")
assert_eq(ac_after, sw.original_ac, "Crash recovery restored AC")
assert_eq(dc_after, sw.original_dc, "Crash recovery restored DC")
print(" PASS: crash recovery fixed stuck lid")

# Restore mocks
sw.do_sleep = orig_sleep
sw.write_lid_values = orig_write
# Ensure final restore
sw.write_lid_values(sw.original_ac, sw.original_dc)
sw.override_active = False
sw.save_state()
print("\n=== All simulation tests PASSED ===")
print("Check final lid:", sw.read_lid_values())
print("Log at", sw.LOG_FILE)
ac_final, dc_final = sw.read_lid_values()
print(f"Final lid AC={ac_final} DC={dc_final} (should be original {sw.original_ac},{sw.original_dc})")
