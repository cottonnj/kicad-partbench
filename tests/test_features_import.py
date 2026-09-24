"""GUI-driven feature tests for the core import workflow.

Covers the Inbox tab, Import tab (every option), the import log (skip-list and
the View / Reset Import Log dialog), the Results tab (counters, table, context
menu actions), the Library tab browser, File > Export Report and Clear Results.

Every test checks the real outcome on disk (symbol file, .pretty folder, 3D
folder, import log JSON, exported CSV), not just the absence of exceptions.

Known bugs are kept as tests that assert the CORRECT behaviour and are marked
``@unittest.expectedFailure`` with a ``# BUG:`` comment.

Run:  DISPLAY=:99 python3.12 -m unittest tests.test_features_import -v
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_harness import GuiTestCase, kp  # noqa: E402
import practice_lib as pl  # noqa: E402

EXISTING = ("LM358DR", "LM358DR_imp1", "NE555DR", "R_Generic")
# (mpn, footprint, 3D model file name as shipped in the zip) for the 4 zips in zips/
IN_ZIPS = {
    "STM32G431CBU6": ("QFN50P700X700X60-49N", "QFN50P700X700X60-49N.step", "ul_STM32G431CBU6.zip"),
    "TPS62133RGTR": ("QFN50P300X300X100-17N", "TPS62133RGTR.stp", "LIB_TPS62133RGTR.zip"),
    "USB4105-GF-A": ("GCT_USB4105-GF-A", "USB4105-GF-A.step", "USB4105-GF-A.zip"),
    "AP2112K-3.3TRG1": ("SOT95P280X145-5N", "SOT95P280X145-5N.step", "ul_AP2112K-3.3TRG1.zip"),
}
LAYOUT = {"STM32G431CBU6": "ultralibrarian", "TPS62133RGTR": "samacsys", "USB4105-GF-A": "snapeda"}


# ─── S-expression helpers ────────────────────────────────────────────────

def sexpr_problems(text: str) -> list[str]:
    """Return a list of structural problems (empty list = well formed)."""
    problems, depth, roots, in_str, esc = [], 0, 0, False, False
    for ch in text:
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
            if depth == 0:
                roots += 1
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                problems.append("unbalanced: extra ')'")
                depth = 0
    if in_str:
        problems.append("unterminated string")
    if depth:
        problems.append(f"unbalanced: {depth} unclosed '('")
    if roots != 1:
        problems.append(f"{roots} root forms")
    return problems


def symbols(text: str) -> dict[str, str]:
    """Top-level symbol name -> block."""
    return dict(kp.parse_symbol_blocks(text))


def top_names(text: str) -> list[str]:
    return [n for n, _ in kp.parse_symbol_blocks(text)]


def prop(block: str, name: str):
    m = re.search(r'\(property\s+"%s"\s+"([^"]*)"' % re.escape(name), block)
    return m.group(1) if m else None


def props(block: str) -> dict[str, str]:
    return dict(re.findall(r'\(property\s+"([^"]+)"\s+"([^"]*)"', block))


def pin_count(block: str) -> int:
    return len(re.findall(r"\(pin\s+", block))


def unit_names(block: str) -> list[str]:
    return re.findall(r'\(symbol\s+"([^"]+)"', block)[1:]


def models(fp_file: Path) -> list[str]:
    return re.findall(r'\(model\s+"([^"]+)"', fp_file.read_text(encoding="utf-8"))


class ImportTestBase(GuiTestCase):
    """Small helpers over the harness shared by all the test classes."""

    # ── widgets ──
    def tab(self, word: str):
        nb = self.h.app.notebook
        for t in nb.tabs():
            if word.lower() in nb.tab(t, "text").lower():
                return self.h.app.nametowidget(t)
        raise AssertionError(word)

    def checkbutton(self, root, text: str) -> ttk.Checkbutton:
        for w in self.h.walk(root):
            if isinstance(w, ttk.Checkbutton) and text.lower() in str(w.cget("text")).lower():
                return w
        raise AssertionError(f"no checkbutton {text!r}")

    def set_check(self, text: str, value: bool, var, root=None):
        """Toggle a real Import-tab checkbutton (clicking it) until var == value."""
        w = self.checkbutton(root or self.tab("import"), text)
        if bool(var.get()) != value:
            w.invoke()
            self.h.pump()
        self.assertEqual(bool(var.get()), value)

    def menu_invoke(self, cascade: str, item: str):
        menubar = self.h.app.nametowidget(self.h.app.cget("menu"))
        for i in range(menubar.index("end") + 1):
            if menubar.type(i) == "cascade" and menubar.entrycget(i, "label") == cascade:
                sub = self.h.app.nametowidget(menubar.entrycget(i, "menu"))
                for j in range(sub.index("end") + 1):
                    if sub.type(j) == "command" and item in sub.entrycget(j, "label"):
                        sub.invoke(j)
                        self.h.pump()
                        return
        raise AssertionError(f"no menu item {cascade} > {item}")

    def run_button(self):
        self.h.click(self.tab("import"), "Run Import")

    def window(self, title_part: str):
        for w in reversed(self.h.windows()):
            if title_part.lower() in w.title().lower():
                return w
        raise AssertionError(f"no window {title_part!r}; have {[w.title() for w in self.h.windows()]}")

    def window_text(self, win) -> str:
        out = []
        for w in self.h.walk(win):
            if w.winfo_class() == "Text":
                out.append(w.get("1.0", "end"))
            else:
                try:
                    out.append(str(w.cget("text")))
                except Exception:
                    pass
        return "\n".join(out)

    def close_windows(self):
        for w in self.h.windows():
            w.destroy()
        self.h.pump()

    # ── disk ──
    def sym_text(self) -> str:
        return self.lib.symbols_text()

    def log_entries(self) -> dict:
        p = self.lib.personal / kp.ImportLog.FILENAME
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))["packages"]

    def snapshot(self, d: Path) -> dict:
        return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}

    def assert_valid_lib(self):
        text = self.sym_text()
        self.assertEqual(sexpr_problems(text), [], "symbol library is not well formed")
        self.assertTrue(text.lstrip().startswith("(kicad_symbol_lib"))
        names = top_names(text)
        self.assertEqual(len(names), len(set(names)), f"duplicate top-level symbols: {names}")
        return text

    def results_rows(self):
        return self.h.rows(self.h.app.tree)

    def counters(self):
        return {k: v.get() for k, v in self.h.app.sum_vars.items()}


# ═══════════════════════════════════════════════════════════════════════════
# IMPORT TAB: vendor layouts, merge result, existing content
# ═══════════════════════════════════════════════════════════════════════════
class ImportVendorLayoutTests(ImportTestBase):

    def test_import_all_three_layouts_via_run_button(self):
        before = self.sym_text()
        self.run_button()
        self.assertEqual(self.h.errors(), [])
        text = self.assert_valid_lib()
        syms = symbols(text)
        for mpn, (fp, model, _zip) in IN_ZIPS.items():
            with self.subTest(mpn=mpn):
                self.assertIn(mpn, syms)
                # symbol Footprint property autoset to personal:<fp>
                self.assertEqual(prop(syms[mpn], "Footprint"), f"personal:{fp}")
                # footprint copied and still well formed
                fp_file = self.lib.pretty / f"{fp}.kicad_mod"
                self.assertTrue(fp_file.exists())
                self.assertEqual(sexpr_problems(fp_file.read_text()), [])
                # 3D model copied into 3d/step and linked with a relative path that resolves
                self.assertTrue((self.lib.models / "step" / model).exists())
                self.assertEqual(models(fp_file), [f"../3d/step/{model}"])
                self.assertTrue((fp_file.parent / models(fp_file)[0]).resolve().exists())
        # pin counts survive the merge
        self.assertEqual(pin_count(syms["STM32G431CBU6"]), 12)
        self.assertEqual(pin_count(syms["TPS62133RGTR"]), 8)
        self.assertEqual(pin_count(syms["USB4105-GF-A"]), 10)
        # the Downloads zips were NOT imported (they aren't in the source folder)
        self.assertNotIn("BSS138", syms)
        self.assertNotIn("W25Q128JVSIQ", syms)
        # results summary popup opened and lists the new symbols
        win = self.window("Import Complete")
        txt = self.window_text(win)
        for mpn in IN_ZIPS:
            self.assertIn(mpn, txt)
        self.assertIn("New Symbols (4)", txt)
        self.assertNotEqual(before, text)

    def test_each_layout_individually(self):
        """Point Source at a single zip file (a single-zip source is allowed)."""
        for mpn, layout in LAYOUT.items():
            with self.subTest(layout=layout):
                fp, model, zname = IN_ZIPS[mpn]
                self.h.app.source.set(str(self.lib.zips / zname))
                self.run_button()
                self.close_windows()
                syms = symbols(self.assert_valid_lib())
                self.assertIn(mpn, syms)
                self.assertEqual(prop(syms[mpn], "Footprint"), f"personal:{fp}")
                self.assertEqual(prop(syms[mpn], "MPN"), mpn)
                self.assertEqual(models(self.lib.pretty / f"{fp}.kicad_mod"), [f"../3d/step/{model}"])
                self.assertEqual(self.results_rows()[0][1], "✅ OK")

    def test_autoset_off_keeps_vendor_footprint_property(self):
        self.set_check("Auto-set footprint", False, self.h.app.autoset)
        self.run_import()
        syms = symbols(self.assert_valid_lib())
        self.assertEqual(prop(syms["STM32G431CBU6"], "Footprint"), "QFN50P700X700X60-49N")
        # footprint + model still imported
        self.assertTrue((self.lib.pretty / "QFN50P700X700X60-49N.kicad_mod").exists())

    def test_existing_library_symbols_preserved(self):
        before = symbols(self.sym_text())
        self.run_import()
        after = symbols(self.assert_valid_lib())
        for name in EXISTING:
            with self.subTest(name=name):
                self.assertIn(name, after)
                self.assertEqual(props(after[name]), props(before[name]))
                self.assertEqual(pin_count(after[name]), pin_count(before[name]))
        self.assertEqual(prop(after["NE555DR"], "Manufacturer_Part_Number"), "NE555DR")
        self.assertEqual(len(after), len(before) + 4)
        # existing footprints untouched
        self.assertEqual(models(self.lib.pretty / "SOIC127P600X175-8N.kicad_mod"),
                         ["../3d/step/SOIC127P600X175-8N.step"])

    # BUG: sanitize_block() forces every sub-unit to unit>=1 (max(1, u)), so the
    # common-to-all-units body "<name>_0_1" of EVERY symbol (existing ones too) is
    # rewritten to "<name>_1_1", duplicating the pins unit name.
    @unittest.expectedFailure
    def test_existing_symbol_sub_units_not_renamed(self):
        before = symbols(self.sym_text())
        self.assertIn("LM358DR_0_1", unit_names(before["LM358DR"]))
        self.run_import()
        after = symbols(self.sym_text())
        for name in EXISTING + tuple(IN_ZIPS):
            with self.subTest(name=name):
                units = unit_names(after[name])
                self.assertEqual(len(units), len(set(units)), f"duplicate unit names {units}")
        self.assertEqual(unit_names(after["LM358DR"]), unit_names(before["LM358DR"]))

    # BUG: write_symbols_file() renames the library to .bak once PER PACKAGE, so after
    # a multi-zip import the .bak holds the library minus the last zip, not the
    # pre-import library.
    @unittest.expectedFailure
    def test_backup_holds_pre_import_library(self):
        original = self.sym_text()
        self.run_import()
        bak = self.lib.sym_file.with_suffix(".kicad_sym.bak")
        self.assertTrue(bak.exists())
        self.assertEqual(top_names(bak.read_text()), top_names(original))

    # BUG: import-time 3D pairing writes into footprints that were SKIPPED because
    # they already exist, adding a second (model ...) to the user's own footprint.
    @unittest.expectedFailure
    def test_skipped_existing_footprint_is_not_modified(self):
        src = self.lib.root / "src_lm"
        src.mkdir()
        pl.vendor_zip(src / "LM358DR.zip", "LM358DR", "U", "SOIC127P600X175-8N", 6, "snapeda")
        fp_file = self.lib.pretty / "SOIC127P600X175-8N.kicad_mod"
        original = fp_file.read_text()
        self.h.app.source.set(str(src))
        self.run_import()
        self.assertEqual(self.results_rows()[0][3], "0+1")   # footprint skipped (exists)
        self.assertEqual(models(fp_file), ["../3d/step/SOIC127P600X175-8N.step"])
        self.assertEqual(fp_file.read_text(), original)

    def test_bad_zip_reports_error_and_is_not_logged(self):
        (self.lib.zips / "broken.zip").write_bytes(b"this is not a zip")
        self.run_import()
        rows = {r[0]: r for r in self.results_rows()}
        self.assertEqual(rows["broken.zip"][1], "❌ ERR")
        self.assertTrue(rows["broken.zip"][5])
        self.assertEqual(self.counters()["errors"], "1")
        self.assertEqual(self.counters()["sym_add"], "4")        # others still imported
        paths = [e["path"] for e in self.log_entries().values()]
        self.assertFalse(any(p.endswith("broken.zip") for p in paths))
        self.assertEqual(len(paths), 4)
        self.assertIn("error", self.window_text(self.window("Import Complete")).lower())
        self.assert_valid_lib()

    def test_missing_source_shows_error_and_writes_nothing(self):
        before = self.snapshot(self.lib.personal)
        self.h.app.source.set(str(self.lib.root / "nope"))
        self.run_button()
        self.assertEqual(self.h.errors()[-1][1], "Error")
        self.assertIn("Source not found", self.h.errors()[-1][2])
        self.assertEqual(self.snapshot(self.lib.personal), before)


# ═══════════════════════════════════════════════════════════════════════════
# IMPORT OPTIONS
# ═══════════════════════════════════════════════════════════════════════════
class ImportOptionTests(ImportTestBase):

    def _lm358_variant_source(self, npins=6):
        """A vendor zip whose LM358DR has different content than the library's."""
        src = self.lib.root / "src_lm"
        src.mkdir(exist_ok=True)
        pl.vendor_zip(src / "LM358DR.zip", "LM358DR", "U", "SOIC127P600X175-8N", npins, "snapeda")
        self.h.app.source.set(str(src))

    def test_if_symbol_exists_skip(self):
        self._lm358_variant_source()
        self.h.app.dup_handling.set("skip")
        self.run_import()
        syms = symbols(self.assert_valid_lib())
        self.assertEqual(pin_count(syms["LM358DR"]), 8)       # original kept
        self.assertNotIn("LM358DR_imp2", syms)
        self.assertEqual(len(syms), 4)
        self.assertEqual(self.counters()["sym_add"], "0")

    def test_if_symbol_exists_rename(self):
        self._lm358_variant_source()
        self.h.app.dup_handling.set("rename")
        self.run_import()
        syms = symbols(self.assert_valid_lib())
        self.assertEqual(pin_count(syms["LM358DR"]), 8)
        self.assertIn("LM358DR_imp2", syms)                   # _imp1 already taken
        self.assertEqual(pin_count(syms["LM358DR_imp2"]), 6)
        self.assertTrue(all(u.startswith("LM358DR_imp2_") for u in unit_names(syms["LM358DR_imp2"])))
        self.assertEqual(pin_count(syms["LM358DR_imp1"]), 8)
        self.assertEqual(self.counters()["sym_ren"], "1")
        self.assertIn("LM358DR_imp2", self.window_text(self.window("Import Complete")))

    def test_if_symbol_exists_replace(self):
        self._lm358_variant_source()
        self.h.app.dup_handling.set("replace")
        self.run_import()
        syms = symbols(self.assert_valid_lib())
        self.assertEqual(pin_count(syms["LM358DR"]), 6)
        self.assertEqual(pin_count(syms["LM358DR_imp1"]), 8)  # untouched
        self.assertEqual(len(syms), 4)
        self.assertEqual(self.counters()["sym_ovr"], "1")

    def test_overwrite_checkbox_replaces_symbol_and_footprint(self):
        self._lm358_variant_source()
        self.h.app.dup_handling.set("skip")
        self.set_check("Overwrite existing", True, self.h.app.overwrite)
        self.run_import()
        syms = symbols(self.assert_valid_lib())
        self.assertEqual(pin_count(syms["LM358DR"]), 6)       # overwrite wins over "skip"
        fp = (self.lib.pretty / "SOIC127P600X175-8N.kicad_mod").read_text()
        self.assertEqual(len(re.findall(r"\(pad ", fp)), 6)   # vendor footprint copied over

    def test_overwrite_bypasses_skip_list(self):
        self.run_import()
        self.close_windows()
        self.set_check("Overwrite existing", True, self.h.app.overwrite)
        self.run_import()
        self.assertEqual(len(self.results_rows()), 4)         # re-processed, not skipped
        self.assertEqual(self.counters()["fp_added"], "4")
        syms = symbols(self.assert_valid_lib())
        self.assertEqual(len(syms), 8)

    def test_dry_run_leaves_symbol_file_and_log_untouched(self):
        before = self.sym_text()
        self.set_check("Dry run", True, self.h.app.dry_run)
        self.run_import()
        self.assertEqual(self.sym_text(), before)
        self.assertFalse((self.lib.personal / kp.ImportLog.FILENAME).exists())
        self.assertEqual(self.counters()["sym_add"], "4")     # preview still counts
        # a later real run imports everything
        self.close_windows()
        self.set_check("Dry run", False, self.h.app.dry_run)
        self.run_import()
        self.assertEqual(self.counters()["sym_add"], "4")

    # BUG: dry_run is only honoured for the symbol file and the log; process_one_package
    # still calls copy_3d_models() and import_footprints() and writes the files.
    @unittest.expectedFailure
    def test_dry_run_copies_no_footprints_or_models(self):
        pretty, mdl = self.snapshot(self.lib.pretty), self.snapshot(self.lib.models)
        self.set_check("Dry run", True, self.h.app.dry_run)
        self.run_import()
        self.assertEqual(self.snapshot(self.lib.pretty), pretty)
        self.assertEqual(self.snapshot(self.lib.models), mdl)

    def test_recursive_on_finds_nested_zips(self):
        sub = self.lib.zips / "vendor" / "deep"
        sub.mkdir(parents=True)
        shutil.move(str(self.lib.zips / "ul_AP2112K-3.3TRG1.zip"), str(sub))
        self.assertTrue(self.h.app.recurse.get())
        self.run_import()
        self.assertIn("AP2112K-3.3TRG1", symbols(self.assert_valid_lib()))
        self.assertEqual(self.counters()["packages"], "4")

    # BUG: gather_packages() only looks at zips inside the rglob() loop, so with
    # "Recursive directory search" off NOT EVEN the zips directly in the source
    # folder are found -- the import silently processes 0 packages.
    @unittest.expectedFailure
    def test_recursive_off_imports_top_level_only(self):
        sub = self.lib.zips / "vendor"
        sub.mkdir()
        shutil.move(str(self.lib.zips / "ul_AP2112K-3.3TRG1.zip"), str(sub))
        self.set_check("Recursive", False, self.h.app.recurse)
        self.run_import()
        syms = symbols(self.sym_text())
        self.assertNotIn("AP2112K-3.3TRG1", syms)
        for mpn in ("STM32G431CBU6", "TPS62133RGTR", "USB4105-GF-A"):
            self.assertIn(mpn, syms)

    def test_relative_model_paths_off_gives_non_relative_path(self):
        self.set_check("Use relative model paths", False, self.h.app.rel_models)
        self.run_import()
        fp_file = self.lib.pretty / "QFN50P700X700X60-49N.kicad_mod"
        (m,) = models(fp_file)
        self.assertFalse(m.startswith(".."), m)
        # Off does NOT give an absolute path: it uses ${PERSONAL_LIB_DIR}, which
        # only resolves if that KiCad path variable is configured.
        self.assertEqual(m, "${PERSONAL_LIB_DIR}/3d/step/QFN50P700X700X60-49N.step")
        resolved = kp.expand_personal_var(m, self.lib.personal)
        self.assertTrue(resolved.is_absolute() and resolved.exists())

    # BUG: the Import tab's "3D Models Root" field is ignored by the import;
    # models are always copied to <personal>/3d/step (process_one_package).
    @unittest.expectedFailure
    def test_models_root_setting_used_by_import(self):
        elsewhere = self.lib.root / "shared_3d"
        elsewhere.mkdir()
        self.h.app.models_root.set(str(elsewhere))
        self.run_import()
        found = [p.name for p in elsewhere.rglob("*") if p.is_file()]
        self.assertIn("QFN50P700X700X60-49N.step", found)

    def _reimport_with_raw_vendor_footprint(self, ignore_fp: bool):
        self.run_import()
        self.close_windows()
        self.set_check("Skip already-imported", False, self.h.app.skip_imported)
        self.set_check("Auto-set footprint", False, self.h.app.autoset)
        self.set_check("Ignore footprint", ignore_fp, self.h.app.ignore_fp_dedupe)
        self.h.app.dup_handling.set("rename")
        self.run_import()
        return symbols(self.assert_valid_lib())

    def test_dedupe_considers_footprint_when_ignore_off(self):
        syms = self._reimport_with_raw_vendor_footprint(ignore_fp=False)
        # same symbol, different Footprint property -> treated as different -> renamed
        self.assertIn("STM32G431CBU6_imp1", syms)
        self.assertEqual(prop(syms["STM32G431CBU6_imp1"], "Footprint"), "QFN50P700X700X60-49N")

    # BUG: "Ignore footprint in deduplication" has no effect in the default
    # "Ignore Properties" mode: strip_prop_ids() first mangles the property's
    # (effects ...) so strip_footprint_prop()'s regex never matches.
    @unittest.expectedFailure
    def test_dedupe_ignores_footprint_when_ignore_on(self):
        syms = self._reimport_with_raw_vendor_footprint(ignore_fp=True)
        self.assertNotIn("STM32G431CBU6_imp1", syms)
        self.assertEqual(self.counters()["sym_ren"], "0")


