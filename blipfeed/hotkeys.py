"""Global hotkeys — AUTO mode's control surface.

AUTO mode is headless, so without these there is no way to start or stop a session
without alt-tabbing out of whatever you're immersing in. That defeats the point.

**The gotcha (learned in Blip, gotcha #7): `RegisterHotKey` and the message pump must
run on the SAME thread.** A hotkey registered on thread A produces `WM_HOTKEY`
messages in thread A's queue only, so pumping from anywhere else silently receives
nothing — no error, just a dead hotkey. Hence the whole listener lives inside
`run()`: it registers, pumps, and unregisters on its own thread.

Registration can legitimately fail (another app owns the combo). That's reported per
binding rather than swallowed, so the caller can tell the user *which* hotkey is dead
instead of leaving them pressing a key that does nothing.
"""
import ctypes
import ctypes.wintypes as wintypes
import threading

from .logging_setup import get

log = get("hotkeys")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
         "shift": MOD_SHIFT, "win": MOD_WIN, "super": MOD_WIN}

# Non-alphanumeric keys we accept by name.
_KEYS = {"f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
         "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
         "space": 0x20, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
         "enter": 0x0D, "return": 0x0D, "insert": 0x2D, "delete": 0x2E,
         "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
         "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


def parse(combo):
    """'ctrl+alt+b' -> (modifiers, virtual_key). Raises ValueError if unparseable."""
    if not combo or not combo.strip():
        raise ValueError("empty hotkey")
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"unparseable hotkey {combo!r}")
    *mod_names, key = parts
    mods = 0
    for name in mod_names:
        if name not in _MODS:
            raise ValueError(f"unknown modifier {name!r} in {combo!r}")
        mods |= _MODS[name]
    if key in _KEYS:
        vk = _KEYS[key]
    elif len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key.upper())
    else:
        raise ValueError(f"unknown key {key!r} in {combo!r}")
    if not mods:
        # A bare key would swallow that key system-wide — never what anyone wants.
        raise ValueError(f"{combo!r} needs at least one modifier")
    return mods, vk


class HotkeyListener(threading.Thread):
    """Registers combos and dispatches their callbacks. One per process.

    bindings: {"ctrl+alt+b": callable}. Callbacks run on THIS thread, so they must
    not block — hand off long work (a capture, a window) to another thread.
    """

    def __init__(self, bindings, name="hotkeys"):
        super().__init__(name=name, daemon=True)
        self.bindings = dict(bindings)
        self.failed = {}                 # combo -> reason
        self.registered = []             # combos that actually took
        self._thread_id = None
        self._ready = threading.Event()

    def run(self):
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        callbacks = {}

        for i, (combo, cb) in enumerate(self.bindings.items(), start=1):
            try:
                mods, vk = parse(combo)
            except ValueError as exc:
                self.failed[combo] = str(exc)
                log.warning("hotkey %s: %s", combo, exc)
                continue
            # MOD_NOREPEAT: holding the keys fires once, not continuously.
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                callbacks[i] = (combo, cb)
                self.registered.append(combo)
            else:
                self.failed[combo] = "already in use by another application"
                log.warning("hotkey %s could not be registered (in use?)", combo)

        self._ready.set()
        if not callbacks:
            log.error("no hotkeys registered — none of %s were available",
                      list(self.bindings))
            return

        msg = wintypes.MSG()
        try:
            # GetMessage returns 0 on WM_QUIT, -1 on error.
            while True:
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got in (0, -1):
                    break
                if msg.message == WM_HOTKEY:
                    entry = callbacks.get(msg.wParam)
                    if entry:
                        combo, cb = entry
                        try:
                            cb()
                        except Exception:
                            log.exception("hotkey %s callback failed", combo)
        finally:
            for i in callbacks:
                user32.UnregisterHotKey(None, i)
            log.info("hotkey listener stopped")

    def wait_ready(self, timeout=2.0):
        """Block until registration has been attempted, so callers can report which
        bindings are dead before the user starts pressing keys."""
        return self._ready.wait(timeout)

    def stop(self):
        """Break the pump from another thread."""
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
