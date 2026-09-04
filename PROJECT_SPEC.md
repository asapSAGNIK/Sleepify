# Project: StayAwake — Spotify-aware lid-close sleep control (Windows)

## Goal
Build a small, silent Windows background utility that changes how the laptop
handles being put to sleep when the lid is closed, based on Spotify playback
state — while leaving default Windows behavior completely untouched in every
other case.

## Core behavior (non-negotiable)
1. **Spotify desktop app is actively playing** → closing the lid must NOT put
   the laptop to sleep. It stays fully awake and running.
2. **Spotify is paused, stopped, or was never opened** → the laptop must
   behave exactly like stock Windows. Specifically:
   - If Spotify has never played this session, lid-close sleep behaves
     identically to a machine with this tool not installed at all — nothing
     about Windows' power settings should be touched.
   - If Spotify WAS playing and then paused/stopped, give a 5-minute grace
     period (in case it's just between songs or a temporary pause) before
     putting the machine to sleep. Only sleep if the lid is actually closed
     at that point — do not force sleep on an open lid just because Spotify
     paused.
3. **No permanent system setting changes.** The tool must only override
   Windows' lid-close-action setting for the duration that Spotify is
   actively playing, and must always restore the original setting the
   instant that condition ends. Under no circumstances should the laptop be
   left in a state where it can never sleep, even if the tool crashes,
   is force-killed, or the machine loses power. (Consider: what happens if
   the process dies mid-override? Should there be a periodic
   self-healing check, or a startup routine that restores defaults if an
   unclean shutdown is detected?)

## Toggle control
- A global hotkey (e.g. `Ctrl+Alt+S`) enables/disables the entire tool
  without needing any window or tray icon open.
- The tool should run 100% silently otherwise — no visible window, no
  taskbar entry, no persistent tray icon required.
- On toggle, give SOME lightweight confirmation of the new state. Toast
  notifications were attempted and were unreliable in testing (win10toast,
  win11toast, and winotify all failed silently on this machine — likely a
  Windows AppUserModelID/app-identity issue with unpackaged Python scripts,
  not a library bug per se). Acceptable alternatives to evaluate, in order
  of preference:
  1. A properly registered AppUserModelID so a real Windows toast works
     (this is the "correct" fix — Python scripts need to register an app
     identity via the Start Menu shortcut / registry for
     `ToastNotificationManager` to reliably show anything).
  2. A short audible beep (distinct beep for on vs. off) as a zero-dependency
     fallback that cannot silently fail.
  3. A tiny always-on-top window that flashes briefly and auto-closes.
  Pick one, but don't spend excessive effort chasing toast reliability if it
  keeps failing — a beep is a perfectly acceptable fallback.

## Playback detection
- Detect Spotify desktop app play/pause state via Windows' system-wide
  "Now Playing" media session API
  (`GlobalSystemMediaTransportControlsSessionManager`), filtered to the
  Spotify session specifically. No Spotify API keys, OAuth, or login
  required.
- Poll roughly every 10–15 seconds — no need for instant sub-second
  reaction time.

## Sleep/lid mechanics (Windows specifics)
- While playing: hold `SetThreadExecutionState` with
  `ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED`, and set the
  lid-close action (`powercfg`, `SUB_BUTTONS LIDACTION`) to "Do nothing" for
  the AC (and optionally DC/battery) power scheme.
- When the 5-minute grace period expires: restore the original lid-action
  value (captured at startup, not hardcoded — read the user's actual prior
  setting so it's restored correctly regardless of what it was), release the
  execution state, and if the lid is currently closed, put the machine to
  sleep explicitly (`SetSuspendState`).
- Needs actual lid-open/closed state (not just relying on Windows' own
  reaction to the lid event) to correctly gate the forced-sleep call — use
  `RegisterPowerSettingNotification` with
  `GUID_LIDSWITCH_STATE_CHANGE` to track real-time lid state.
- The whole tool must run elevated (Administrator), since live `powercfg`
  changes require it. Auto-start at login should use Task Scheduler with
  "Run with highest privileges," not the plain Startup folder (which does
  not run elevated).

## Explicitly out of scope for v1
- Mac support (may be a separate future project, different mechanism
  entirely — `caffeinate` + AppleScript, no lid-action equivalent).
- Any playback source other than the Spotify desktop app (not Spotify Web,
  not SoundCloud, not generic "any audio playing").
- Any GUI/settings panel — config values (hotkey, timeout minutes) can be
  constants at the top of the script.

## Testing checklist (must pass all of these)
- [ ] Spotify never opened, lid closed → sleeps immediately, same as before
      this tool existed.
- [ ] Spotify playing, lid closed → stays awake indefinitely while playing.
- [ ] Spotify playing then paused, lid closed, wait 5+ min untouched →
      sleeps, and the lid-action setting is restored to its original value
      (verify via `powercfg /q SCHEME_CURRENT SUB_BUTTONS LIDACTION`).
- [ ] Spotify playing then paused, LID OPEN, wait 5+ min → does NOT force
      sleep (since lid is open).
- [ ] Kill the process forcefully (Task Manager) while override is active →
      confirm the lid-action setting does not stay permanently stuck on "do
      nothing" (this must be fixed given the earlier real-world failure
      where exactly this happened).
- [ ] Hotkey toggles on/off correctly and confirmation (whatever mechanism
      is chosen) fires reliably every time, not silently.
