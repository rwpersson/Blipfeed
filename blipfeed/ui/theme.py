"""One theme for every window — palette, fonts, spacing, and reusable widgets.

Before this, each window defined its own colour constants and drifted. Everything now
imports from here, so a scheme change is one file and the app stays consistent.

Scheme: **Amber** — a warm dark base with a gold accent that echoes the tone-color
palette. Dark suits a tool that runs alongside games and long immersion.
"""
import tkinter as tk

# --- palette ------------------------------------------------------------------
BG = "#12100c"           # app background
SURFACE = "#1c1913"      # cards / panels
SURFACE_2 = "#262117"    # inputs, ghost buttons, rows
BORDER = "#322c20"
HOVER = "#31291c"        # ghost-button hover

INK = "#f0ebe2"          # primary text
MUTED = "#9a9184"        # secondary text
FAINT = "#6b6558"        # tertiary / disabled

ACCENT = "#f2b035"       # primary buttons, active toggles, the capture dot
ACCENT_HOVER = "#f7c052"
ACCENT_INK = "#1a1610"   # text on the (light) accent
GOOD = "#6bbf59"
GOOD_DIM = "#3f7237"     # the capture dot mid-pulse
DANGER = "#e05a4d"
DANGER_HOVER = "#e8695f"
WARNING = "#f2b035"

# --- type + spacing -----------------------------------------------------------
FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_LABEL = ("Segoe UI", 8, "bold")     # UPPERCASE section labels
FONT_H = ("Segoe UI Semibold", 11)
FONT_BIG = ("Segoe UI Semibold", 13)
FONT_TITLE = ("Segoe UI Semibold", 18)
FONT_NUM = ("Segoe UI Semibold", 22)
CJK = ("Microsoft YaHei", 15, "bold")

PAD = 8                  # base spacing unit; multiples elsewhere

_KINDS = {
    # kind:     (bg,        fg,         hover)
    "primary": (ACCENT, ACCENT_INK, ACCENT_HOVER),
    "ghost": (SURFACE_2, INK, HOVER),
    "danger": (DANGER, "#ffffff", DANGER_HOVER),
}


def commas(n):
    """Thousands separators for the big stat numbers."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def style_button(btn, kind):
    """Point an existing button at a kind (used when a button changes role, e.g.
    Start -> Stop). Keeps its hover in sync."""
    bg, fg, hover = _KINDS[kind]
    btn._base, btn._hover = bg, hover
    btn.config(bg=bg, fg=fg, activebackground=hover, activeforeground=fg)


def button(master, text, command=None, kind="ghost", big=False, **kw):
    """A flat themed button with a real hover state (Tk gives us press for free via
    activebackground)."""
    bg, fg, hover = _KINDS[kind]
    b = tk.Button(master, text=text, command=command,
                  font=(FONT_BIG if big else FONT), bg=bg, fg=fg,
                  activebackground=hover, activeforeground=fg, relief="flat", bd=0,
                  padx=kw.pop("padx", 16), pady=kw.pop("pady", 9 if big else 7),
                  cursor="hand2", **kw)
    b._base, b._hover = bg, hover
    b.bind("<Enter>", lambda _e: b["state"] == "normal" and b.config(bg=b._hover))
    b.bind("<Leave>", lambda _e: b.config(bg=b._base))
    return b


def card(master, title=None):
    """A surface panel with an optional uppercase section label."""
    frame = tk.Frame(master, bg=SURFACE)
    frame.pack(fill="x", padx=20, pady=6)
    if title:
        tk.Label(frame, text=title.upper(), bg=SURFACE, fg=MUTED,
                 font=FONT_LABEL).pack(anchor="w", padx=14, pady=(10, 0))
    return frame


def section_label(master, text, **kw):
    return tk.Label(master, text=text.upper(), bg=kw.pop("bg", SURFACE), fg=MUTED,
                    font=FONT_LABEL, **kw)


def style_ttk(root):
    """Configure ttk widgets for the dark theme (the review window uses ttk).

    ttk widgets ignore per-widget colours and read a Style, so a window built from
    them looks light on the default 'clam' theme until this runs.
    """
    from tkinter import ttk

    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    st.configure(".", background=SURFACE, foreground=INK, fieldbackground=SURFACE_2,
                 bordercolor=BORDER, focuscolor=SURFACE, insertcolor=INK)
    st.configure("TFrame", background=SURFACE)
    st.configure("TLabel", background=SURFACE, foreground=INK)
    st.configure("TButton", background=SURFACE_2, foreground=INK, borderwidth=0,
                 padding=(10, 5))
    st.map("TButton", background=[("active", HOVER)])
    st.configure("Accent.TButton", background=ACCENT, foreground=ACCENT_INK)
    st.map("Accent.TButton", background=[("active", ACCENT_HOVER)])
    st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
    st.configure("TNotebook.Tab", background=SURFACE_2, foreground=MUTED,
                 padding=(16, 8), borderwidth=0)
    st.map("TNotebook.Tab", background=[("selected", SURFACE)],
           foreground=[("selected", INK)])
    st.configure("TCheckbutton", background=SURFACE, foreground=INK)
    st.map("TCheckbutton", background=[("active", SURFACE)])
    st.configure("TScrollbar", background=SURFACE_2, troughcolor=BG, borderwidth=0,
                 arrowcolor=MUTED)
    st.configure("TSeparator", background=BORDER)
    st.configure("TEntry", fieldbackground=SURFACE_2, foreground=INK,
                 bordercolor=BORDER)
    root.configure(bg=BG)


# --- window chrome ------------------------------------------------------------
def apply_icon(win):
    """Set the app icon on a window. No-op if the .ico isn't present."""
    from .. import paths
    ico = paths.icon_file()
    if ico.exists():
        try:
            win.iconbitmap(default=str(ico))
        except tk.TclError:
            pass