# ═══════════════════════════════════════════════════════════════════════════
# SKIP-LIST / RE-IMPORT / IMPORT LOG DIALOG
# ═══════════════════════════════════════════════════════════════════════════
class ImportLogTests(ImportTestBase):

    def test_first_import_writes_log(self):
        self.run_import()
        entries = self.log_entries()
        self.assertEqual(len(entries), 4)
        names = sorted(Path(e["path"]).name for e in entries.values())
        self.assertEqual(names, sorted(z for _, _, z in IN_ZIPS.values()))
        for e in entries.values():
            self.assertEqual(e["summary"]["symbols_added"], 1)

    def test_reimport_skips_logged_zips(self):
        self.run_import()
        self.close_windows()
        text = self.sym_text()
        self.run_button()
        self.assertEqual(self.sym_text(), text)               # nothing rewritten
        self.assertEqual(self.results_rows(), [])
        c = self.counters()
        self.assertEqual((c["packages"], c["new"], c["sym_add"]), ("4", "0", "0"))
        txt = self.window_text(self.window("Import Complete"))
        self.assertIn("Skipped via Import Log (4)", txt)
        self.assertIn("Nothing new was added", txt)

    def test_reimport_without_skip_list_adds_no_duplicates(self):
        self.run_import()
        self.close_windows()
        self.set_check("Skip already-imported", False, self.h.app.skip_imported)
        self.run_import()
        self.assertEqual(len(self.results_rows()), 4)
        syms = symbols(self.assert_valid_lib())
        self.assertEqual(len(syms), 8)
        self.assertFalse([n for n in syms if "_imp" in n and not n.startswith("LM358DR")])
        c = self.counters()
        self.assertEqual((c["sym_add"], c["fp_added"], c["fp_skipped"], c["models"]), ("0", "0", "4", "0"))
        # footprints still carry exactly one model each
        for mpn, (fp, model, _z) in IN_ZIPS.items():
            self.assertEqual(models(self.lib.pretty / f"{fp}.kicad_mod"), [f"../3d/step/{model}"])

    def test_content_duplicate_under_new_zip_name_not_added(self):
        self.run_import()
        self.close_windows()
        shutil.copy(self.lib.zips / "ul_STM32G431CBU6.zip", self.lib.zips / "STM32 copy.zip")
        self.run_import()
        self.assertEqual([r[0] for r in self.results_rows()], ["STM32 copy.zip"])
        syms = symbols(self.assert_valid_lib())
        self.assertEqual(len(syms), 8)
        self.assertEqual(self.h.app.results[0]["symbols_skipped_content_dupe"], 1)

    def test_changed_zip_is_reimported(self):
        self.run_import()
        self.close_windows()
        z = self.lib.zips / "USB4105-GF-A.zip"
        st = z.stat()
        os.utime(z, (st.st_atime, st.st_mtime + 100))
        self.run_import()
        self.assertEqual([r[0] for r in self.results_rows()], ["USB4105-GF-A.zip"])

    def _open_log_dialog(self):
        self.h.click(self.tab("import"), "View / reset import log")
        win = self.window("Import Log")
        return win, self.h.trees(win)[0]

    def test_log_dialog_lists_entries_and_filters(self):
        self.run_import()
        self.close_windows()
        win, tree = self._open_log_dialog()
        rows = self.h.rows(tree)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r[1] for r in rows}, {z for _, _, z in IN_ZIPS.values()})
        self.assertTrue(all(r[3] == "1sym + 1fp + 13d" for r in rows))
        entry = [w for w in self.h.walk(win) if isinstance(w, ttk.Entry)][0]
        entry.insert(0, "stm32")
        self.h.pump()
        self.assertEqual([r[1] for r in self.h.rows(tree)], ["ul_STM32G431CBU6.zip"])

    def test_log_dialog_from_tools_menu(self):
        self.run_import()
        self.close_windows()
        self.menu_invoke("Tools", "View / Reset Import Log")
        self.assertEqual(len(self.h.rows(self.h.trees(self.window("Import Log"))[0])), 4)

    def test_log_dialog_remove_selected(self):
        self.run_import()
        self.close_windows()
        win, tree = self._open_log_dialog()
        # nothing selected -> info message, nothing removed
        self.h.click(win, "Remove Selected")
        self.assertEqual(self.h.last_dialog()[1], "No selection")
        # selected + answer No -> nothing removed
        target = next(i for i in tree.get_children() if tree.item(i, "values")[1] == "USB4105-GF-A.zip")
        tree.selection_set(target)
        self.h.answer(False)
        self.h.click(win, "Remove Selected")
        self.assertEqual(len(self.log_entries()), 4)
        # answer Yes -> removed from disk + table + header
        tree.selection_set(target)
        self.h.answer(True)
        self.h.click(win, "Remove Selected")
        self.assertEqual(len(self.log_entries()), 3)
        self.assertEqual(len(self.h.rows(tree)), 3)
        self.assertIn("Import log: 3 package(s)", self.window_text(win))
        win.destroy()
        # next run re-processes only that zip
        self.run_import()
        self.assertEqual([r[0] for r in self.results_rows()], ["USB4105-GF-A.zip"])

    def test_log_dialog_reset_entire_log(self):
        self.run_import()
        self.close_windows()
        win, tree = self._open_log_dialog()
        self.h.answer(False)
        self.h.click(win, "Reset Entire Log")
        self.assertEqual(len(self.log_entries()), 4)
        self.h.answer(True)
        self.h.click(win, "Reset Entire Log")
        self.assertEqual(self.log_entries(), {})
        self.assertEqual(self.h.rows(tree), [])
        win.destroy()
        self.run_import()
        self.assertEqual(len(self.results_rows()), 4)
        self.assertEqual(len(symbols(self.assert_valid_lib())), 8)   # still no duplicates

    # BUG: ImportLog.forget() recomputes the fingerprint from the file's CURRENT
    # size/mtime, so an entry whose zip was moved (e.g. Inbox "Back"), deleted or
    # changed can never be removed via "Remove Selected" -- it silently stays.
    @unittest.expectedFailure
    def test_log_dialog_remove_entry_for_moved_zip(self):
        self.run_import()
        self.close_windows()
        shutil.move(str(self.lib.zips / "USB4105-GF-A.zip"), str(self.lib.downloads))
        win, tree = self._open_log_dialog()
        target = next(i for i in tree.get_children() if tree.item(i, "values")[1] == "USB4105-GF-A.zip")
        tree.selection_set(target)
        self.h.answer(True)
        self.h.click(win, "Remove Selected")
        self.assertEqual(len(self.log_entries()), 3)

    def test_log_dialog_without_library_shows_error(self):
        self.h.app.personal.set("")
        self.h.click(self.tab("import"), "View / reset import log")
        self.assertEqual(self.h.errors()[-1][1], "No library selected")


