#!/usr/bin/env python3
# Shared config — same logic on all platforms (Windows/Mac/Linux)
import os, sys, pathlib

# Hotkey — platform-specific: Windows/Linux Ctrl+Shift+Space, Mac Cmd+Shift+Space
if sys.platform == "darwin":
    HOTKEY_MOD = 0x0008 | 0x0004          # MOD_WIN (Cmd) | MOD_SHIFT on Mac
    HOTKEY_VK = 0x20                       # VK_SPACE
    HOTKEY_STR = "cmd+shift+space"
else:
    HOTKEY_MOD = 0x0002 | 0x0004          # MOD_CONTROL | MOD_SHIFT
    HOTKEY_VK = 0x20                       # VK_SPACE
    HOTKEY_STR = "ctrl+shift+space"

POLL_INTERVAL_SEC = 12
GRACE_MINUTES = 5
GRACE_SECONDS = GRACE_MINUTES * 60
BEEP_ENABLED = True
LOG_TO_FILE = True

# Windows powercfg GUIDs (used only on Windows, harmless elsewhere)
SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
LIDACTION   = "5ca83367-6e45-459f-a27b-476b1d01c936"

ES_CONTINUOUS        = 0x80000000
ES_SYSTEM_REQUIRED   = 0x00000001
ES_DISPLAY_REQUIRED  = 0x00000002
ES_AWAYMODE_REQUIRED = 0x00000040
ES_HOLD   = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED | ES_AWAYMODE_REQUIRED
ES_RELEASE= ES_CONTINUOUS

SPOTIFY_AUMID = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"

# Platform-aware app dirs
def _app_dir():
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "StayAwake"
    elif sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))
        return pathlib.Path(xdg) / "StayAwake"
    else:
        return pathlib.Path(os.environ.get("APPDATA", str(home))) / "StayAwake"

APPDIR = _app_dir()
STATE_FILE = APPDIR / "state.json"
LOG_FILE = APPDIR / "stayawake.log"

# Custom sounds — single location at repo root assets/sounds (all platforms)
# Resolved at runtime: first try repo assets, then installed copy in APPDIR
def _sounds_dir():
    # repo root is two levels up from core/config.py (core -> spotifysleeptool)
    repo_root = pathlib.Path(__file__).parent.parent
    repo_sounds = repo_root / "assets" / "sounds"
    if (repo_sounds / "on.wav").exists() or (repo_sounds / "off.wav").exists():
        return repo_sounds
    # installed copy (e.g., %APPDATA%/StayAwake/assets/sounds)
    return APPDIR / "assets" / "sounds"

SOUNDS_DIR = _sounds_dir()
ON_SOUND = SOUNDS_DIR / "on.wav"
OFF_SOUND = SOUNDS_DIR / "off.wav"

# CLI overrides
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
    try: GRACE_MINUTES = int(sys.argv[sys.argv.index("--grace")+1]); GRACE_SECONDS = GRACE_MINUTES*60
    except: pass
if "--poll" in sys.argv:
    try: POLL_INTERVAL_SEC = int(sys.argv[sys.argv.index("--poll")+1])
    except: pass
