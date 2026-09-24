"""Drive the real kicad_partbench App against a practice library.

    class MyTest(GuiTestCase):
        def test_x(self):
            h = self.h                       # AppHarness, fresh practice library
            h.click(h.app, "Run Import")     # find a button by text anywhere and invoke it
            win = h.last_window()            # most recently opened Toplevel
            h.answer(True)                   # queue the next askyesno answer (default: True)
            h.files.append("/path")          # queue the next file-dialog answer

Every messagebox call is recorded in ``h.dialogs`` as (kind, title, message)
instead of being shown, so tests can assert on errors and confirmations.

GUI tests need a display. They are skipped when Tk can't start (for example
on a headless Linux box without Xvfb; run them with ``xvfb-run``).
"""
from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kicad_partbench as kp  # noqa: E402

from practice_lib import PracticeLib, build  # noqa: E402

_DISPLAY_OK: bool | None = None


def display_available() -> bool:
    global _DISPLAY_OK
    if _DISPLAY_OK is None:
        try:
            r = tk.Tk()
            r.destroy()
            _DISPLAY_OK = True
        except tk.TclError:
            _DISPLAY_OK = False
    return _DISPLAY_OK


def _widget_text(w) -> str:
    try:
        return str(w.cget("text"))
    except tk.TclError:
        return ""


class AppHarness:
    def __init__(self, lib: PracticeLib):
        self.lib = lib
        self.dialogs: list[tuple[str, str, str]] = []
        self.answers: list = []      # queued answers for ask* message boxes
        self.files: list = []        # queued answers for file dialogs
        self._patches = []

        lib.home.mkdir(parents=True, exist_ok=True)
        # Keep the app away from the real home, KiCad config and trash
        env = mock.patch.dict(os.environ, {
            "HOME": str(lib.home), "USERPROFILE": str(lib.home),
            "APPDATA": str(lib.home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(lib.home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(lib.home / ".config"),
            "XDG_DATA_HOME": str(lib.home / ".local" / "share"),
        })
        env.start()
        self._patches.append(env)
        prefs_file = lib.home / ".kicad_personal_importer.json"
        prefs_file.write_text(__import__("json").dumps(lib.prefs), encoding="utf-8")
        pdir = lib.home / ".kicad-library-tool"
        self._patch(kp, "PREFS_FILE", prefs_file)
        self._patch(kp, "PASSIVES_LIB_DIR", pdir)
        self._patch(kp, "PASSIVES_LIB_FILE", pdir / "passives.json")

        def info(kind):
            def f(title="", message="", **kw):
                self.dialogs.append((kind, str(title), str(message)))
                return "ok"
            return f

        def ask(kind):
            def f(title="", message="", **kw):
                self.dialogs.append((kind, str(title), str(message)))
                return self.answers.pop(0) if self.answers else True
            return f

        for name in ("showinfo", "showwarning", "showerror"):
            self._patch(messagebox, name, info(name))
        for name in ("askyesno", "askokcancel", "askyesnocancel", "askquestion", "askretrycancel"):
            self._patch(messagebox, name, ask(name))

        def filed(kind):
            def f(*a, **kw):
                self.dialogs.append((kind, str(kw.get("title", "")), ""))
                return self.files.pop(0) if self.files else ""
            return f

        for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
            self._patch(filedialog, name, filed(name))
        self._patch(filedialog, "askopenfilenames", lambda *a, **kw: tuple(self.files.pop(0)) if self.files else ())

        self.app = kp.App()
        self.app.geometry("1400x900+0+0")
        self.pump()

    def _patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self._patches.append(p)

    # ── event loop ──
    def pump(self, n: int = 3):
        for _ in range(n):
            self.app.update_idletasks()
            self.app.update()

    def close(self):
        try:
            # Cancel pending after() callbacks (Inbox poll, nickname detect, ...)
            for aid in self.app.tk.splitlist(self.app.tk.call("after", "info")):
                self.app.after_cancel(aid)
            for w in self.windows():
                w.destroy()
            self.app.destroy()
        except tk.TclError:
            pass
        for p in reversed(self._patches):
            p.stop()

    # ── dialogs ──
    def answer(self, *values):
        self.answers.extend(values)

    def errors(self):
        return [d for d in self.dialogs if d[0] == "showerror"]

    def last_dialog(self, kind: str | None = None):
        for d in reversed(self.dialogs):
            if kind is None or d[0] == kind:
                return d
        return None

    # ── widgets ──
    def walk(self, root=None):
        root = root or self.app
        stack = [root]
        while stack:
            w = stack.pop()
            yield w
            stack.extend(w.winfo_children())

    def windows(self) -> list[tk.Toplevel]:
        return [w for w in self.app.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]

    def last_window(self) -> tk.Toplevel:
        wins = self.windows()
        if not wins:
            raise AssertionError("no dialog window is open")
        return wins[-1]

    def find_buttons(self, root, text: str):
        text = text.lower()
        return [w for w in self.walk(root)
                if isinstance(w, (ttk.Button, tk.Button)) and text in _widget_text(w).lower()]

    def click(self, root, text: str):
        """Invoke the first button under ``root`` whose label contains ``text``."""
        found = self.find_buttons(root, text)
        if not found:
            labels = sorted({_widget_text(w) for w in self.walk(root) if isinstance(w, (ttk.Button, tk.Button))})
            raise AssertionError(f"no button containing {text!r}; buttons: {labels}")
        found[0].invoke()
        self.pump()

    def click_later(self, text: str, delay_ms: int = 200):
        """For buttons in modal dialogs that block (wait_window): click once it appears."""
        def attempt(tries=25):
            for w in reversed(self.windows()):
                if self.find_buttons(w, text):
                    self.find_buttons(w, text)[0].invoke()
                    return
            if tries:
                self.app.after(100, attempt, tries - 1)
        self.app.after(delay_ms, attempt)

    def trees(self, root=None) -> list[ttk.Treeview]:
        return [w for w in self.walk(root) if isinstance(w, ttk.Treeview)]

    def rows(self, tree: ttk.Treeview) -> list[tuple]:
        return [tuple(tree.item(i, "values")) for i in tree.get_children()]

    def select_tab(self, word: str):
        nb = self.app.notebook
        for t in nb.tabs():
            if word.lower() in nb.tab(t, "text").lower():
                nb.select(t)
                self.pump()
                return
        raise AssertionError(f"no tab {word!r}")


class GuiTestCase(unittest.TestCase):
    """Fresh practice library and App for every test."""

    def setUp(self):
        if not display_available():
            self.skipTest("no display available for Tk (run under xvfb-run)")
        self._tmp = tempfile.TemporaryDirectory()
        self.lib = build(Path(self._tmp.name))
        self.h = AppHarness(self.lib)

    def tearDown(self):
        self.h.close()
        self._tmp.cleanup()

    def run_import(self):
        """Import everything in the zips folder the way the Import tab does."""
        self.h.app.run()
        self.h.pump()