# ═══════════════════════════════════════════════════════════════════════════
# RESULTS TAB, EXPORT REPORT, CLEAR RESULTS
# ═══════════════════════════════════════════════════════════════════════════
class ResultsTabTests(ImportTestBase):

    def test_counters_and_table_after_import(self):
        self.run_button()
        self.assertIn("results", self.h.app.notebook.tab(self.h.app.notebook.select(), "text").lower())
        self.assertEqual(self.counters(), {
            "packages": "4", "new": "4", "fp_added": "4", "fp_skipped": "0", "models": "4",
            "sym_add": "4", "sym_ren": "0", "sym_ovr": "0", "errors": "0"})
        rows = self.results_rows()
        self.assertEqual(sorted(r[0] for r in rows), sorted(z for _, _, z in IN_ZIPS.values()))
        for r in rows:
            self.assertEqual(r[1:], ("✅ OK", "1+0", "1+0", "1+1", ""))
        log = self.h.app.log.get("1.0", "end")
        self.assertIn("IMPORT COMPLETE", log)

    def test_view_details_and_copy_package_name(self):
        self.run_import()
        self.close_windows()
        tree = self.h.app.tree
        item = next(i for i in tree.get_children() if tree.item(i, "values")[0] == "USB4105-GF-A.zip")
        tree.selection_set(item)
        menu = self.h.app.tree_menu
        self.assertEqual(menu.entrycget(0, "label"), "View Details")
        menu.invoke(0)
        kind, title, msg = self.h.last_dialog()
        self.assertEqual((kind, title), ("showinfo", "Result Details"))
        self.assertIn("Package: USB4105-GF-A.zip", msg)
        self.assertIn("Symbols: 1+0", msg)
        menu.invoke(1)
        self.assertEqual(self.h.app.clipboard_get(), "USB4105-GF-A.zip")

    def test_context_menu_actions_without_selection_do_nothing(self):
        self.run_import()
        self.close_windows()
        self.h.app.tree.selection_set(())
        n = len(self.h.dialogs)
        self.h.app.tree_menu.invoke(0)
        self.h.app.tree_menu.invoke(1)
        self.assertEqual(len(self.h.dialogs), n)

    def test_sort_results_by_column(self):
        self.run_import()
        self.close_windows()
        self.h.app.sort_tree("package")
        names = [r[0] for r in self.results_rows()]
        self.assertEqual(names, sorted(names))

    def _check_csv(self, path: Path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["Package", "Kind", "FP+", "FP-", "3D", "SYM+", "REN", "OVR", "Errors"])
        self.assertEqual(len(rows), 5)
        by = {r[0]: r for r in rows[1:]}
        self.assertEqual(by["USB4105-GF-A.zip"], ["USB4105-GF-A.zip", "zip", "1", "0", "1", "1", "0", "0", ""])

    def test_export_report_button(self):
        self.run_import()
        self.close_windows()
        out = self.lib.root / "report.csv"
        self.h.files.append(str(out))
        self.h.click(self.tab("import"), "Export Report")
        self.assertTrue(out.exists())
        self._check_csv(out)
        self.assertEqual(self.h.last_dialog()[1], "Exported")

    def test_export_report_file_menu(self):
        self.run_import()
        self.close_windows()
        out = self.lib.root / "menu_report.csv"
        self.h.files.append(str(out))
        self.menu_invoke("File", "Export Report")
        self._check_csv(out)

    def test_export_report_cancelled_writes_nothing(self):
        self.run_import()
        self.close_windows()
        n = len(self.h.dialogs)
        self.h.click(self.tab("import"), "Export Report")     # dialog returns "" = cancel
        self.assertEqual([d[0] for d in self.h.dialogs[n:]], ["asksaveasfilename"])

    def test_export_report_without_results_warns(self):
        self.h.click(self.tab("import"), "Export Report")
        self.assertEqual(self.h.last_dialog()[:2], ("showwarning", "No Data"))

    def test_export_report_includes_error_text(self):
        (self.lib.zips / "broken.zip").write_bytes(b"junk")
        self.run_import()
        self.close_windows()
        out = self.lib.root / "r.csv"
        self.h.files.append(str(out))
        self.h.click(self.tab("import"), "Export Report")
        with open(out, newline="", encoding="utf-8") as f:
            rows = {r[0]: r for r in csv.reader(f)}
        self.assertTrue(rows["broken.zip"][8])

    def test_clear_results(self):
        self.run_import()
        self.close_windows()
        text = self.sym_text()
        self.h.click(self.tab("import"), "Clear Results")
        self.assertEqual(self.results_rows(), [])
        self.assertTrue(all(v == "0" for v in self.counters().values()))
        self.assertEqual(self.h.app.log.get("1.0", "end").strip(), "")
        self.assertEqual(self.h.app.results, [])
        self.assertEqual(self.sym_text(), text)               # library untouched
        self.assertEqual(len(self.log_entries()), 4)          # import log untouched
        self.h.click(self.tab("import"), "Export Report")
        self.assertEqual(self.h.last_dialog()[1], "No Data")


