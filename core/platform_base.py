from abc import ABC, abstractmethod
from typing import Optional, Tuple

class BasePlatform(ABC):
    """Platform interface — core logic calls only these. Each OS implements."""

    @abstractmethod
    def is_admin(self) -> bool: ...

    @abstractmethod
    def ensure_admin(self) -> bool: ...

    @abstractmethod
    def hide_console(self) -> None: ...

    @abstractmethod
    def read_lid_values(self) -> Tuple[Optional[int], Optional[int]]:
        """Returns (ac, dc) lid indices or (None,None)."""

    @abstractmethod
    def write_lid_values(self, ac: int, dc: int) -> bool: ...

    @abstractmethod
    def hold_awake(self): ...

    @abstractmethod
    def release_awake(self): ...

    @abstractmethod
    def do_sleep(self) -> bool: ...

    @abstractmethod
    def get_spotify_status(self) -> Tuple[Optional[int], bool]:
        """Returns (raw_status_int_or_None, app_running_bool). raw None = no session."""

    @abstractmethod
    def is_spotify_playing(self) -> Optional[bool]:
        """True=playing, False=paused/closed, None=error."""

    @abstractmethod
    def get_lid_state(self) -> Tuple[bool, bool]:
        """Returns (lid_closed, lid_known)."""

    @abstractmethod
    def start_lid_monitor(self, on_lid_change, on_hotkey_toggle):
        """Start background thread for lid + hotkey. Calls callbacks."""

    @abstractmethod
    def beep_on(self): ...

    @abstractmethod
    def beep_off(self): ...

    @abstractmethod
    def beep_error(self): ...

    @abstractmethod
    def cleanup(self, hotkey_hwnd=None): ...
