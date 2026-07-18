"""Blipfeed UI.

Everything here is a window the user *deliberately opens*, so ordinary focus-taking
Tk is correct — do NOT port Blip's prevent_focus_steal machinery (CLAUDE.md
Decisions #6). AUTO-mode capture is headless and lives outside this package.
"""