# ═══════════════════════════════════════════════════════════════════════════
# LIBRARY TAB BROWSER
# ═══════════════════════════════════════════════════════════════════════════
class LibraryBrowserTests(ImportTestBase):

    def _rows(self):
        return {r[0]: r for r in self.h.rows(self.h.app.lib_tree)}

    def test_columns_for_existing_library(self):
        self.h.select_tab("library")
        rows = self._rows()
        self.assertEqual(set(rows), set(EXISTING))
        self.assertEqual(rows["LM358DR"], ("LM358DR", "SOIC127P600X175-8N", "LM358DR", "SOIC127P600X175-8N.step", "8"))
        self.assertEqual(rows["NE555DR"][2], "NE555DR")       # non-standard MPN field found
        self.assertEqual(rows["R_Generic"][1], "(none)")
        self.assertEqual(rows["R_Generic"][3], "(none)")
        self.assertEqual(rows["R_Generic"][4], "2")
        self.assertEqual(self.h.app.lib_count_label.cget("text"), "Total: 4")
        self.assertIn("Loaded 4 symbols", self.h.app.lib_info_var.get())

    def test_imported_parts_appear_with_model(self):
        self.run_import()
        self.close_windows()
        self.h.select_tab("library")
        rows = self._rows()
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows["STM32G431CBU6"],
                         ("STM32G431CBU6", "QFN50P700X700X60-49N", "STM32G431CBU6", "QFN50P700X700X60-49N.step", "12"))
        self.assertEqual(rows["TPS62133RGTR"][3], "TPS62133RGTR.stp")

    def test_search_filter(self):
        self.h.select_tab("library")
        app = self.h.app
        app.lib_search_var.set("lm358")
        self.h.pump()
        self.assertEqual(set(self._rows()), {"LM358DR", "LM358DR_imp1"})
        self.assertEqual(app.lib_count_label.cget("text"), "Showing: 2/4")
        app.lib_search_var.set("soic127")                      # matches footprint column
        self.assertEqual(set(self._rows()), {"LM358DR", "LM358DR_imp1", "NE555DR"})
        app.lib_search_var.set("zzz")
        self.assertEqual(self._rows(), {})
        app.lib_search_var.set("")
        self.assertEqual(len(self._rows()), 4)
        self.assertEqual(app.lib_count_label.cget("text"), "Total: 4")

    def test_refresh_button_picks_up_changes(self):
        self.h.select_tab("library")
        text = self.sym_text()
        self.lib.sym_file.write_text(text.rstrip()[:-1] + pl.symbol("NEWPART", "U", "", 3) + ")\n")
        self.assertEqual(len(self._rows()), 4)
        self.h.click(self.tab("library"), "Refresh")
        self.assertIn("NEWPART", self._rows())
        self.assertEqual(self.h.app.lib_count_label.cget("text"), "Total: 5")

    def test_refresh_keeps_active_filter(self):
        self.h.select_tab("library")
        self.h.app.lib_search_var.set("NE555")
        self.h.click(self.tab("library"), "Refresh")
        self.assertEqual(set(self._rows()), {"NE555DR"})

    def test_missing_symbol_file_message(self):
        self.h.app.sym_choice.set("nope.kicad_sym")
        self.h.click(self.tab("library"), "Refresh")
        self.assertIn("Symbol file not found", self.h.app.lib_info_var.get())

    # BUG: sort_lib_tree() sorts every column as text, so Pins sorts 10,12,2,5,8.
    @unittest.expectedFailure
    def test_sort_by_pins_is_numeric(self):
        self.run_import()
        self.close_windows()
        self.h.select_tab("library")
        self.h.app.sort_lib_tree("pins")
        pins = [int(r[4]) for r in self.h.rows(self.h.app.lib_tree)]
        self.assertEqual(pins, sorted(pins))


