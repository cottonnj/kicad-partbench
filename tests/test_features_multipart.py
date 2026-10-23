"""GUI feature tests for the Multi-part CPL tab.

Every test drives the real App (tests/gui_harness.py) against a temp project
built from the trimmed M-050 Rev-12 board (tests/fixtures) plus plugin-style
BOM/CPL files, and checks the CSVs ON DISK with the csv module, not with the
app's own helpers.

Run:  DISPLAY=:99 python3.12 -m unittest tests.test_features_multipart -v
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_harness import GuiTestCase, kp  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "m050_rev12_board.kicad_pcb"
CONN = "CONN-SMD_100P-P0.40_10164227-1001A1RLF"
CPL = [
    ["Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer"],
    ["C1", "100nF", "C_0402", "10.0", "20.0", "0.0", "top"],
    ["J7", "GT-B0403FSV14-100B1101", CONN, "33.999911", "86.101148", "0.0", "top"],
    ["Module1", "ComputeModule5-CM5", "Raspberry-Pi-5-Compute-Module", "31.5", "103.102274", "90.0", "top"],
    ["R1", "10k", "R_0402", "1.0", "2.0", "90.0", "top"],
]
BOM = [
    ["Comment", "Designator", "Footprint", "LCSC", "Quantity"],
    ["100nF", "C1", "C_0402", "C1525", "1"],
    ["GT-B0403FSV14-100B1101", "J7", CONN, "C42376244", "1"],
    ["ComputeModule5-CM5", "Module1", "Raspberry-Pi-5-Compute-Module", "", "1"],
    ["10k", "R1", "R_0402", "C25744", "1"],
]


def read_csv(path: Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def write_csv(path: Path, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


class MultipartTabTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        root = Path(self._tmp.name) / "Rev12"
        self.out = root / "jlcpcb" / "production_files"
        self.out.mkdir(parents=True)
        self.pro = root / "board.kicad_pro"
        self.pro.write_text("{}", encoding="utf-8")
        shutil.copy(FIXTURE, root / "board.kicad_pcb")
        self.cpl, self.bom = self.out / "CPL-board.csv", self.out / "BOM-board.csv"
        write_csv(self.cpl, CPL)
        write_csv(self.bom, BOM)
        self.h.select_tab("Multi-part")
        self.tab = self.h.app.nametowidget(self.h.app.notebook.select())

    def load(self):
        self.h.files.append(str(self.pro))
        self.h.click(self.tab, "Browse")

    def load_with_preset(self):
        self.load()
        self.h.click(self.tab, "Apply Preset")

    def sub_rows(self):
        return self.h.rows(self.h.app.mp_sub_tree)

    def test_load_picks_biggest_footprint(self):
        self.load()
        self.assertEqual(self.h.app._mp_current_ref(), "Module1")
        self.assertIn("✓ CPL-board.csv", self.h.app.mp_status.get())
        self.assertEqual(self.h.errors(), [])

    def test_cm5_preset_fills_table(self):
        self.load_with_preset()
        rows = {r[0]: r for r in self.sub_rows()}
        self.assertEqual(set(rows), {"J6", "J7"})
        self.assertEqual(rows["J6"][2], "101-200")
        self.assertEqual(rows["J6"][-1], "33.950 / 120.260 / 0")
        self.assertEqual(rows["J7"][-1], "33.950 / 86.260 / 0")
        # Ghost J7 on the board is reported; J6 sits in the right place
        warn = self.h.app.mp_warn_var.get()
        self.assertIn("J7 is also on the board", warn)
        self.assertIn("48.65 mm", warn)
        self.assertNotIn("J6 is also", warn)

    def test_preview_then_patch(self):
        self.load_with_preset()
        self.h.click(self.tab, "Preview")
        self.assertEqual(str(self.h.app.mp_patch_btn.cget("state")), "normal")
        self.assertEqual(read_csv(self.cpl), CPL)  # Preview writes nothing
        changes = {(r[0], r[1], r[2]) for r in self.h.rows(self.h.app.mp_diff_tree)}
        self.assertIn(("CPL", "removed", "Module1"), changes)
        self.assertIn(("CPL", "added", "J6"), changes)
        self.assertIn(("CPL", "replaced", "J7"), changes)

        self.h.answer(True)
        self.h.click(self.tab, "Patch CSVs")
        self.assertEqual(self.h.errors(), [])
        cpl = read_csv(self.cpl)
        self.assertEqual([r[0] for r in cpl[1:]], ["C1", "J6", "J7", "R1"])
        self.assertEqual(cpl[2], ["J6", "10164227-1001A1RLF", CONN, "33.95", "120.26", "0.0", "top"])
        self.assertEqual(cpl[3], ["J7", "10164227-1001A1RLF", CONN, "33.95", "86.26", "0.0", "top"])
        bom = read_csv(self.bom)
        self.assertEqual(bom[2], ["10164227-1001A1RLF", "J6,J7", CONN, "C6782225", "2"])
        self.assertNotIn("Module1", {r[1] for r in bom})
        self.assertEqual(read_csv(self.out / "CPL-board.csv.bak"), CPL)
        self.assertEqual(read_csv(self.out / "BOM-board.csv.bak"), BOM)
        cfg = json.loads((self.pro.parent / "board.multipart.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["parents"][0]["ref"], "Module1")
        self.assertEqual(cfg["parents"][0]["preset"], kp.CM5_PRESET)

        # The tab re-previews after patching: nothing left to do
        self.assertEqual(str(self.h.app.mp_patch_btn.cget("state")), "disabled")
        kinds = {r[1] for r in self.h.rows(self.h.app.mp_diff_tree)}
        self.assertEqual(kinds, {"unchanged"})

    def test_patch_is_safe_to_rerun_after_reload(self):
        self.load_with_preset()
        self.h.click(self.tab, "Preview")
        self.h.answer(True)
        self.h.click(self.tab, "Patch CSVs")
        once = (read_csv(self.cpl), read_csv(self.bom))
        # Fresh app session: loads the saved config, previews, nothing to patch
        self.h.app._mp_load_project(self.pro)
        self.h.click(self.tab, "Preview")
        self.assertEqual(str(self.h.app.mp_patch_btn.cget("state")), "disabled")
        self.assertEqual((read_csv(self.cpl), read_csv(self.bom)), once)

    def test_csv_changed_after_preview_is_not_overwritten(self):
        self.load_with_preset()
        self.h.click(self.tab, "Preview")
        regenerated = CPL + [["U9", "x", "y", "1", "2", "0.0", "top"]]
        write_csv(self.cpl, regenerated)
        st = os.stat(self.cpl)
        os.utime(self.cpl, (st.st_atime, st.st_mtime + 5))
        self.h.click(self.tab, "Patch CSVs")
        self.assertEqual(self.h.last_dialog()[1], "CSV Changed")
        self.assertEqual(read_csv(self.cpl), regenerated)
        self.assertFalse((self.out / "CPL-board.csv.bak").exists())

    def test_offset_mode_by_dialog_and_canvas_click(self):
        self.load()
        self.h.click(self.tab, "Add Sub-part")
        dlg = self.h.last_window()
        v = self.h.app._mp_edit["vars"]
        v["ref"].set("SH1")
        v["mode"].set("offset")
        v["dx"].set("10")
        v["dy"].set("0")
        self.h.pump()
        self.h.click(dlg, "OK")
        row = self.sub_rows()[0]
        self.assertEqual(row[:2], ("SH1", "offset"))
        # Module1 at (103.95, 92.94, 90): local (10, 0) -> board (103.95, 82.94)
        self.assertEqual(row[-1], f"{103.95 - 48.5:.3f} / {-(82.94 - 179.7):.3f} / 90")

        # Clicking the canvas moves the selected offset-mode part there
        app = self.h.app
        app.mp_sub_tree.selection_set("0")
        self.h.pump()
        fp = app.mp_fps["Module1"]
        bx, by = kp._fp_to_board(fp, 5, -20)
        cx, cy = app._mp_board_to_canvas(bx, by)
        event = type("E", (), {"x": round(cx), "y": round(cy)})
        app._mp_canvas_click(event)
        self.h.pump()
        dx, dy = app._mp_parent_entry()["subparts"][0]["xy_offset_mm"]
        tol = 1.5 / app._mp_view[0]  # one canvas pixel, in mm
        self.assertAlmostEqual(dx, 5, delta=tol)
        self.assertAlmostEqual(dy, -20, delta=tol)

    def test_cancel_new_subpart_removes_it(self):
        self.load()
        self.h.click(self.tab, "Add Sub-part")
        self.h.click(self.h.last_window(), "Cancel")
        self.assertEqual(self.sub_rows(), [])

    def test_bad_pad_range_is_refused(self):
        self.load()
        self.h.click(self.tab, "Add Sub-part")
        dlg = self.h.last_window()
        self.h.app._mp_edit["vars"]["pads"].set("100-1")
        self.h.pump()
        self.h.click(dlg, "OK")
        self.assertTrue(dlg.winfo_exists())  # still open, error shown in the dialog
        texts = [str(w.cget("text")) for w in self.h.walk(dlg) if isinstance(w, ttk.Label)]
        self.assertTrue(any("reversed" in t for t in texts), texts)

    def test_save_as_preset(self):
        self.load_with_preset()
        self.h.click(self.tab, "Save as Preset")
        dlg = self.h.last_window()
        entry = next(w for w in self.h.walk(dlg) if isinstance(w, ttk.Entry))
        entry.delete(0, "end")
        entry.insert(0, "My CM5 carrier")
        self.h.click(dlg, "Save Preset")
        self.assertEqual(self.h.errors(), [])
        saved = json.loads(kp._multipart_presets_file().read_text(encoding="utf-8"))
        sub = saved["My CM5 carrier"]["subparts"][0]
        self.assertEqual(sub["ref"], "J6")
        self.assertEqual(sub["expect_local_mm"], [33.5, -21.5])
        self.assertIn("My CM5 carrier", self.h.app.mp_preset_combo["values"])


if __name__ == "__main__":
    unittest.main()
