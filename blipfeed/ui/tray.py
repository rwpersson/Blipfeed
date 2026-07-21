"""System-tray icon — the control surface while the dashboard is hidden.

The point of AUTO mode is to run while you game with the window out of the way. The
tray gives you Start/Stop, Show, and Quit without alt-tabbing, and its title reflects
whether capture is running.

pystray runs its own message loop, so the `Icon` lives on a **background thread** while
Tk owns the main thread. Menu callbacks therefore fire on the tray thread and must hop
back to Tk — every callback here is a thin wrapper the caller routes through
`root.after`. If pystray isn't installed the tray degrades to a no-op (the window still
works), so it's never a hard dependency at runtime.
"""
import threading

from .. import paths
from ..logging_setup import get

log = get("ui.tray")


class Tray:
    """A tray icon driven by callbacks. `running()` lets the menu label itself.

    callbacks: toggle, show, review, quit — each called with no args, on the tray
    thread; wrap them to marshal onto Tk before touching widgets.
    """

    def __init__(self, *, running, on_toggle, on_show, on_review, on_quit,
                 title="Blipfeed"):
        self._running = running
        self._on = {"toggle": on_toggle, "show": on_show, "review": on_review,
                    "quit": on_quit}
        self._title = title
        self._icon = None
        self._thread = None

    def available(self):
        try:
            import pystray  # noqa: F401
            return True
        except ImportError:
            return False

    def _image(self):
        from PIL import Image
        ico = paths.icon_file()
        if ico.exists():
            return Image.open(ico)
        return Image.new("RGB", (64, 64), (242, 176, 53))     # amber fallback

    def _menu(self):
        from pystray import Menu, MenuItem

        return Menu(
            MenuItem(lambda _i: "Stop capture" if self._running() else "Start capture",
                     lambda: self._on["toggle"](), default=False),
            MenuItem("Open Blipfeed", lambda: self._on["show"](), default=True),
            MenuItem("Review words", lambda: self._on["review"]()),
            Menu.SEPARATOR,
            MenuItem("Quit", lambda: self._on["quit"]()),
        )

    def start(self):
        """Run the tray on its own thread. No-op if pystray is missing."""
        if not self.available():
            log.info("pystray not installed — no tray icon")
            return False
        from pystray import Icon

        self._icon = Icon("blipfeed", self._image(), self._title, menu=self._menu())
        self._thread = threading.Thread(target=self._icon.run, name="tray",
                                        daemon=True)
        self._thread.start()
        return True

    def refresh(self):
        """Re-evaluate the menu labels + tooltip after a state change."""
        if self._icon is not None:
            state = "capturing" if self._running() else "idle"
            self._icon.title = f"{self._title} — {state}"
            try:
                self._icon.update_menu()
            except Exception:
                log.exception("tray menu update")

    def notify(self, message, title=None):
        """A one-off balloon (e.g. 'still running in the tray')."""
        if self._icon is not None:
            try:
                self._icon.notify(message, title or self._title)
            except Exception:
                log.exception("tray notify")

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                log.exception("tray stop")
            self._icon = None