# ═══════════════════════════════════════════════════════════════════════════
# INBOX TAB
# ═══════════════════════════════════════════════════════════════════════════
class InboxTests(ImportTestBase):

    def setUp(self):
        super().setUp()
        self.inbox = self.tab("inbox")
        self.h.select_tab("inbox")
        self.h.click(self.inbox, "↻")

    def dl_names(self):
        return {r[0] for r in self.h.rows(self.h.app.inbox_dl_tree)}

    def zip_rows(self):
        return {r[0]: r for r in self.h.rows(self.h.app.inbox_zip_tree)}

    def sym_rows(self):
        return self.h.rows(self.h.app.inbox_sym_tree)

    def select_sym(self, name):
        tv = self.h.app.inbox_sym_tree
        iid = next(i for i in tv.get_children() if tv.item(i, "values")[0] == name)
        tv.selection_set(iid)
        return iid

    def test_lists_downloads_and_zips_filter(self):
        self.assertEqual(self.dl_names(), {"BSS138.zip", "LIB_W25Q128JVSIQ.zip"})
        self.assertEqual(set(self.zip_rows()), {z for _, _, z in IN_ZIPS.values()})
        cb = self.checkbutton(self.inbox, "Zips only")
        cb.invoke()
        self.h.pump()
        self.assertFalse(self.h.app.inbox_zips_only.get())
        self.assertEqual(self.dl_names(), {"BSS138.zip", "LIB_W25Q128JVSIQ.zip", "datasheet.pdf"})
        prefs = json.loads(kp.PREFS_FILE.read_text())
        self.assertFalse(prefs["inbox_zips_only"])            # remembered
        cb.invoke()
        self.h.pump()
        self.assertEqual(self.dl_names(), {"BSS138.zip", "LIB_W25Q128JVSIQ.zip"})

    def test_status_column_new_then_imported(self):
        self.assertTrue(all(r[3] == "● New" for r in self.zip_rows().values()))
        self.h.click(self.inbox, "Import Zips")
        self.assertTrue(all(r[3] == "✓ Imported" for r in self.zip_rows().values()))

    def test_move_to_zips_and_back(self):
        self.h.app.inbox_dl_tree.selection_set("BSS138.zip")
        self.h.click(self.inbox, "Move →")
        self.assertFalse((self.lib.downloads / "BSS138.zip").exists())
        self.assertTrue((self.lib.zips / "BSS138.zip").exists())
        self.assertNotIn("BSS138.zip", self.dl_names())
        self.assertEqual(self.zip_rows()["BSS138.zip"][3], "● New")
        self.assertEqual(self.h.app.inbox_status.get(), "Moved 1 file(s): BSS138.zip")
        self.h.app.inbox_zip_tree.selection_set("BSS138.zip")
        self.h.click(self.inbox, "Back")
        self.assertTrue((self.lib.downloads / "BSS138.zip").exists())
        self.assertFalse((self.lib.zips / "BSS138.zip").exists())
        self.assertIn("BSS138.zip", self.dl_names())

    def test_move_multiple_then_import(self):
        self.h.app.inbox_dl_tree.selection_set(("BSS138.zip", "LIB_W25Q128JVSIQ.zip"))
        self.h.click(self.inbox, "Move →")
        self.assertEqual(self.dl_names(), set())
        self.h.click(self.inbox, "Import Zips")
        syms = symbols(self.assert_valid_lib())
        self.assertIn("BSS138", syms)
        self.assertIn("W25Q128JVSIQ", syms)
        self.assertEqual(len(syms), 10)

    def test_move_with_nothing_selected(self):
        self.h.app.inbox_dl_tree.selection_set(())
        self.h.click(self.inbox, "Move →")
        self.assertIn("Select one or more", self.h.app.inbox_status.get())
        self.assertEqual(len(self.dl_names()), 2)

    def test_move_collision_asks_and_can_skip(self):
        shutil.copy(self.lib.downloads / "BSS138.zip", self.lib.zips / "BSS138.zip")
        self.h.app.inbox_dl_tree.selection_set("BSS138.zip")
        self.h.answer(False)
        self.h.click(self.inbox, "Move →")
        self.assertEqual(self.h.last_dialog()[1], "File exists")
        self.assertTrue((self.lib.downloads / "BSS138.zip").exists())
        self.assertIn("skipped 1", self.h.app.inbox_status.get())
        self.h.app.inbox_dl_tree.selection_set("BSS138.zip")
        self.h.answer(True)
        self.h.click(self.inbox, "Move →")
        self.assertFalse((self.lib.downloads / "BSS138.zip").exists())
        self.assertTrue((self.lib.zips / "BSS138.zip").exists())

    @unittest.skipUnless(shutil.which("gio"), "gio not installed")
    def test_delete_sends_to_trash_with_gio(self):
        trash_home = self.lib.root / "xdg"
        self.h.app.inbox_dl_tree.selection_set("BSS138.zip")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(trash_home)}):
            self.h.click(self.inbox, "Delete")
        self.assertEqual(self.h.dialogs[-1][1], "Delete")
        self.assertFalse((self.lib.downloads / "BSS138.zip").exists())
        self.assertEqual(self.h.app.inbox_status.get(), "Deleted 1 file(s)")
        self.assertNotIn("BSS138.zip", self.dl_names())
        # really in the trash, not gone for good
        self.assertTrue(list(trash_home.rglob("BSS138.zip")))

    def test_delete_confirm_no_keeps_file(self):
        self.h.app.inbox_dl_tree.selection_set("BSS138.zip")
        self.h.answer(False)
        with mock.patch.object(kp, "_send_to_trash", side_effect=AssertionError("should not trash")):
            self.h.click(self.inbox, "Delete")
        self.assertTrue((self.lib.downloads / "BSS138.zip").exists())

    def test_delete_trash_unavailable_offers_permanent_delete(self):
        with mock.patch.object(kp, "_send_to_trash", return_value=False):
            # declined -> file kept, reported as failed
            self.h.app.inbox_dl_tree.selection_set("BSS138.zip")
            self.h.answer(True, False)
            self.h.click(self.inbox, "Delete")
            self.assertEqual(self.h.dialogs[-1][1], "Recycle Bin unavailable")
            self.assertTrue((self.lib.downloads / "BSS138.zip").exists())
            self.assertEqual(self.h.app.inbox_status.get(), "Deleted 0 file(s), 1 failed")
            # accepted -> permanently deleted
            self.h.app.inbox_dl_tree.selection_set("BSS138.zip")
            self.h.answer(True, True)
            self.h.click(self.inbox, "Delete")
            self.assertFalse((self.lib.downloads / "BSS138.zip").exists())
            self.assertEqual(self.h.app.inbox_status.get(), "Deleted 1 file(s)")

    def test_import_zips_populates_symbol_table_without_popup(self):
        self.h.click(self.inbox, "Import Zips")
        self.assertEqual(self.h.windows(), [])                # no modal summary
        self.assertIn("inbox", self.h.app.notebook.tab(self.h.app.notebook.select(), "text").lower())
        rows = {r[0]: r for r in self.sym_rows()}
        self.assertEqual(set(rows), set(IN_ZIPS))
        for mpn, (fp, _m, zname) in IN_ZIPS.items():
            self.assertEqual(rows[mpn], (mpn, f"personal:{fp}", "New", zname))
        self.assertEqual(self.h.app.inbox_status.get(), "Processed 4 zip(s): 4 new symbol(s)")
        self.assertEqual(len(symbols(self.assert_valid_lib())), 8)
        # Results tab was filled too
        self.assertEqual(len(self.results_rows()), 4)

    def test_import_zips_again_reports_nothing_new(self):
        self.h.click(self.inbox, "Import Zips")
        self.h.click(self.inbox, "Import Zips")
        self.assertEqual(self.sym_rows(), [])
        self.assertTrue(self.h.app.inbox_status.get().startswith("Nothing new to import"))

    def test_import_zips_shows_existing_symbols(self):
        self.h.click(self.inbox, "Import Zips")
        self.set_check("Skip already-imported", False, self.h.app.skip_imported)
        self.h.click(self.inbox, "Import Zips")
        rows = {r[0]: r for r in self.sym_rows()}
        self.assertEqual(set(rows), set(IN_ZIPS))
        self.assertTrue(all(r[2] == "Already in library" for r in rows.values()))
        self.assertEqual(len(symbols(self.assert_valid_lib())), 8)

    def test_import_zips_with_missing_folder(self):
        self.h.app.source.set(str(self.lib.root / "missing"))
        self.h.click(self.inbox, "Import Zips")
        self.assertEqual(self.h.errors()[-1][1], "Import")

    def test_copy_buttons(self):
        self.h.click(self.inbox, "Import Zips")
        app = self.h.app
        self.select_sym("STM32G431CBU6")
        self.h.click(self.inbox, "Copy Symbol")
        self.assertEqual(app.clipboard_get(), "STM32G431CBU6")
        self.assertEqual(app.inbox_status.get(), "📋 Copied: STM32G431CBU6")
        with mock.patch.object(kp, "_all_kicad_config_dirs", return_value=[]):
            self.h.click(self.inbox, "Copy Lib:Symbol")
        self.assertEqual(app.clipboard_get(), "personal:STM32G431CBU6")
        self.h.click(self.inbox, "Copy Footprint")
        self.assertEqual(app.clipboard_get(), "personal:QFN50P700X700X60-49N")
        self.h.click(self.inbox, "Copy All Names")
        self.assertEqual(set(app.clipboard_get().splitlines()), set(IN_ZIPS))
        self.assertEqual(app.inbox_status.get(), "📋 Copied 4 names")

    def test_copy_lib_symbol_uses_sym_lib_table_nickname(self):
        (self.lib.personal / "sym-lib-table").write_text(
            '(sym_lib_table\n  (lib (name "MyParts")(type "KiCad")(uri "%s")(options "")(descr ""))\n)\n'
            % self.lib.sym_file.as_posix())
        self.h.click(self.inbox, "Import Zips")
        self.select_sym("TPS62133RGTR")
        with mock.patch.object(kp, "_all_kicad_config_dirs", return_value=[]):
            self.h.click(self.inbox, "Copy Lib:Symbol")
        self.assertEqual(self.h.app.clipboard_get(), "MyParts:TPS62133RGTR")

    def test_copy_multi_selection_and_ctrl_c(self):
        self.h.click(self.inbox, "Import Zips")
        tv = self.h.app.inbox_sym_tree
        tv.selection_set(tv.get_children()[:2])
        self.h.click(self.inbox, "Copy Symbol")
        self.assertEqual(len(self.h.app.clipboard_get().splitlines()), 2)
        self.select_sym("USB4105-GF-A")
        tv.focus_force()
        tv.event_generate("<Control-c>")
        self.h.pump()
        self.assertEqual(self.h.app.clipboard_get(), "USB4105-GF-A")

    def test_copy_with_nothing_selected(self):
        self.h.click(self.inbox, "Copy Symbol")
        self.assertEqual(self.h.app.inbox_status.get(), "Nothing selected to copy.")

    def test_full_summary(self):
        self.h.click(self.inbox, "Full Summary")
        self.assertEqual(self.h.last_dialog()[1], "Summary")
        self.assertEqual(self.h.windows(), [])
        self.h.click(self.inbox, "Import Zips")
        self.h.click(self.inbox, "Full Summary")
        txt = self.window_text(self.window("Import Complete"))
        self.assertIn("New Symbols (4)", txt)
        self.assertIn("Import log now contains 4 package(s)", txt)

    # BUG: the Inbox reports "DRY RUN — nothing written" but footprints and 3D
    # models were copied into the library (same root cause as the dry-run bug).
    @unittest.expectedFailure
    def test_inbox_dry_run_message_is_truthful(self):
        pretty = self.snapshot(self.lib.pretty)
        self.set_check("Dry run", True, self.h.app.dry_run)
        self.h.click(self.inbox, "Import Zips")
        self.assertIn("DRY RUN", self.h.app.inbox_status.get())
        self.assertEqual(self.snapshot(self.lib.pretty), pretty)


if __name__ == "__main__":
    unittest.main()
