"""GUI feature tests for the Library tab's "Library Tools" row, the matching
Tools-menu items, and the three standalone scripts in tools/.

Every test drives the real buttons against a throwaway practice library and
then checks the result on disk: which symbols / model paths ended up in the
files, whether the files are still a single well-formed S-expression, and
whether unrelated symbols were left alone.

Tests that document a real bug assert the CORRECT behaviour and are marked
``@unittest.expectedFailure`` with a ``# BUG:`` comment, so the suite stays
green until the bug is fixed (at which point unittest reports an
"unexpected success" and the decorator should be removed).

Run:  DISPLAY=:99 python3.12 -m unittest tests.test_features_library_tools -v
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_harness import GuiTestCase, display_available, kp  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


# ─── S-expression helpers ─────────────────────────────────────────────────

def parse_sexpr(text: str):
    """Parse ``text`` into nested lists. Quoted strings become ``str`` values
    (escapes decoded), bare atoms become ``str`` too. Raises ValueError unless
    the text is exactly one balanced root expression."""
    stack: list[list] = [[]]
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "(":
            stack.append([])
            i += 1
        elif c == ")":
            if len(stack) == 1:
                raise ValueError(f"unexpected ')' at offset {i}")
            done = stack.pop()
            stack[-1].append(done)
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j:j + 2])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            if j >= n:
                raise ValueError(f"unterminated string at offset {i}")
            stack[-1].append("".join(buf))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            stack[-1].append(text[i:j])
            i = j
    if len(stack) != 1:
        raise ValueError(f"{len(stack) - 1} unclosed '('")
    roots = stack[0]
    if len(roots) != 1 or not isinstance(roots[0], list):
        raise ValueError(f"expected exactly one root expression, got {len(roots)} top-level items")
    return roots[0]


def top_symbols(path: Path) -> dict:
    """name -> parsed symbol node for the direct children of kicad_symbol_lib."""
    root = parse_sexpr(path.read_text(encoding="utf-8"))
    assert root[0] == "kicad_symbol_lib", root[0]
    return {node[1]: node for node in root[1:] if isinstance(node, list) and node and node[0] == "symbol"}


def props(sym_node) -> dict:
    return {c[1]: c[2] for c in sym_node if isinstance(c, list) and c and c[0] == "property"}


def pins(sym_node) -> int:
    count = 0
    stack = [sym_node]
    while stack:
        node = stack.pop()
        for c in node:
            if isinstance(c, list):
                if c and c[0] == "pin":
                    count += 1
                stack.append(c)
    return count


def model_paths(mod: Path) -> list[str]:
    root = parse_sexpr(mod.read_text(encoding="utf-8"))
    assert root[0] == "footprint", root[0]
    return [c[1] for c in root if isinstance(c, list) and c and c[0] == "model"]


def canon_blocks(path: Path) -> dict:
    """name -> whitespace-normalised raw block, for 'unrelated symbol untouched' checks."""
    txt = path.read_text(encoding="utf-8")
    return {name: re.sub(r"\s+", " ", block).strip() for name, block in kp.parse_symbol_blocks(txt)}


def widget_text(w) -> str:
    try:
        return str(w.cget("text"))
    except tk.TclError:
        return ""


def wait(pump, ms: int):
    """Run the event loop for ``ms`` milliseconds (lets win.after() callbacks fire)."""
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        pump()
        time.sleep(0.01)


# ─── App-driven tests ─────────────────────────────────────────────────────

class LibraryToolsTestBase(GuiTestCase):
    ORIGINAL_SYMBOLS = {"LM358DR", "LM358DR_imp1", "NE555DR", "R_Generic"}

    def setUp(self):
        super().setUp()
        self.h.select_tab("library")
        self.orig_sym_bytes = self.lib.sym_file.read_bytes()
        self.orig_blocks = canon_blocks(self.lib.sym_file)

    # widgets
    def tools_frame(self):
        for w in self.h.walk(self.h.app):
            if isinstance(w, ttk.Labelframe) and widget_text(w) == "Library Tools":
                return w
        raise AssertionError("Library Tools frame not found")

    def tool(self, text: str) -> tk.Toplevel:
        before = set(self.h.windows())
        self.h.click(self.tools_frame(), text)
        new = [w for w in self.h.windows() if w not in before]
        self.assertTrue(new, f"{text!r} opened no window; dialogs: {self.h.dialogs}")
        return new[-1]

    def menu_invoke(self, menu_label: str, item: str):
        menubar = self.h.app.nametowidget(self.h.app["menu"])
        for i in range(menubar.index("end") + 1):
            if menubar.type(i) == "cascade" and menubar.entrycget(i, "label") == menu_label:
                sub = menubar.nametowidget(menubar.entrycget(i, "menu"))
                for j in range(sub.index("end") + 1):
                    if sub.type(j) == "command" and sub.entrycget(j, "label") == item:
                        before = set(self.h.windows())
                        sub.invoke(j)
                        self.h.pump()
                        new = [w for w in self.h.windows() if w not in before]
                        return new[-1] if new else None
        raise AssertionError(f"menu item {menu_label} > {item} not found")

    def widgets(self, root, cls):
        return [w for w in self.h.walk(root) if isinstance(w, cls)]

    def check(self, root, text: str) -> ttk.Checkbutton:
        for w in self.widgets(root, (ttk.Checkbutton, ttk.Radiobutton)):
            if text.lower() in widget_text(w).lower():
                return w
        raise AssertionError(f"no check/radio button {text!r}")

    def type_filter(self, root, text: str):
        entries = [e for e in self.widgets(root, ttk.Entry) if str(e.cget("state")) != "readonly"]
        self.assertTrue(entries, "no filter entry")
        entries[0].delete(0, "end")
        entries[0].insert(0, text)
        self.h.pump()

    def text_of(self, root) -> str:
        return "\n".join(t.get("1.0", "end") for t in self.widgets(root, tk.Text))

    def wait(self, ms=400):
        wait(self.h.pump, ms)

    # assertions
    def assertValidSymbolFile(self, path: Path | None = None):
        path = path or self.lib.sym_file
        try:
            return top_symbols(path)
        except (ValueError, AssertionError) as e:
            self.fail(f"{path.name} is not a valid symbol library: {e}")

    def assertUntouched(self, names):
        now = canon_blocks(self.lib.sym_file)
        for n in names:
            self.assertIn(n, now, f"{n} disappeared")
            self.assertEqual(self.orig_blocks[n], now[n], f"{n} was modified")


class TestViewAndMenu(LibraryToolsTestBase):
    def test_view_library_parts_lists_every_symbol(self):
        win = self.menu_invoke("Tools", "View Library Parts")
        self.assertIsNotNone(win)
        lb = self.widgets(win, tk.Listbox)[0]
        self.assertEqual(sorted(lb.get(0, "end")), sorted(self.ORIGINAL_SYMBOLS))
        self.type_filter(win, "lm358")
        self.assertEqual(set(lb.get(0, "end")), {"LM358DR", "LM358DR_imp1"})

    def test_tools_menu_items_open_same_dialogs_as_buttons(self):
        pairs = [("Audit Footprints (3D)", "Audit Footprints"),
                 ("Fix 3D Paths...", "Fix 3D Paths"),
                 ("Delete Parts...", "Delete Parts")]
        for menu_item, button in pairs:
            w1 = self.menu_invoke("Tools", menu_item)
            t1 = w1.title()
            w1.destroy()
            w2 = self.tool(button)
            self.assertEqual(t1, w2.title())
            w2.destroy()
            self.h.pump()


class TestStandardizeMpn(LibraryToolsTestBase):
    def open(self):
        win = self.tool("Standardize MPN")
        self.wait(400)   # auto-scan runs from win.after(100)
        return win

    def test_scan_finds_manufacturer_part_number_field(self):
        win = self.open()
        rows = {r[0]: r for r in self.h.rows(self.h.trees(win)[0])}
        self.assertEqual(rows["NE555DR"][1:3], ("Manufacturer_Part_Number", "NE555DR"))
        self.assertIn("Will copy to MPN", rows["NE555DR"][3])
        self.assertNotIn("LM358DR", rows)            # already has MPN
        self.assertNotIn("LM358DR_imp1", rows)

    def test_dry_run_default_changes_nothing(self):
        win = self.open()
        self.h.click(win, "Apply Changes")
        self.assertEqual(self.h.last_dialog()[1], "Dry Run")
        self.assertEqual(self.lib.sym_file.read_bytes(), self.orig_sym_bytes)

    def test_apply_copies_ne555_into_mpn_and_keeps_rest(self):
        win = self.open()
        self.check(win, "Dry run").invoke()
        self.h.click(win, "Apply Changes")
        self.assertEqual(self.h.errors(), [])
        syms = self.assertValidSymbolFile()
        self.assertEqual(set(syms), self.ORIGINAL_SYMBOLS)
        p = props(syms["NE555DR"])
        self.assertEqual(p.get("MPN"), "NE555DR")
        self.assertEqual(p.get("Manufacturer_Part_Number"), "NE555DR")   # "remove old" was off
        self.assertEqual(pins(syms["NE555DR"]), 8)
        self.assertUntouched(["LM358DR", "LM358DR_imp1"])
        self.assertEqual(self.lib.sym_file.with_suffix(".kicad_sym.bak").read_bytes(), self.orig_sym_bytes)

    # BUG: "Remove old field" regex deletes only `(property "X" "v"` and leaves its tail `(at..)(effects..))` behind -> unbalanced file
    @unittest.expectedFailure
    def test_apply_with_remove_old_field_moves_value(self):
        win = self.open()
        self.check(win, "Dry run").invoke()
        self.check(win, "Remove old field").invoke()
        self.h.click(win, "Apply Changes")
        syms = self.assertValidSymbolFile()
        p = props(syms["NE555DR"])
        self.assertEqual(p.get("MPN"), "NE555DR")
        self.assertNotIn("Manufacturer_Part_Number", p)


class TestCleanDuplicates(LibraryToolsTestBase):
    # BUG: groups every unit sub-symbol (X_0_1, X_1_1) as a "duplicate" of its parent, so every symbol is listed
    @unittest.expectedFailure
    def test_dialog_lists_only_the_imp_duplicate(self):
        win = self.tool("Clean Duplicates")
        rows = self.h.rows(self.h.trees(win)[0])
        self.assertEqual([(r[0], r[3], r[4]) for r in rows], [("LM358DR", "LM358DR", "LM358DR_imp1")])

    # BUG: deletes unit sub-symbols with a regex that runs to the parent's closing paren -> pins lost, file unbalanced, _imp1 kept
    @unittest.expectedFailure
    def test_clean_removes_imp1_and_keeps_everything_else(self):
        self.tool("Clean Duplicates")
        self.h.click(self.h.last_window(), "Clean Library")
        syms = self.assertValidSymbolFile()
        self.assertEqual(set(syms), {"LM358DR", "NE555DR", "R_Generic"})
        self.assertUntouched(["LM358DR", "NE555DR", "R_Generic"])

    def test_clean_makes_backup_of_original(self):
        self.tool("Clean Duplicates")
        self.h.click(self.h.last_window(), "Clean Library")
        self.assertEqual(self.lib.sym_file.with_suffix(".kicad_sym.bak").read_bytes(), self.orig_sym_bytes)

    def test_cancel_confirmation_changes_nothing(self):
        self.tool("Clean Duplicates")
        self.h.answer(False)
        self.h.click(self.h.last_window(), "Clean Library")
        self.assertEqual(self.lib.sym_file.read_bytes(), self.orig_sym_bytes)


class TestValidateFiles(LibraryToolsTestBase):
    def run_validation(self):
        win = self.tool("Validate Files")
        self.h.click(win, "Run Validation")
        return self.text_of(win)

    def test_practice_library_is_valid(self):
        out = self.run_validation()
        self.assertIn("ALL FILES VALID", out)
        self.assertIn("Found 5 footprint files", out)

    # BUG: symbol count uses re.findall('(symbol "') so unit sub-symbols are counted: reports 12 symbols for a 4-symbol library
    @unittest.expectedFailure
    def test_symbol_count_is_top_level_symbols(self):
        out = self.run_validation()
        self.assertIn("Found 4 symbols", out)

    def test_flags_literal_newline_and_unbalanced_footprints(self):
        (self.lib.pretty / "Broken_NL.kicad_mod").write_text(
            '(footprint "Broken_NL" (layer "F.Cu")\\n  (model "x.step")\\n)\n')
        (self.lib.pretty / "Broken_Paren.kicad_mod").write_text('(footprint "Broken_Paren" (layer "F.Cu")\n')
        out = self.run_validation()
        self.assertIn("Broken_NL.kicad_mod: Literal \\n", out)
        self.assertIn("Broken_Paren.kicad_mod: Mismatched parentheses", out)
        self.assertNotIn("ALL FILES VALID", out)

    # BUG: any `\n` inside a quoted string is reported as corruption, but KiCad itself writes multi-line text as "a\nb"
    @unittest.expectedFailure
    def test_kicad_escaped_newline_in_text_is_not_an_error(self):
        mod = self.lib.pretty / "Label_2line.kicad_mod"
        mod.write_text('(footprint "Label_2line" (layer "F.Cu")\n'
                       '  (fp_text user "LINE1\\nLINE2" (at 0 0) (layer "F.SilkS"))\n)\n')
        parse_sexpr(mod.read_text())   # valid S-expression
        out = self.run_validation()
        self.assertNotIn("Label_2line.kicad_mod: Literal", out)


class TestAssign3DModel(LibraryToolsTestBase):
    FP = "Crystal_3225"

    def assign(self, model: Path, fmt_label: str | None):
        win = self.tool("Assign 3D Model")
        self.type_filter(win, self.FP)
        self.h.click(win, "Select All")
        self.h.files.append(str(model))
        self.h.click(win, "Browse for 3D Model")
        if fmt_label:
            self.check(win, fmt_label).invoke()
        before = {p.name: p.read_bytes() for p in self.lib.pretty.glob("*.kicad_mod") if p.stem != self.FP}
        self.h.click(win, "Assign Model")
        self.assertEqual(self.h.errors(), [])
        after = {p.name: p.read_bytes() for p in self.lib.pretty.glob("*.kicad_mod") if p.stem != self.FP}
        self.assertEqual(before, after, "other footprints were modified")
        return model_paths(self.lib.pretty / f"{self.FP}.kicad_mod")

    def model_file(self) -> Path:
        m = self.lib.models / "step" / "ABM8-3225.step"
        m.write_text("ISO-10303-21;\n")
        return m

    def test_relative_format_replaces_broken_model(self):
        m = self.model_file()
        paths = self.assign(m, "Relative path")
        self.assertEqual(paths, ["../3d/step/ABM8-3225.step"])
        resolved, exists = kp.resolve_model_path(paths[0], self.lib.pretty, self.lib.personal)
        self.assertTrue(exists, resolved)

    def test_absolute_format_writes_full_path(self):
        m = self.model_file()
        paths = self.assign(m, "Absolute path")
        self.assertEqual(paths, [m.as_posix()])  # KiCad paths use forward slashes on every OS

    def test_personal_lib_format_for_model_in_3d_step(self):
        m = self.model_file()
        paths = self.assign(m, None)     # default radio: ${PERSONAL_LIB_DIR}
        self.assertEqual(paths, ["${PERSONAL_LIB_DIR}/3d/step/ABM8-3225.step"])

    # BUG: default ${PERSONAL_LIB_DIR} format assumes the file is in <lib>/3d/step; a model picked elsewhere gets a dangling path (no copy, no warning)
    @unittest.expectedFailure
    def test_personal_lib_format_for_model_outside_library_resolves(self):
        outside = self.lib.root / "vendor_models" / "ABM8-3225.step"
        outside.parent.mkdir()
        outside.write_text("ISO-10303-21;\n")
        paths = self.assign(outside, None)
        self.assertEqual(len(paths), 1)
        resolved, exists = kp.resolve_model_path(paths[0], self.lib.pretty, self.lib.personal)
        self.assertTrue(exists, f"{paths[0]} -> {resolved} does not exist")


class TestRepairRestore(LibraryToolsTestBase):
    def test_validate_reports_valid_library(self):
        win = self.tool("Repair/Restore")
        self.h.click(win, "Validate Library")
        self.assertEqual(self.h.last_dialog()[:2], ("showinfo", "Validation Complete"))
        self.assertIn("4 symbols", self.h.last_dialog()[2])

    # BUG: a whole-file paren mismatch is only logged; it is not counted as an error, so "Library is valid" is still shown
    @unittest.expectedFailure
    def test_validate_reports_unbalanced_library(self):
        self.lib.sym_file.write_text(self.lib.symbols_text().rstrip()[:-1])  # drop final ')'
        win = self.tool("Repair/Restore")
        self.h.click(win, "Validate Library")
        self.assertIn("Parentheses mismatch", self.text_of(win))
        self.assertNotIn(("showinfo", "Validation Complete"), [d[:2] for d in self.h.dialogs])

    def test_rebuild_keeps_all_symbols(self):
        win = self.tool("Repair/Restore")
        self.h.click(win, "Rebuild Library")
        syms = self.assertValidSymbolFile()
        self.assertEqual(set(syms), self.ORIGINAL_SYMBOLS)
        self.assertUntouched(self.ORIGINAL_SYMBOLS)
        self.assertEqual(self.lib.sym_file.with_suffix(".kicad_sym.rebuild_bak").read_bytes(), self.orig_sym_bytes)

    def test_restore_without_backup_errors(self):
        win = self.tool("Repair/Restore")
        self.h.click(win, "Restore from Backup")
        self.assertEqual(self.h.last_dialog()[:2], ("showerror", "Error"))
        self.assertEqual(self.lib.sym_file.read_bytes(), self.orig_sym_bytes)


class TestDeleteParts(LibraryToolsTestBase):
    def delete(self, filter_text: str):
        win = self.tool("Delete Parts")
        self.type_filter(win, filter_text)
        self.h.click(win, "Select Filtered")
        self.h.click(win, "Delete Selected Parts")
        self.assertEqual(self.h.errors(), [])

    def test_delete_one_symbol_makes_backup_and_keeps_others(self):
        self.delete("NE555")
        syms = self.assertValidSymbolFile()
        self.assertEqual(set(syms), self.ORIGINAL_SYMBOLS - {"NE555DR"})
        self.assertUntouched(["LM358DR", "LM358DR_imp1", "R_Generic"])
        self.assertEqual(self.lib.sym_file.with_suffix(".kicad_sym.bak").read_bytes(), self.orig_sym_bytes)
        # library browser refreshed
        shown = {r[0] for r in self.h.rows(self.h.app.lib_tree)}
        self.assertNotIn("NE555DR", shown)

    def test_delete_duplicate_by_exact_filter(self):
        self.delete("LM358DR_imp1")
        syms = self.assertValidSymbolFile()
        self.assertEqual(set(syms), self.ORIGINAL_SYMBOLS - {"LM358DR_imp1"})
        self.assertUntouched(["LM358DR", "NE555DR", "R_Generic"])

    def test_restore_from_backup_brings_deleted_symbol_back(self):
        self.delete("NE555")
        win = self.tool("Repair/Restore")
        self.h.click(win, "Restore from Backup")
        self.assertEqual(self.lib.sym_file.read_bytes(), self.orig_sym_bytes)
        self.assertIn("NE555DR", self.assertValidSymbolFile())

    def test_cancel_confirmation_changes_nothing(self):
        win = self.tool("Delete Parts")
        self.type_filter(win, "NE555")
        self.h.click(win, "Select Filtered")
        self.h.answer(False)
        self.h.click(win, "Delete Selected Parts")
        self.assertEqual(self.lib.sym_file.read_bytes(), self.orig_sym_bytes)
        self.assertFalse(self.lib.sym_file.with_suffix(".kicad_sym.bak").exists())


class TestAuditFootprints(LibraryToolsTestBase):
    def audit_rows(self):
        win = self.tool("Audit Footprints")
        return {r[0]: r for r in self.h.rows(self.h.trees(win)[0])}

    def test_lists_every_footprint_and_flags_missing_model(self):
        rows = self.audit_rows()
        self.assertEqual(set(rows), {p.stem for p in self.lib.pretty.glob("*.kicad_mod")})
        self.assertIn("No models", rows["SW_Tactile_6x6"][1])
        self.assertIn("Has models", rows["SOIC127P600X175-8N"][1])

    # BUG: audit only counts "(model" tokens; footprints whose model file does not exist are reported "Has models"
    @unittest.expectedFailure
    def test_flags_models_whose_file_is_missing(self):
        rows = self.audit_rows()
        for fp in ("QFN-20_4x4mm", "Crystal_3225"):
            self.assertNotIn("Has models", rows[fp][1], fp)


class TestFix3DPaths(LibraryToolsTestBase):
    def open(self):
        win = self.tool("Fix 3D Paths")
        self.wait(400)   # auto-scan runs from win.after(100)
        tree = self.h.trees(win)[0]
        rows = {tree.item(i, "values")[0]: (tree.item(i, "text"),) + tuple(tree.item(i, "values"))
                for i in tree.get_children()}
        return win, tree, rows

    def test_scan_classifies_good_missing_and_mismatched(self):
        _, _, rows = self.open()
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows["SOIC127P600X175-8N"][4], "✓ OK")
        self.assertEqual(rows["SW_Tactile_6x6"][4], "➕ Add")
        self.assertEqual(rows["SW_Tactile_6x6"][0], "☑")
        self.assertEqual(rows["QFN-20_4x4mm"][4], "🔧 Fix Path")

    # BUG: scan ignores the existing path; backslash path (SOT-23) and missing file with no match (Crystal) show "—" and are not flagged
    @unittest.expectedFailure
    def test_scan_flags_backslash_and_unresolvable_paths(self):
        _, _, rows = self.open()
        for fp in ("SOT-23-3_legacy", "Crystal_3225"):
            self.assertNotIn(rows[fp][4], ("—", "✓ OK"), fp)

    def test_apply_adds_relative_model_to_footprint_without_one(self):
        win, _, _ = self.open()
        self.h.click(win, "APPLY CHANGES")
        self.assertEqual(self.h.errors(), [])
        paths = model_paths(self.lib.pretty / "SW_Tactile_6x6.kicad_mod")
        self.assertEqual(paths, ["../3d/step/SW_Tactile_6x6mm.step"])
        self.assertTrue(kp.resolve_model_path(paths[0], self.lib.pretty, self.lib.personal)[1])
        # untouched good footprint
        self.assertEqual(model_paths(self.lib.pretty / "SOIC127P600X175-8N.kicad_mod"),
                         ["../3d/step/SOIC127P600X175-8N.step"])

    def test_apply_with_relative_off_uses_personal_lib_var(self):
        win, _, _ = self.open()
        self.check(win, "Use relative paths").invoke()
        self.h.click(win, "APPLY CHANGES")
        self.assertEqual(model_paths(self.lib.pretty / "SW_Tactile_6x6.kicad_mod"),
                         ["${PERSONAL_LIB_DIR}/3d/step/SW_Tactile_6x6mm.step"])

    # BUG: scan matches by footprint name (finds QFN-20_4x4mm.step, shows "Fix Path") but apply re-matches by the old
    # model filename (QFN20.step), finds nothing and leaves the file unchanged
    @unittest.expectedFailure
    def test_apply_fixes_the_path_it_flagged(self):
        win, _, rows = self.open()
        self.assertEqual(rows["QFN-20_4x4mm"][4], "🔧 Fix Path")
        self.h.click(win, "APPLY CHANGES")
        paths = model_paths(self.lib.pretty / "QFN-20_4x4mm.kicad_mod")
        self.assertEqual(len(paths), 1)
        resolved, exists = kp.resolve_model_path(paths[0], self.lib.pretty, self.lib.personal)
        self.assertTrue(exists, f"{paths[0]} still unresolved")
        self.assertEqual(resolved.name, "QFN-20_4x4mm.step")

    def test_select_none_then_apply_writes_nothing(self):
        before = {p.name: p.read_bytes() for p in self.lib.pretty.glob("*.kicad_mod")}
        win, _, _ = self.open()
        self.h.click(win, "Select None")
        self.h.click(win, "APPLY CHANGES")
        self.assertEqual(self.h.last_dialog()[1], "Nothing Selected")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.lib.pretty.glob("*.kicad_mod")})

    # BUG: manual pick strips old model with non-greedy regex `\(model ....*?\)` that stops at the first ')' inside
    # (offset (xyz ..)) -> leftover `) (scale ...) (rotate ...) )` makes the footprint unbalanced
    @unittest.expectedFailure
    def test_manual_pick_replaces_model_and_keeps_file_valid(self):
        win, tree, _ = self.open()
        item = next(i for i in tree.get_children() if tree.item(i, "values")[0] == "Crystal_3225")
        tree.see(item)
        self.h.pump()
        x, y, w, hgt = tree.bbox(item, "footprint")
        # event_generate can't synthesise <Double-1>, so run the widget's own <Double-1> binding
        # script with the event fields tkinter expects (%# %b %f %h %k %s %t %w %x %y ... %D).
        func = re.search(r"\[(\S+) %#", tree.bind("<Double-1>")).group(1)
        fields = ["0"] + ["??"] * 18
        fields[8], fields[9], fields[14], fields[15] = str(x + 5), str(y + hgt // 2), str(tree), "4"
        tree.tk.call(func, *fields)
        self.h.pump()
        picker = [w for w in win.winfo_children() if isinstance(w, tk.Toplevel)]   # child of the dialog
        self.assertTrue(picker, "double-click did not open the model picker")
        mtree = self.h.trees(picker[-1])[0]
        target = next(i for i in mtree.get_children() if mtree.item(i, "values")[0] == "SOT-23")
        mtree.selection_set(target)
        self.h.click(picker[-1], "USE THIS MODEL")
        self.h.click(win, "APPLY CHANGES")
        paths = model_paths(self.lib.pretty / "Crystal_3225.kicad_mod")
        self.assertEqual(paths, ["../3d/step/SOT-23.step"])


# ─── Standalone tools/ scripts ────────────────────────────────────────────

def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"tool_{name}", REPO / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StandaloneToolTestBase(unittest.TestCase):
    """Patches messagebox/filedialog the way gui_harness does, for tk.Tk apps."""

    def setUp(self):
        if not display_available():
            self.skipTest("no display available for Tk")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.dialogs, self.answers, self.files = [], [], []
        self._patches = []

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

        def filed(*a, **kw):
            return self.files.pop(0) if self.files else ""

        for n in ("showinfo", "showwarning", "showerror"):
            self._patch(messagebox, n, info(n))
        for n in ("askyesno", "askokcancel"):
            self._patch(messagebox, n, ask(n))
        for n in ("askopenfilename", "askdirectory"):
            self._patch(filedialog, n, filed)
        self.app = None

    def _patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self._patches.append(p)

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except tk.TclError:
                pass
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def pump(self):
        self.app.update_idletasks()
        self.app.update()

    def click(self, text: str):
        stack = [self.app]
        while stack:
            w = stack.pop()
            if isinstance(w, (ttk.Button, tk.Button)) and text.lower() in widget_text(w).lower():
                w.invoke()
                self.pump()
                return
            stack.extend(w.winfo_children())
        raise AssertionError(f"no button {text!r}")

    def errors(self):
        return [d for d in self.dialogs if d[0] == "showerror"]


class TestSymbolDeduplicatorScript(StandaloneToolTestBase):
    def setUp(self):
        super().setUp()
        from practice_lib import build
        self.lib = build(self.tmp)
        self.orig = self.lib.sym_file.read_bytes()
        self.orig_blocks = canon_blocks(self.lib.sym_file)
        self.app = load_tool("symbol_deduplicator").SymbolDeduplicator()
        self.files.append(str(self.lib.sym_file))
        self.click("Load Library")

    # BUG: same grouping as the in-app Clean Duplicates: unit sub-symbols (_0_1/_1_1) are treated as duplicates
    @unittest.expectedFailure
    def test_groups_only_the_imp_duplicate(self):
        self.assertEqual(sorted(self.app.duplicates), ["LM358DR"])
        self.assertEqual(self.app.selections["LM358DR"], "LM358DR")

    # BUG: same deletion regex as in-app Clean Duplicates: cuts parent symbols' tails, leaves _imp1, file unbalanced
    @unittest.expectedFailure
    def test_clean_removes_imp1_only_and_file_stays_valid(self):
        self.click("Clean Library")
        syms = top_symbols(self.lib.sym_file)
        self.assertEqual(set(syms), {"LM358DR", "NE555DR", "R_Generic"})
        now = canon_blocks(self.lib.sym_file)
        for n in ("LM358DR", "NE555DR", "R_Generic"):
            self.assertEqual(self.orig_blocks[n], now[n])

    def test_clean_writes_backup(self):
        self.click("Clean Library")
        self.assertEqual(self.lib.sym_file.with_suffix(".kicad_sym.bak").read_bytes(), self.orig)


GOOD_FP = ('(footprint "Good" (version 20221018) (generator pcbnew)\n  (layer "F.Cu")\n'
           '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n'
           '  (model "../3d/step/Good.step"\n    (offset (xyz 0 0 0))\n    (scale (xyz 1 1 1))\n'
           '    (rotate (xyz 0 0 0))\n  )\n)\n')


class TestFixBrokenFootprintsScript(StandaloneToolTestBase):
    def setUp(self):
        super().setUp()
        self.pretty = self.tmp / "lib.pretty"
        self.pretty.mkdir()
        (self.pretty / "Good.kicad_mod").write_text(GOOD_FP)
        self.app = load_tool("fix_broken_footprints").FootprintRepair()

    def scan(self):
        self.files.append(str(self.pretty))
        self.click("Select Footprint Directory")

    def test_repairs_literal_newlines_and_leaves_good_file(self):
        broken = self.pretty / "B2B-XH-A.kicad_mod"
        broken.write_text('(footprint "B2B-XH-A" (layer "F.Cu")\n  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
                          '\\n  (model "../3d/step/B2B-XH-A.step")\\n)')
        self.scan()
        self.assertEqual([p.name for p in self.app.broken_files], ["B2B-XH-A.kicad_mod"])
        self.click("Fix All Broken Files")
        self.assertEqual(self.errors(), [])
        self.assertEqual(model_paths(broken), ["../3d/step/B2B-XH-A.step"])
        self.assertNotIn("\\n", broken.read_text())
        self.assertTrue(broken.with_suffix(".kicad_mod.bak").exists())
        self.assertEqual((self.pretty / "Good.kicad_mod").read_text(), GOOD_FP)

    def test_clean_directory_reports_nothing_to_fix(self):
        self.scan()
        self.assertEqual(self.app.broken_files, [])
        self.click("Fix All Broken Files")
        self.assertEqual(self.dialogs[-1][:2], ("showwarning", "Nothing to Fix"))

    # BUG: blanket replace of every `\n` also hits backslash paths like ..\3d\step\nrf52.step, splitting the filename with a real newline
    @unittest.expectedFailure
    def test_backslash_path_starting_with_n_is_preserved(self):
        broken = self.pretty / "NRF52.kicad_mod"
        broken.write_text('(footprint "NRF52" (layer "F.Cu")\\n  (model "..\\3d\\step\\nrf52832.step")\\n)')
        self.scan()
        self.click("Fix All Broken Files")
        paths = model_paths(broken)
        self.assertEqual(len(paths), 1)
        self.assertNotIn("\n", paths[0])
        self.assertTrue(paths[0].replace("\\", "/").endswith("step/nrf52832.step"), paths[0])


def broken_symbol(name: str, ind: str) -> str:
    """Symbol whose MPN was inserted INSIDE the Reference property (README example)."""
    i1, i2, i3, i4 = ind, ind * 2, ind * 3, ind * 4
    return (f'{i1}(symbol "{name}" (in_bom yes) (on_board yes)\n'
            f'{i2}(property "Reference" "J"\n{i3}(at -12.7 13.462 0)\n{i4}(property "MPN" "{name}-MPN")\n'
            f'{i3}(effects (font (size 1.27 1.27)))\n{i2})\n'
            f'{i2}(property "Value" "{name}" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
            f'{i2}(symbol "{name}_1_1"\n'
            f'{i3}(pin passive line (at 0 0 0) (length 2.54) (name "A" (effects (font (size 1.27 1.27)))) '
            f'(number "1" (effects (font (size 1.27 1.27)))))\n{i2})\n{i1})\n')


def good_symbol(name: str, ind: str) -> str:
    return (f'{ind}(symbol "{name}" (in_bom yes) (on_board yes)\n'
            f'{ind * 2}(property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
            f'{ind * 2}(property "Value" "{name}" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
            f'{ind * 2}(property "MPN" "{name}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
            f'{ind})\n')


class TestRepairBrokenSymbolsScript(StandaloneToolTestBase):
    def setUp(self):
        super().setUp()
        self.app = load_tool("repair_broken_symbols").SymbolRepair()

    def run_repair(self, text: str) -> Path:
        f = self.tmp / "lib.kicad_sym"
        f.write_text(text, encoding="utf-8")
        self.files.append(str(f))
        self.click("Select Symbol File")
        self.click("Repair File")
        self.assertEqual(self.errors(), [])
        return f

    def assertRepaired(self, f: Path, broken: list, good: list):
        syms = top_symbols(f)
        self.assertEqual(set(syms), set(broken) | set(good))
        for n in broken:
            ref = next(c for c in syms[n] if isinstance(c, list) and c[:2] == ["property", "Reference"])
            self.assertFalse(any(isinstance(c, list) and c and c[0] == "property" for c in ref),
                             f"{n}: MPN still nested in Reference")
            self.assertEqual(props(syms[n]).get("MPN"), f"{n}-MPN")
            self.assertEqual(pins(syms[n]), 1)
        for n in good:
            self.assertEqual(props(syms[n]).get("MPN"), n)

    def test_repairs_nested_mpn_two_space_format(self):
        text = ('(kicad_symbol_lib (version 20211014) (generator "x")\n'
                + broken_symbol("J1", "  ") + good_symbol("U_OK", "  ") + broken_symbol("J2", "  ") + ")\n")
        f = self.run_repair(text)
        self.assertRepaired(f, ["J1", "J2"], ["U_OK"])
        self.assertEqual(f.with_suffix(".kicad_sym.bak").read_text(), text)

    def test_clean_file_is_left_alone(self):
        text = '(kicad_symbol_lib (version 20211014) (generator "x")\n' + good_symbol("U_OK", "  ") + ")\n"
        f = self.tmp / "ok.kicad_sym"
        f.write_text(text)
        self.files.append(str(f))
        self.click("Select Symbol File")
        self.click("Repair File")
        self.assertEqual(self.dialogs[-1][:2], ("showinfo", "Nothing to Fix"))
        self.assertEqual(f.read_text(), text)

    # BUG: symbols are split on "\n  (symbol" (two spaces); KiCad 7+/8 tab-indented files become one block and only the first nested MPN is fixed
    @unittest.expectedFailure
    def test_repairs_nested_mpn_tab_indented_kicad8_format(self):
        text = ('(kicad_symbol_lib\n\t(version 20231120)\n\t(generator "kicad_symbol_editor")\n'
                + broken_symbol("J1", "\t") + good_symbol("U_OK", "\t") + broken_symbol("J2", "\t") + ")\n")
        f = self.run_repair(text)
        self.assertRepaired(f, ["J1", "J2"], ["U_OK"])


if __name__ == "__main__":
    unittest.main()
