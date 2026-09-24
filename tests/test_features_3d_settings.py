"""GUI-driven feature tests for the 3D Models tab and the Settings tab.

Run:  DISPLAY=:99 python3.12 -m unittest tests.test_features_3d_settings -v

Tests that document a confirmed bug assert the CORRECT behaviour and are
marked ``@unittest.expectedFailure`` with a ``# BUG:`` comment, so the suite
stays green while the bug exists and flips to "unexpected success" once fixed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_harness import GuiTestCase, kp  # noqa: E402
from practice_lib import STEP, footprint  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────

def sexpr_balanced(txt: str) -> bool:
    """True if parentheses balance outside of quoted strings and the file is one top-level form."""
    depth, in_str, esc, closed_top = 0, False, False, 0
    for ch in txt:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            if depth == 0 and closed_top:
                return False          # a second top-level form
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
            if depth == 0:
                closed_top += 1
    return depth == 0 and not in_str and closed_top == 1


def model_paths(fp: Path) -> list[str]:
    txt = fp.read_text(encoding="utf-8")
    return [a or b for a, b in re.findall(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)]


def snapshot(root: Path) -> dict:
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


class ThreeDBase(GuiTestCase):
    """Common helpers for driving the 3D Models tab."""

    _click_time = 1_000_000

    def setUp(self):
        super().setUp()
        # Stop the tab's deferred "first visit" auto-scan from firing mid-test
        # and wiping tick state; tests scan explicitly with the Scan button.
        self.h.app._3d_tab_first_visit_done = True
        self.h.select_tab("3d models")

    # widgets
    def tab_frame(self, word: str):
        nb = self.h.app.notebook
        for t in nb.tabs():
            if word.lower() in nb.tab(t, "text").lower():
                return self.h.app.nametowidget(t)
        raise AssertionError(word)

    def press(self, root, label: str):
        """Invoke the button whose label (emoji stripped) equals ``label``."""
        want = label.lower()
        for w in self.h.walk(root):
            if isinstance(w, (ttk.Button, tk.Button)):
                txt = re.sub(r"[^\w\s…./${}()-]", "", str(w.cget("text"))).strip().lower()
                if txt == want or txt.startswith(want + " (") or txt == want + "…":
                    w.invoke()
                    self.h.pump()
                    return w
        raise AssertionError(f"no button {label!r} under {root}")

    @property
    def tab(self):
        return self.tab_frame("3d models")

    def scan(self):
        self.press(self.tab, "Scan")

    def rows(self) -> dict:
        """fp_name -> (checkbox, status_text, current, resolved, suggested, confidence)."""
        t = self.h.app._3d_tree
        out = {}
        for iid in t.get_children():
            v = t.item(iid, "values")
            out[v[1]] = (t.item(iid, "text"), v[0], v[2], v[3], v[4], v[5])
        return out

    def info(self, name) -> dict:
        for i in self.h.app._3d_results:
            if i["fp_name"] == name:
                return i
        raise AssertionError(name)

    def tick_row(self, fp_name: str):
        """Real click on the checkbox (#0) cell of a row."""
        t = self.h.app._3d_tree
        for iid in t.get_children():
            if t.item(iid, "values")[1] == fp_name:
                t.see(iid)
                self.h.pump()
                x, y, w, hgt = t.bbox(iid, "#0")
                # distinct, well-spaced timestamps so Tk never sees a <Double-1>
                ThreeDBase._click_time += 5000
                t.event_generate("<Button-1>", x=x + w // 2, y=y + hgt // 2, time=ThreeDBase._click_time)
                t.event_generate("<ButtonRelease-1>", x=x + w // 2, y=y + hgt // 2, time=ThreeDBase._click_time + 50)
                self.h.pump()
                return
        raise AssertionError(f"row {fp_name} not visible")

    def select_row(self, fp_name: str):
        t = self.h.app._3d_tree
        for iid in t.get_children():
            if t.item(iid, "values")[1] == fp_name:
                t.selection_set(iid)
                t.focus(iid)
                self.h.pump()
                return
        raise AssertionError(fp_name)

    def fp(self, name) -> Path:
        return self.lib.pretty / f"{name}.kicad_mod"

    def resolves(self, name) -> bool:
        p = model_paths(self.fp(name))[0]
        _, ok = kp.resolve_model_path(p, self.lib.pretty, self.lib.personal)
        return ok

    def assert_valid_fp(self, name, n_models=1):
        txt = self.fp(name).read_text(encoding="utf-8")
        self.assertTrue(sexpr_balanced(txt), f"{name} unbalanced:\n{txt}")
        self.assertEqual(len(model_paths(self.fp(name))), n_models, txt)

    def add_fp(self, name, npads=4, model=None, raw=None):
        self.fp(name).write_text(raw if raw is not None else footprint(name, npads, model), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# 3D MODELS TAB — scan, classification, filters, search, selection
# ═══════════════════════════════════════════════════════════════════════════

class ScanTests(ThreeDBase):
    def test_scan_classifies_practice_footprints(self):
        self.scan()
        r = {i["fp_name"]: i for i in self.h.app._3d_results}
        self.assertEqual(set(r), {"SOIC127P600X175-8N", "SOT-23-3_legacy", "QFN-20_4x4mm",
                                  "SW_Tactile_6x6", "Crystal_3225"})
        self.assertEqual((r["SOIC127P600X175-8N"]["status"], r["SOIC127P600X175-8N"]["corrupted"]), ("ok", False))
        self.assertEqual((r["SOT-23-3_legacy"]["status"], r["SOT-23-3_legacy"]["corrupted"]), ("ok", True))
        self.assertEqual(r["QFN-20_4x4mm"]["status"], "missing")
        self.assertEqual(r["QFN-20_4x4mm"]["suggested_method"], "exact")
        self.assertEqual(r["QFN-20_4x4mm"]["suggested"].name, "QFN-20_4x4mm.step")
        self.assertEqual(r["SW_Tactile_6x6"]["status"], "no_path")
        self.assertEqual(r["SW_Tactile_6x6"]["suggested_method"], "fuzzy")
        self.assertEqual(r["SW_Tactile_6x6"]["suggested"].name, "SW_Tactile_6x6mm.step")
        self.assertEqual(r["Crystal_3225"]["status"], "missing")
        self.assertIsNone(r["Crystal_3225"]["suggested"])

        rows = self.rows()
        self.assertEqual(rows["SOIC127P600X175-8N"][1], "✅ OK")
        self.assertIn("Corrupted", rows["SOT-23-3_legacy"][1])
        self.assertIn("exact match", rows["QFN-20_4x4mm"][1])
        self.assertIn("fuzzy", rows["SW_Tactile_6x6"][1])
        self.assertEqual(rows["Crystal_3225"][1], "❌ Missing file")
        self.assertEqual(rows["QFN-20_4x4mm"][5], "100% exact")
        self.assertEqual(self.h.errors(), [])

    def test_scan_modifies_no_files(self):
        before = snapshot(self.lib.personal)
        self.scan()
        self.scan()
        self.assertEqual(snapshot(self.lib.personal), before)

    def test_fix_corrupted_button_enabled_only_when_needed(self):
        btn = self.h.app._3d_fix_corrupted_btn
        self.scan()
        self.assertIn("(1)", str(btn.cget("text")))
        self.assertNotIn("disabled", btn.state())
        fixed = self.fp("SOT-23-3_legacy").read_text().replace("..\\3d\\step\\", "../3d/step/")
        self.fp("SOT-23-3_legacy").write_text(fixed)
        self.scan()
        self.assertIn("disabled", btn.state())

    @unittest.expectedFailure
    def test_summary_ok_count_matches_ok_filter(self):
        # BUG: summary counts corrupted-but-resolving rows as "OK" while the "✅ OK" filter hides them (says "kept in sync")
        self.scan()
        m = re.search(r"✅ (\d+) OK", self.h.app._3d_summary_var.get())
        self.h.app._3d_filter_var.set("✅ OK")
        self.h.pump()
        self.assertEqual(int(m.group(1)), len(self.rows()))

    def test_show_filter(self):
        self.scan()
        expect = {
            "All": {"SOIC127P600X175-8N", "SOT-23-3_legacy", "QFN-20_4x4mm", "SW_Tactile_6x6", "Crystal_3225"},
            "Issues only (everything except OK)": {"SOT-23-3_legacy", "QFN-20_4x4mm", "SW_Tactile_6x6", "Crystal_3225"},
            "✅ OK": {"SOIC127P600X175-8N"},
            "🔧 Corrupted (backslash)": {"SOT-23-3_legacy"},
            "❌ Missing file": {"Crystal_3225"},
            "❓ No path": set(),
            "🔍 Suggested by search": {"QFN-20_4x4mm", "SW_Tactile_6x6"},
            "⚠️ Not found": {"Crystal_3225"},
        }
        for flt, names in expect.items():
            with self.subTest(flt):
                self.h.app._3d_filter_var.set(flt)
                self.h.pump()
                self.assertEqual(set(self.rows()), names)

    def test_search_matches_name_path_and_suggestion(self):
        self.scan()
        for q, names in [("tactile", {"SW_Tactile_6x6"}), ("ABM8", {"Crystal_3225"}),
                         ("qfn20", {"QFN-20_4x4mm"}), ("6x6mm", {"SW_Tactile_6x6"}),
                         ("zzz", set()), ("", None)]:
            with self.subTest(q):
                self.h.app._3d_search_var.set(q)
                self.h.pump()
                if names is None:
                    self.assertEqual(len(self.rows()), 5)
                else:
                    self.assertEqual(set(self.rows()), names)

    def test_select_all_suggested_all_none(self):
        self.scan()
        self.press(self.tab, "Select All Suggested")
        self.assertEqual(self.h.app._3d_checked, {"QFN-20_4x4mm", "SW_Tactile_6x6"})
        self.assertEqual({n for n, r in self.rows().items() if r[0] == "☑"}, {"QFN-20_4x4mm", "SW_Tactile_6x6"})
        self.press(self.tab, "Select All")
        self.assertEqual(len(self.h.app._3d_checked), 5)
        self.assertTrue(all(r[0] == "☑" for r in self.rows().values()))
        self.press(self.tab, "Select None")
        self.assertEqual(self.h.app._3d_checked, set())
        self.assertTrue(all(r[0] == "☐" for r in self.rows().values()))

    def test_click_checkbox_toggles(self):
        self.scan()
        self.tick_row("QFN-20_4x4mm")
        self.assertEqual(self.h.app._3d_checked, {"QFN-20_4x4mm"})
        self.assertEqual(self.rows()["QFN-20_4x4mm"][0], "☑")
        self.tick_row("QFN-20_4x4mm")
        self.assertEqual(self.h.app._3d_checked, set())


# ═══════════════════════════════════════════════════════════════════════════
# Apply Suggested to Checked
# ═══════════════════════════════════════════════════════════════════════════

class ApplySuggestedTests(ThreeDBase):
    def test_apply_relative_writes_correct_paths_and_rescan_ok(self):
        self.h.app.rel_models.set(True)
        self.scan()
        untouched = {n: self.fp(n).read_bytes() for n in ("SOIC127P600X175-8N", "SOT-23-3_legacy", "Crystal_3225")}
        self.press(self.tab, "Select All Suggested")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(self.h.last_dialog()[0:2], ("showinfo", "Done"))
        self.assertIn("Updated 2", self.h.last_dialog()[2])
        self.assertEqual(model_paths(self.fp("QFN-20_4x4mm")), ["../3d/step/QFN-20_4x4mm.step"])
        self.assertEqual(model_paths(self.fp("SW_Tactile_6x6")), ["../3d/step/SW_Tactile_6x6mm.step"])
        for n in ("QFN-20_4x4mm", "SW_Tactile_6x6"):
            self.assert_valid_fp(n)
        # transforms of the replaced block survive
        self.assertIn("(rotate (xyz 0 0 0))", self.fp("QFN-20_4x4mm").read_text())
        for n, b in untouched.items():
            self.assertEqual(self.fp(n).read_bytes(), b, n)
        # rescan (Apply does it) shows both OK
        self.assertEqual(self.info("QFN-20_4x4mm")["status"], "ok")
        self.assertEqual(self.info("SW_Tactile_6x6")["status"], "ok")
        self.assertEqual(self.h.app._3d_checked, set())

    def test_apply_non_relative_writes_resolving_non_relative_path(self):
        self.h.app.rel_models.set(False)
        self.scan()
        self.press(self.tab, "Select All Suggested")
        self.press(self.tab, "Apply Suggested to Checked")
        for n in ("QFN-20_4x4mm", "SW_Tactile_6x6"):
            p = model_paths(self.fp(n))[0]
            self.assertTrue(p.startswith("${PERSONAL_LIB_DIR}/") or os.path.isabs(p), p)
            self.assertTrue(self.resolves(n), p)
            self.assert_valid_fp(n)
            self.assertEqual(self.info(n)["status"], "ok")

    def test_apply_only_touches_ticked(self):
        self.scan()
        sw_before = self.fp("SW_Tactile_6x6").read_bytes()
        self.tick_row("QFN-20_4x4mm")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(model_paths(self.fp("QFN-20_4x4mm")), ["../3d/step/QFN-20_4x4mm.step"])
        self.assertEqual(self.fp("SW_Tactile_6x6").read_bytes(), sw_before)

    def test_apply_cancel_and_nothing_selected(self):
        self.scan()
        before = snapshot(self.lib.pretty)
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(self.h.last_dialog()[1], "Nothing selected")
        self.press(self.tab, "Select All Suggested")
        self.h.answer(False)
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(self.h.last_dialog()[0], "askyesno")
        self.assertEqual(snapshot(self.lib.pretty), before)

    def test_select_all_apply_skips_ok_and_unmatched(self):
        self.scan()
        before = {n: self.fp(n).read_bytes() for n in ("SOIC127P600X175-8N", "SOT-23-3_legacy", "Crystal_3225")}
        self.press(self.tab, "Select All")
        self.press(self.tab, "Apply Suggested to Checked")
        msg = self.h.last_dialog()[2]
        self.assertIn("Updated 2", msg)
        for n, b in before.items():
            self.assertEqual(self.fp(n).read_bytes(), b)

    @unittest.expectedFailure
    def test_select_all_apply_summary_counts(self):
        # BUG: OK rows have no suggestion, so they are counted as "Skipped (no match)" before the "already OK" check
        self.scan()
        self.press(self.tab, "Select All")
        self.press(self.tab, "Apply Suggested to Checked")
        msg = self.h.last_dialog()[2]
        self.assertIn("Skipped (no match): 1", msg)
        self.assertIn("Skipped (already OK): 2", msg)

    def test_apply_twice_is_idempotent(self):
        self.scan()
        self.press(self.tab, "Select All Suggested")
        self.press(self.tab, "Apply Suggested to Checked")
        snap = snapshot(self.lib.pretty)
        self.press(self.tab, "Select All Suggested")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual({k: v[0] for k, v in snapshot(self.lib.pretty).items()},
                         {k: v[0] for k, v in snap.items()})

    # ── edge cases ──
    def test_wrl_model_ok_and_wrl_suggestion(self):
        wrl = self.lib.models / "wrl"
        wrl.mkdir(exist_ok=True)
        (wrl / "LED_0603.wrl").write_text("#VRML V2.0 utf8\n")
        (wrl / "Buzzer_12mm.wrl").write_text("#VRML V2.0 utf8\n")
        self.add_fp("LED_0603", 2, "../3d/wrl/LED_0603.wrl")
        self.add_fp("Buzzer_12mm", 2)
        self.scan()
        self.assertEqual(self.info("LED_0603")["status"], "ok")
        self.assertEqual(self.info("Buzzer_12mm")["suggested"].name, "Buzzer_12mm.wrl")
        self.h.app.rel_models.set(False)
        self.tick_row("Buzzer_12mm")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(model_paths(self.fp("Buzzer_12mm")), ["${PERSONAL_LIB_DIR}/3d/wrl/Buzzer_12mm.wrl"])
        self.assertEqual(self.info("Buzzer_12mm")["status"], "ok")

    def test_uppercase_STEP_extension(self):
        (self.lib.models / "step" / "TO-220.STEP").write_text(STEP)
        self.add_fp("TO-220", 3)
        self.scan()
        self.assertEqual(self.info("TO-220")["suggested"].name, "TO-220.STEP")
        self.tick_row("TO-220")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(model_paths(self.fp("TO-220")), ["../3d/step/TO-220.STEP"])
        self.assertEqual(self.info("TO-220")["status"], "ok")

    def test_spaces_in_paths(self):
        d = self.lib.models / "step" / "My Parts"
        d.mkdir()
        (d / "Relay SPDT.step").write_text(STEP)
        self.add_fp("Relay SPDT", 5)
        self.add_fp("Relay OK", 5, "../3d/step/My Parts/Relay SPDT.step")
        self.scan()
        self.assertEqual(self.info("Relay OK")["status"], "ok")
        self.tick_row("Relay SPDT")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(model_paths(self.fp("Relay SPDT")), ["../3d/step/My Parts/Relay SPDT.step"])
        self.assert_valid_fp("Relay SPDT")
        self.assertEqual(self.info("Relay SPDT")["status"], "ok")

    def test_unquoted_model_path(self):
        raw = footprint("Unq", 2, "X").replace('(model "X"', "(model ../3d/step/SOT-23.step")
        self.add_fp("Unq", raw=raw)
        self.scan()
        self.assertEqual(self.info("Unq")["status"], "ok")

    @unittest.expectedFailure
    def test_env_var_model_path_is_resolved(self):
        # BUG: resolve_model_path only knows ${PERSONAL_LIB_DIR}; ${KICAD9_3DMODEL_DIR}/... is treated as relative and flagged "missing"
        stock = self.lib.root / "kicad-stock-3d"
        (stock / "Package_SO.3dshapes").mkdir(parents=True)
        (stock / "Package_SO.3dshapes" / "SOIC-8_3.9x4.9mm_P1.27mm.step").write_text(STEP)
        self.add_fp("SOIC-8_stock", 8, "${KICAD9_3DMODEL_DIR}/Package_SO.3dshapes/SOIC-8_3.9x4.9mm_P1.27mm.step")
        with mock.patch.dict(os.environ, {"KICAD9_3DMODEL_DIR": str(stock)}):
            self.scan()
        self.assertEqual(self.info("SOIC-8_stock")["status"], "ok")

    @unittest.expectedFailure
    def test_two_model_blocks_apply_does_not_duplicate(self):
        # BUG: analysis/apply only look at the FIRST model block; replacing it can duplicate the second block's model
        (self.lib.models / "step" / "Dual_5x5.step").write_text(STEP)
        raw = footprint("Dual_5x5", 4, "../3d/wrl/Dual_5x5.wrl")  # missing wrl first
        raw = raw.rstrip()[:-1] + ('  (model "../3d/step/Dual_5x5.step"\n    (offset (xyz 0 0 0))\n'
                                   '    (scale (xyz 1 1 1))\n    (rotate (xyz 0 0 90))\n  )\n)\n')
        self.add_fp("Dual_5x5", raw=raw)
        self.scan()
        self.tick_row("Dual_5x5")
        self.press(self.tab, "Apply Suggested to Checked")
        paths = model_paths(self.fp("Dual_5x5"))
        self.assertTrue(sexpr_balanced(self.fp("Dual_5x5").read_text()))
        self.assertEqual(len(paths), len(set(paths)), f"duplicated model blocks: {paths}")

    @unittest.expectedFailure
    def test_fuzzy_does_not_suggest_wrong_package(self):
        # BUG: fuzzy matcher confidently suggests SOT-23 (3-pin) for SOT-23-5 and the SOIC-8 model for SOIC-14; Select All Suggested ticks them
        self.add_fp("SOT-23-5", 5)
        self.add_fp("SOIC127P600X175-14N", 8)
        self.scan()
        self.press(self.tab, "Select All Suggested")
        wrong = {n: (self.info(n)["suggested"].name, round(self.info(n)["suggested_confidence"], 2))
                 for n in ("SOT-23-5", "SOIC127P600X175-14N") if self.info(n)["suggested"] is not None}
        self.assertEqual(wrong, {})
        self.assertNotIn("SOT-23-5", self.h.app._3d_checked)

    @unittest.expectedFailure
    def test_non_relative_apply_keeps_subfolder(self):
        # BUG: rel_models off writes ${PERSONAL_LIB_DIR}/3d/<step|wrl>/<name>, dropping sub-folders -> path doesn't resolve
        sub = self.lib.models / "step" / "vendor"
        sub.mkdir()
        (sub / "BGA-64_8x8.step").write_text(STEP)
        self.add_fp("BGA-64_8x8", 8)
        self.h.app.rel_models.set(False)
        self.scan()
        self.tick_row("BGA-64_8x8")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertTrue(self.resolves("BGA-64_8x8"), model_paths(self.fp("BGA-64_8x8")))


# ═══════════════════════════════════════════════════════════════════════════
# Manual Search… dialog
# ═══════════════════════════════════════════════════════════════════════════

class ManualSearchTests(ThreeDBase):
    def open_for(self, name):
        self.select_row(name)
        self.press(self.tab, "Manual Search")
        win = self.h.last_window()
        self.assertIn(name, win.title())
        return win

    def test_no_selection_shows_info(self):
        self.scan()
        self.h.app._3d_tree.selection_set(())
        self.press(self.tab, "Manual Search")
        self.assertEqual(self.h.last_dialog()[1], "No selection")
        self.assertEqual(self.h.windows(), [])

    def test_prefilter_and_use_selected_from_list(self):
        self.scan()
        win = self.open_for("QFN-20_4x4mm")
        mtree = self.h.trees(win)[0]
        self.assertEqual([r[0] for r in self.h.rows(mtree)], ["QFN-20_4x4mm"])
        self.press(win, "Use selected model")
        self.assertFalse(win.winfo_exists())
        row = self.rows()["QFN-20_4x4mm"]
        self.assertEqual((row[0], row[1], row[5]), ("☑", "🎯 Manual pick", "manual"))
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(model_paths(self.fp("QFN-20_4x4mm")), ["../3d/step/QFN-20_4x4mm.step"])
        self.assertEqual(self.info("QFN-20_4x4mm")["status"], "ok")
        self.assertEqual(self.h.app._3d_manual_picks, {})

    def test_empty_prefilter_then_warns_and_filter_edit_lists_models(self):
        self.scan()
        win = self.open_for("Crystal_3225")
        mtree = self.h.trees(win)[0]
        self.assertEqual(self.h.rows(mtree), [])
        self.press(win, "Use selected model")
        self.assertEqual(self.h.last_dialog()[1], "No selection")
        self.assertTrue(win.winfo_exists())
        entry = [w for w in self.h.walk(win) if isinstance(w, ttk.Entry)][0]
        entry.delete(0, "end")
        self.h.pump()
        self.assertEqual(len(self.h.rows(mtree)), 4)
        # pick SOT-23 explicitly from the list
        for iid in mtree.get_children():
            if mtree.item(iid, "values")[0] == "SOT-23":
                mtree.selection_set(iid)
        self.h.pump()
        self.press(win, "Use selected model")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(model_paths(self.fp("Crystal_3225")), ["../3d/step/SOT-23.step"])
        self.assert_valid_fp("Crystal_3225")

    def test_browse_inside_library(self):
        (self.lib.models / "step" / "XTAL_3.2x2.5.step").write_text(STEP)
        self.scan()
        self.assertIsNone(self.info("Crystal_3225")["suggested"])
        win = self.open_for("Crystal_3225")
        self.h.files.append(str(self.lib.models / "step" / "XTAL_3.2x2.5.step"))
        self.press(win, "Browse for file")
        self.press(win, "Use selected model")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(model_paths(self.fp("Crystal_3225")), ["../3d/step/XTAL_3.2x2.5.step"])
        self.assertEqual(self.info("Crystal_3225")["status"], "ok")

    @unittest.expectedFailure
    def test_browse_outside_library_non_relative_resolves(self):
        # BUG: with rel_models off a browsed file outside <lib>/3d is written as ${PERSONAL_LIB_DIR}/3d/step/<name> (not copied) -> dangling path
        ext = self.lib.root / "elsewhere"
        ext.mkdir()
        (ext / "ABM8-3225.step").write_text(STEP)
        self.h.app.rel_models.set(False)
        self.scan()
        win = self.open_for("Crystal_3225")
        self.h.files.append(str(ext / "ABM8-3225.step"))
        self.press(win, "Browse for file")
        self.press(win, "Use selected model")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(self.info("Crystal_3225")["status"], "ok", model_paths(self.fp("Crystal_3225")))

    def test_browse_cancel_keeps_state(self):
        self.scan()
        win = self.open_for("Crystal_3225")
        self.press(win, "Browse for file")  # no queued file -> cancelled
        self.press(win, "Cancel")
        self.assertEqual(self.h.app._3d_manual_picks, {})
        self.assertEqual(self.rows()["Crystal_3225"][1], "❌ Missing file")

    @unittest.expectedFailure
    def test_clear_manual_pick_really_clears(self):
        # BUG: do_clear drops _3d_manual_picks but leaves info["suggested"] patched + row ticked -> cleared pick is still applied
        self.scan()
        before = self.fp("Crystal_3225").read_bytes()
        win = self.open_for("Crystal_3225")
        self.h.files.append(str(self.lib.models / "step" / "SOT-23.step"))
        self.press(win, "Browse for file")
        self.press(win, "Use selected model")
        self.assertEqual(self.rows()["Crystal_3225"][1], "🎯 Manual pick")
        win = self.open_for("Crystal_3225")
        self.press(win, "Clear manual pick")
        row = self.rows()["Crystal_3225"]
        self.h.answer(True)
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(self.fp("Crystal_3225").read_bytes(), before, "cleared pick was written")
        self.assertEqual((row[1], row[4]), ("❌ Missing file", ""))


# ═══════════════════════════════════════════════════════════════════════════
# Fix Corrupted Paths… dialog
# ═══════════════════════════════════════════════════════════════════════════

class FixCorruptedTests(ThreeDBase):
    def setUp(self):
        super().setUp()
        # a second corrupted footprint so un-ticking can be tested
        self.add_fp("D_SOD-123_legacy", 2, "..\\3d\\step\\SOIC127P600X175-8N.step")
        # corrupted AND missing
        self.add_fp("Gone_legacy", 2, "..\\3d\\step\\Gone.step")

    def open_dialog(self):
        self.scan()
        self.press(self.tab, "Fix Corrupted Paths")
        win = self.h.last_window()
        self.assertIn("Fix Corrupted", win.title())
        return win

    def test_preview_lists_all_corrupted_ticked(self):
        win = self.open_dialog()
        ftree = self.h.trees(win)[0]
        rows = {ftree.item(i, "values")[0]: (ftree.item(i, "text"), ftree.item(i, "values")[2])
                for i in ftree.get_children()}
        self.assertEqual(rows, {
            "SOT-23-3_legacy": ("☑", "../3d/step/SOT-23.step"),
            "D_SOD-123_legacy": ("☑", "../3d/step/SOIC127P600X175-8N.step"),
            "Gone_legacy": ("☑", "../3d/step/Gone.step"),
        })

    def test_untick_row_fix_only_ticked_and_summary(self):
        win = self.open_dialog()
        ftree = self.h.trees(win)[0]
        d_before = self.fp("D_SOD-123_legacy").read_bytes()
        for iid in ftree.get_children():
            if ftree.item(iid, "values")[0] == "D_SOD-123_legacy":
                x, y, w, hh = ftree.bbox(iid, "#0")
                ftree.event_generate("<Button-1>", x=x + w // 2, y=y + hh // 2)
                self.h.pump()
                self.assertEqual(ftree.item(iid, "text"), "☐")
        self.press(win, "Fix Ticked Footprints")
        self.assertEqual(self.h.last_dialog()[0], "askyesno")
        self.assertIn("2 footprint", self.h.last_dialog()[2])
        self.assertEqual(self.fp("D_SOD-123_legacy").read_bytes(), d_before)
        self.assertEqual(model_paths(self.fp("SOT-23-3_legacy")), ["../3d/step/SOT-23.step"])
        self.assertEqual(model_paths(self.fp("Gone_legacy")), ["../3d/step/Gone.step"])
        for n in ("SOT-23-3_legacy", "Gone_legacy"):
            self.assert_valid_fp(n)
        self.assertIn("(scale (xyz 1 1 1))", self.fp("SOT-23-3_legacy").read_text())
        # rescanned
        self.assertEqual((self.info("SOT-23-3_legacy")["status"], self.info("SOT-23-3_legacy")["corrupted"]), ("ok", False))
        self.assertTrue(self.info("D_SOD-123_legacy")["corrupted"])
        self.assertEqual(self.rows()["SOT-23-3_legacy"][1], "✅ OK")
        # summary window
        summ = self.h.last_window()
        self.assertEqual(summ.title(), "Path Fix Summary")
        texts = [w.get("1.0", "end") for w in self.h.walk(summ) if isinstance(w, tk.Text)]
        labels = [str(w.cget("text")) for w in self.h.walk(summ) if isinstance(w, ttk.Label)]
        self.assertIn("Fixed 2 footprint(s)", " ".join(labels))
        self.assertIn("SOT-23-3_legacy", texts[0])
        self.assertNotIn("D_SOD-123_legacy", texts[0])
        self.press(summ, "Close")
        self.assertIn("(1)", str(self.h.app._3d_fix_corrupted_btn.cget("text")))

    def test_select_none_then_fix_warns(self):
        win = self.open_dialog()
        before = snapshot(self.lib.pretty)
        self.press(win, "Select None")
        self.press(win, "Fix Ticked Footprints")
        self.assertEqual(self.h.last_dialog()[1], "Nothing ticked")
        self.assertTrue(win.winfo_exists())
        self.press(win, "Select All")
        self.h.answer(False)
        self.press(win, "Fix Ticked Footprints")
        self.assertEqual(snapshot(self.lib.pretty), before)

    def test_fix_all_then_apply_suggested_for_missing(self):
        (self.lib.models / "step" / "Gone_legacy.step").write_text(STEP)
        win = self.open_dialog()
        self.press(win, "Fix Ticked Footprints")
        self.h.last_window().destroy()
        self.assertEqual(self.h.app._3d_corrupted_count, 0)
        self.tick_row("Gone_legacy")
        self.press(self.tab, "Apply Suggested to Checked")
        self.assertEqual(model_paths(self.fp("Gone_legacy")), ["../3d/step/Gone_legacy.step"])
        self.assertEqual(self.info("Gone_legacy")["status"], "ok")


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS TAB
# ═══════════════════════════════════════════════════════════════════════════

KICAD_COMMON = {
    "meta": {"filename": "kicad_common.json", "version": 3},
    "environment": {"show_warning_dialog": True,
                    "vars": {"KICAD_USER_TEMPLATE_DIR": "/somewhere/templates"}},
    "graphics": {"canvas_type": 1},
    "system": {"text_editor": ""},
}


def fp_table(*entries) -> str:
    body = "".join(f'  (lib (name "{n}")(type "KiCad")(uri "{u}")(options "")(descr ""))\n' for n, u in entries)
    return "(fp_lib_table\n  (version 7)\n" + body + ")\n"


class SettingsBase(GuiTestCase):
    """Isolate KiCad config lookup: HOME and APPDATA point into a temp dir."""

    def setUp(self):
        self._envtmp = tempfile.TemporaryDirectory()
        self.fake_home = Path(self._envtmp.name) / "fakehome"
        self.appdata = Path(self._envtmp.name) / "AppData" / "Roaming"
        self.fake_home.mkdir(parents=True)
        self.appdata.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k not in ("APPDATA", "LOCALAPPDATA")}
        env["HOME"] = str(self.fake_home)
        env["APPDATA"] = str(self.appdata)
        self._envpatch = mock.patch.dict(os.environ, env, clear=True)
        super().setUp()  # the harness isolates env first; this test's env goes on top
        self._envpatch.start()
        self.h.select_tab("settings")

    def tearDown(self):
        self._envpatch.stop()
        super().tearDown()
        self._envtmp.cleanup()

    def kicad_dir(self, base: Path) -> Path:
        d = base / "kicad" / "9.0"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def settings(self):
        nb = self.h.app.notebook
        for t in nb.tabs():
            if "settings" in nb.tab(t, "text").lower():
                return self.h.app.nametowidget(t)

    def restart(self):
        try:
            self.h.app.destroy()
        except tk.TclError:
            pass
        self.h.app = kp.App()
        self.h.pump()
        return self.h.app


class NicknameTests(SettingsBase):
    def test_detect_from_appdata_fp_lib_table(self):
        d = self.kicad_dir(self.appdata)
        (d / "fp-lib-table").write_text(fp_table(
            ("Resistor_SMD", "${KICAD9_FOOTPRINT_DIR}/Resistor_SMD.pretty"),
            ("MyParts", "${PERSONAL_LIB_DIR}/personal.pretty")))
        self.h.app.nick.set("wrong")
        self.h.click(self.settings(), "Detect")
        self.assertEqual(self.h.app.nick.get(), "MyParts")

    def test_detect_from_linux_config_absolute_uri(self):
        d = self.kicad_dir(self.fake_home / ".config")
        (d / "fp-lib-table").write_text(fp_table(("LinuxLib", str(self.lib.pretty))))
        self.h.app.nick.set("wrong")
        self.h.click(self.settings(), "Detect")
        self.assertEqual(self.h.app.nick.get(), "LinuxLib")

    def test_detect_uses_kicad_common_var(self):
        d = self.kicad_dir(self.appdata)
        (d / "kicad_common.json").write_text(json.dumps({"path": {"MYLIBS": str(self.lib.personal.parent)}}))
        (d / "fp-lib-table").write_text(fp_table(("ViaVar", "${MYLIBS}/personal/personal.pretty")))
        self.h.click(self.settings(), "Detect")
        self.assertEqual(self.h.app.nick.get(), "ViaVar")

    @unittest.expectedFailure
    def test_detect_uses_kicad_common_environment_vars(self):
        # BUG: _kicad_path_vars reads kicad_common.json["path"], but KiCad stores user vars in ["environment"]["vars"]
        d = self.kicad_dir(self.appdata)
        cfg = json.loads(json.dumps(KICAD_COMMON))
        cfg["environment"]["vars"]["MYLIBS"] = str(self.lib.personal.parent)
        (d / "kicad_common.json").write_text(json.dumps(cfg))
        (d / "fp-lib-table").write_text(fp_table(("ViaEnvVar", "${MYLIBS}/personal/personal.pretty")))
        self.h.click(self.settings(), "Detect")
        self.assertEqual(self.h.app.nick.get(), "ViaEnvVar")

    def test_detect_fallback_to_pretty_stem(self):
        self.h.app.nick.set("wrong")
        self.h.click(self.settings(), "Detect")
        self.assertEqual(self.h.app.nick.get(), "personal")

    def test_auto_detect_checkbox_triggers_detection(self):
        d = self.kicad_dir(self.appdata)
        (d / "fp-lib-table").write_text(fp_table(("AutoLib", str(self.lib.pretty))))
        self.assertFalse(self.h.app.auto_nick.get())
        cb = [w for w in self.h.walk(self.settings())
              if isinstance(w, ttk.Checkbutton) and "Auto-detect" in str(w.cget("text"))][0]
        cb.invoke()
        self.h.pump()
        self.assertTrue(self.h.app.auto_nick.get())
        self.assertEqual(self.h.app.nick.get(), "AutoLib")

    @unittest.expectedFailure
    def test_detect_with_unquoted_legacy_name_does_not_misalign(self):
        # BUG: names/uris are regex-extracted separately and zipped; one unquoted (name X) shifts pairing -> wrong nickname
        d = self.kicad_dir(self.appdata)
        (d / "fp-lib-table").write_text(
            "(fp_lib_table\n"
            '  (lib (name Legacy)(type KiCad)(uri "${KICAD9_FOOTPRINT_DIR}/Legacy.pretty")(options "")(descr ""))\n'
            f'  (lib (name "MyParts")(type "KiCad")(uri "{self.lib.pretty}")(options "")(descr ""))\n'
            '  (lib (name "Other")(type "KiCad")(uri "/nowhere/Other.pretty")(options "")(descr ""))\n'
            ")\n")
        self.h.click(self.settings(), "Detect")
        self.assertEqual(self.h.app.nick.get(), "MyParts")


class KicadPathButtonsTests(SettingsBase):
    def test_set_personal_lib_dir_writes_valid_json_and_preserves_keys(self):
        d = self.kicad_dir(self.appdata)
        (d / "kicad_common.json").write_text(json.dumps(KICAD_COMMON, indent=2))
        self.h.click(self.settings(), "Set ${PERSONAL_LIB_DIR}")
        data = json.loads((d / "kicad_common.json").read_text())  # still valid JSON
        for k in ("meta", "graphics", "system"):
            self.assertEqual(data[k], KICAD_COMMON[k])
        self.assertEqual(self.h.last_dialog()[1], "Success")

    @unittest.expectedFailure
    def test_set_personal_lib_dir_where_kicad_reads_it(self):
        # BUG: writes data["path"]["PERSONAL_LIB_DIR"]; KiCad reads user path vars from data["environment"]["vars"]
        d = self.kicad_dir(self.appdata)
        (d / "kicad_common.json").write_text(json.dumps(KICAD_COMMON, indent=2))
        self.h.click(self.settings(), "Set ${PERSONAL_LIB_DIR}")
        data = json.loads((d / "kicad_common.json").read_text())
        self.assertEqual(data["environment"]["vars"].get("PERSONAL_LIB_DIR"), str(self.lib.personal))
        self.assertEqual(data["environment"]["vars"]["KICAD_USER_TEMPLATE_DIR"], "/somewhere/templates")

    @unittest.expectedFailure
    def test_set_personal_lib_dir_linux_config(self):
        # BUG: set_kicad_var / rewrite_fp_table only search %APPDATA%/%LOCALAPPDATA%; ~/.config/kicad (Linux) and macOS are ignored
        d = self.kicad_dir(self.fake_home / ".config")
        (d / "kicad_common.json").write_text(json.dumps(KICAD_COMMON))
        self.h.click(self.settings(), "Set ${PERSONAL_LIB_DIR}")
        self.assertEqual(self.h.last_dialog()[1], "Success", self.h.last_dialog())

    def test_set_var_no_config_reports_info(self):
        self.h.click(self.settings(), "Set ${PERSONAL_LIB_DIR}")
        self.assertEqual(self.h.last_dialog()[:2], ("showinfo", "Info"))

    def test_rewrite_fp_table_points_at_custom_pretty(self):
        d = self.kicad_dir(self.appdata)
        sibling = self.lib.personal.parent / "personal_vendor" / "x.pretty"
        sibling.mkdir(parents=True)
        (d / "fp-lib-table").write_text(fp_table(
            ("Resistor_SMD", "${KICAD9_FOOTPRINT_DIR}/Resistor_SMD.pretty"),
            ("personal", str(self.lib.pretty)),
            ("vendor", str(sibling))))
        self.h.click(self.settings(), "Rewrite fp-lib-table")
        txt = (d / "fp-lib-table").read_text()
        self.assertTrue(sexpr_balanced(txt), txt)
        self.assertIn('(name "personal")(type "KiCad")(uri "${PERSONAL_LIB_DIR}/personal.pretty")', txt)
        self.assertNotIn("footprints.pretty", txt)
        self.assertIn('(uri "${KICAD9_FOOTPRINT_DIR}/Resistor_SMD.pretty")', txt)
        self.assertIn(f'(uri "{sibling}")', txt)

    def test_rewrite_fp_table_idempotent(self):
        d = self.kicad_dir(self.appdata)
        (d / "fp-lib-table").write_text(fp_table(("personal", str(self.lib.pretty))))
        self.h.click(self.settings(), "Rewrite fp-lib-table")
        self.assertEqual(self.h.last_dialog()[1], "Success")
        txt = (d / "fp-lib-table").read_text()
        self.assertIn('(uri "${PERSONAL_LIB_DIR}/personal.pretty")', txt)
        self.h.click(self.settings(), "Rewrite fp-lib-table")
        self.assertEqual(self.h.last_dialog()[1], "Info")
        self.assertEqual((d / "fp-lib-table").read_text(), txt)

    @unittest.expectedFailure
    def test_rewrite_fp_table_file_uri(self):
        # BUG: target is hardcoded <lib>/footprints.pretty; the fallback branch doesn't strip file://, so file:// URIs to personal.pretty are left alone
        d = self.kicad_dir(self.appdata)
        (d / "fp-lib-table").write_text(fp_table(("personal", "file://" + str(self.lib.pretty))))
        self.h.click(self.settings(), "Rewrite fp-lib-table")
        self.assertIn('(uri "${PERSONAL_LIB_DIR}/personal.pretty")', (d / "fp-lib-table").read_text())

    @unittest.expectedFailure
    def test_rewrite_3d_model_paths_uses_configured_pretty(self):
        # BUG: _rewrite_models hardcodes <lib>/footprints.pretty; with personal.pretty it does nothing and says "No changes needed"
        self.h.click(self.settings(), "Rewrite 3D Model Paths")
        self.assertNotEqual(self.h.last_dialog()[1], "Info", self.h.last_dialog())
        self.assertNotIn("\\", model_paths(self.lib.pretty / "SOT-23-3_legacy.kicad_mod")[0])

    @unittest.expectedFailure
    def test_rewrite_3d_model_paths_handles_blocks_with_transforms(self):
        # BUG: _rewrite_models_in_pretty regex only matches "(model "x")" with no offset/scale/rotate -> real footprints never rewritten
        fpd = self.lib.personal / "footprints.pretty"
        shutil.copytree(self.lib.pretty, fpd)
        self.h.app.fp_choice.set("footprints.pretty")
        self.h.pump()
        self.h.click(self.settings(), "Rewrite 3D Model Paths")
        p = model_paths(fpd / "SOT-23-3_legacy.kicad_mod")[0]
        self.assertEqual(p, "${PERSONAL_LIB_DIR}/3d/step/SOT-23.step")

    def test_rewrite_3d_model_paths_transformless_block(self):
        fpd = self.lib.personal / "footprints.pretty"
        fpd.mkdir()
        (fpd / "Bare.kicad_mod").write_text('(footprint "Bare"\n  (layer "F.Cu")\n  (model "..\\3d\\step\\SOT-23.step")\n)\n')
        self.h.click(self.settings(), "Rewrite 3D Model Paths")
        txt = (fpd / "Bare.kicad_mod").read_text()
        self.assertEqual(model_paths(fpd / "Bare.kicad_mod"), ["${PERSONAL_LIB_DIR}/3d/step/SOT-23.step"])
        self.assertTrue(sexpr_balanced(txt), txt)


class PrefsPersistenceTests(SettingsBase):
    def test_nickname_rel_models_persist_after_save(self):
        app = self.h.app
        app.nick.set("CustomNick")
        app.auto_nick.set(False)
        app.rel_models.set(False)
        app.save_prefs()
        app = self.restart()
        self.assertEqual(app.nick.get(), "CustomNick")
        self.assertFalse(app.auto_nick.get())
        self.assertFalse(app.rel_models.get())

    @unittest.expectedFailure
    def test_dedupe_mode_and_prefer_pkg_persist(self):
        # BUG: save_prefs/__init__ never store/restore dedupe_mode or prefer_pkg -> Settings combos reset on every restart
        app = self.h.app
        app.dedupe_mode.set("Pins-only")
        app.prefer_pkg.set("zip")
        app.save_prefs()
        app = self.restart()
        self.assertEqual((app.dedupe_mode.get(), app.prefer_pkg.get()), ("Pins-only", "zip"))

    @unittest.expectedFailure
    def test_settings_saved_when_window_closed(self):
        # BUG: no WM_DELETE_WINDOW/on-close save; prefs are only written by Run Import / Inbox, so Settings edits are lost on close
        app = self.h.app
        app.nick.set("KeepMe")
        handler = app.protocol("WM_DELETE_WINDOW")
        if handler:
            app.tk.call(handler)
        app = self.restart()
        self.assertEqual(app.nick.get(), "KeepMe")


if __name__ == "__main__":
    unittest.main()
