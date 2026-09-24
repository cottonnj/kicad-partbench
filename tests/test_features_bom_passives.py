"""GUI feature tests for the BOM Manager and Passives tabs.

Every test drives the real App (tests/gui_harness.py) against a fresh practice
project (tests/practice_lib.py) and checks the result ON DISK with an
independent s-expression parser, not with the app's own regex helpers.

Known bugs are kept as tests asserting the CORRECT behaviour and marked
``@unittest.expectedFailure`` with a ``# BUG:`` comment.

Run:  DISPLAY=:99 python3.12 -m unittest tests.test_features_bom_passives -v
"""
from __future__ import annotations

import csv
import json
import re
import sys
import unittest
import uuid
import zipfile
from pathlib import Path
from tkinter import ttk
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_harness import GuiTestCase, kp  # noqa: E402
from practice_lib import BOM_ROWS, R603, schematic, sch_symbol  # noqa: E402

YAGEO_10K = "RC0603FR-0710KL"


# ─── Independent s-expression reader ─────────────────────────────────────

_TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')


class Str(str):
    """A quoted atom (so we can tell "symbol" from symbol)."""


def parse_sexp(text: str):
    """Parse a whole file; raise ValueError when it is not ONE balanced form."""
    stack: list[list] = [[]]
    pos = 0
    for m in _TOKEN.finditer(text):
        if text[pos:m.start()].strip():
            raise ValueError(f"garbage at {pos}: {text[pos:m.start()][:40]!r}")
        pos = m.end()
        tok = m.group(0)
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if len(stack) == 1:
                raise ValueError(f"unbalanced ')' at {m.start()}")
            node = stack.pop()
            stack[-1].append(node)
        elif tok.startswith('"'):
            stack[-1].append(Str(re.sub(r'\\(.)', r'\1', tok[1:-1])))
        else:
            stack[-1].append(tok)
    if text[pos:].strip():
        raise ValueError("trailing garbage")
    if len(stack) != 1:
        raise ValueError(f"{len(stack) - 1} unclosed '('")
    if len(stack[0]) != 1:
        raise ValueError(f"{len(stack[0])} top-level forms")
    return stack[0][0]


def placed_symbols(path: Path) -> list[dict]:
    """Every placed symbol in a schematic: {'ref', 'unit', 'props': [(k, v), ...]}."""
    root = parse_sexp(path.read_text(encoding="utf-8"))
    assert root[0] == "kicad_sch", root[0]
    out = []
    for node in root[1:]:
        if isinstance(node, list) and node and node[0] == "symbol" and any(
                isinstance(c, list) and c and c[0] == "lib_id" for c in node):
            props = [(c[1], c[2]) for c in node if isinstance(c, list) and c and c[0] == "property"]
            unit = next((c[1] for c in node if isinstance(c, list) and c and c[0] == "unit"), "1")
            out.append({"ref": dict(props).get("Reference"), "unit": unit, "props": props})
    return out


def props_of(path: Path, ref: str) -> dict:
    syms = [s for s in placed_symbols(path) if s["ref"] == ref]
    assert len(syms) == 1, f"{ref}: {len(syms)} symbols in {path.name}"
    names = [k for k, _ in syms[0]["props"]]
    dup = {n for n in names if names.count(n) > 1}
    assert not dup, f"{ref} has duplicate properties {dup}"
    return dict(syms[0]["props"])


def symbol_blocks(path: Path) -> dict:
    """Raw text of each top-level placed symbol keyed by reference (unit 1 only)."""
    text = path.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r'(?<=\n)\t\(symbol\n.*?\n\t\)(?=\n)', text, re.S):
        r = re.search(r'\(property "Reference" "([^"]*)"', m.group(0))
        if r:
            out.setdefault(r.group(1), m.group(0))
    return out


def prop_indent(path: Path, ref: str, name: str) -> str:
    blk = symbol_blocks(path)[ref]
    m = re.search(r'\n([ \t]*)\(property "' + re.escape(name) + '"', blk)
    return m.group(1) if m else None


def write_xlsx_rich(path: Path, rows: list[list[str]], rich: set[str]) -> None:
    """Like practice_lib.write_xlsx, but strings in ``rich`` are stored as
    rich-text runs (<si><r><t>..</t></r>...</si>), which Excel writes whenever
    a cell has mixed formatting."""
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(s):
        if s not in index:
            index[s] = len(strings)
            strings.append(s)
        return index[s]

    sheet_rows = ""
    for r, row in enumerate(rows, 1):
        cells = "".join(f'<c r="{chr(65 + c)}{r}" t="s"><v>{sid(v)}</v></c>'
                        for c, v in enumerate(row) if v != "")
        sheet_rows += f'<row r="{r}">{cells}</row>'

    def si(s):
        if s in rich:
            half = len(s) // 2
            return (f'<si><r><rPr><b/></rPr><t>{escape(s[:half])}</t></r>'
                    f'<r><t>{escape(s[half:])}</t></r></si>')
        return f"<si><t>{escape(s)}</t></si>"

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("xl/sharedStrings.xml",
                   f'<?xml version="1.0"?><sst {ns}>' + "".join(si(s) for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<?xml version="1.0"?><worksheet {ns}><sheetData>{sheet_rows}</sheetData></worksheet>')


def write_csv(path: Path, rows) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return path


# ─── Base ────────────────────────────────────────────────────────────────

class Base(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.cb_errors = []
        self.h.app.report_callback_exception = lambda *exc: self.cb_errors.append(exc)
        self.orig = {p: p.read_text(encoding="utf-8") for p in (self.lib.main_sch, self.lib.power_sch)}

    def tab(self):
        nb = self.h.app.notebook
        return nb.nametowidget(nb.select())

    def assert_valid(self, *paths):
        for p in paths or (self.lib.main_sch, self.lib.power_sch):
            try:
                root = parse_sexp(p.read_text(encoding="utf-8"))
            except ValueError as e:
                self.fail(f"{p.name} is no longer a valid s-expression: {e}")
            self.assertEqual(root[0], "kicad_sch")

    def assert_untouched(self, path: Path, *refs):
        before = symbol_blocks_from_text(self.orig[path])
        after = symbol_blocks(path)
        for r in refs:
            self.assertEqual(after[r], before[r], f"{r} in {path.name} was modified")

    def fill(self, win, values: dict):
        """Set form fields by their label text ('MPN', 'Value', ...)."""
        done = set()
        for w in self.h.walk(win):
            if not isinstance(w, ttk.Label):
                continue
            label = str(w.cget("text")).rstrip(":").replace(" *", "").strip()
            if label not in values or w.winfo_manager() != "grid":
                continue
            row = int(w.grid_info()["row"])
            peer = w.master.grid_slaves(row=row, column=1)
            self.assertTrue(peer, f"no input next to {label!r}")
            self.h.app.setvar(str(peer[0].cget("textvariable")), values[label])
            done.add(label)
        self.assertEqual(done, set(values), "some form fields were not found")
        self.h.pump()

    def form_values(self, win) -> dict:
        out = {}
        for w in self.h.walk(win):
            if isinstance(w, ttk.Label) and w.winfo_manager() == "grid" and str(w.cget("text")).endswith(":"):
                peer = w.master.grid_slaves(row=int(w.grid_info()["row"]), column=1)
                if peer and isinstance(peer[0], (ttk.Entry, ttk.Combobox)):
                    label = str(w.cget("text")).rstrip(":").replace(" *", "").strip()
                    out[label] = self.h.app.getvar(str(peer[0].cget("textvariable")))
        return out


def symbol_blocks_from_text(text: str) -> dict:
    out = {}
    for m in re.finditer(r'(?<=\n)\t\(symbol\n.*?\n\t\)(?=\n)', text, re.S):
        r = re.search(r'\(property "Reference" "([^"]*)"', m.group(0))
        if r:
            out.setdefault(r.group(1), m.group(0))
    return out


# ═════════════════════════════════════════════════════════════════════════
# BOM MANAGER
# ═════════════════════════════════════════════════════════════════════════

class BomManagerTests(Base):
    def load(self, bom=None, sch=None) -> dict:
        a = self.h.app
        a.bom_file_var.set(str(bom or self.lib.bom_csv))
        a.sch_file_var.set(str(sch or self.lib.main_sch))
        self.h.select_tab("BOM")
        self.h.click(self.tab(), "Load & Analyze")
        return {r[0]: r for r in self.h.rows(a.bom_tree)}

    def apply(self):
        self.h.click(self.tab(), "Apply to Schematics")

    # ── Load & Analyze ──
    def test_csv_load_analyze_matches_grouped_and_subsheet_refs(self):
        rows = self.load()
        self.assertEqual(set(rows), {"U1", "U2", "L1", "R1", "R2", "R3", "C1", "C2", "C10"})
        # grouped designators expanded
        for r in ("R1", "R2", "R3"):
            self.assertEqual(rows[r][4], YAGEO_10K)
        # sub-sheet parts found in power.kicad_sch
        for r in ("U2", "L1", "C10"):
            self.assertEqual(rows[r][6], "power.kicad_sch")
        self.assertEqual(rows["U1"][6], "sensor-node.kicad_sch")
        # action column
        self.assertIn("OK", rows["R1"][5])
        for r in ("U1", "U2", "L1", "R2", "R3", "C1", "C2", "C10"):
            self.assertIn("Add MPN", rows[r][5], r)
        self.assertEqual(self.h.app.bom_summary_var.get(),
                         "Found 9 components in 2 file(s): 8 to add, 0 to update, 1 OK")
        self.assertEqual(self.h.errors(), [])

    def test_xlsx_gives_identical_matches_to_csv(self):
        csv_rows = self.load(self.lib.bom_csv)
        xlsx_rows = self.load(self.lib.bom_xlsx)
        self.assertEqual(csv_rows, xlsx_rows)
        self.assertEqual(self.h.errors(), [])

    # BUG: XLSX reader only reads <si><t>; a rich-text shared string (<si><r><t>) is dropped, shifting every later string index -> wrong MPNs
    @unittest.expectedFailure
    def test_xlsx_with_rich_text_cell_gives_same_matches(self):
        csv_rows = self.load(self.lib.bom_csv)
        rich = self.lib.project / "bom_rich.xlsx"
        write_xlsx_rich(rich, BOM_ROWS, rich={"Buck"})   # one bold/plain mixed Description cell
        xlsx_rows = self.load(rich)
        self.assertEqual(xlsx_rows, csv_rows)

    def test_bom_refs_not_in_schematic_are_reported(self):
        bom = write_csv(self.lib.project / "extra.csv",
                        BOM_ROWS + [["R99, C99", "1k", "YAGEO", "RC0603FR-071KL", "1k"]])
        rows = self.load(bom)
        self.assertIn("Not found", rows["R99"][5])
        self.assertIn("Not found", rows["C99"][5])
        self.assertIn("2 not found", self.h.app.bom_summary_var.get())
        self.assertIn("2 components in BOM not found", self.h.app.bom_status_var.get())
        # schematic refs that are not in the BOM are not listed at all
        self.assertNotIn("R4", rows)

    def test_update_action_replaces_existing_mpn(self):
        bom = write_csv(self.lib.project / "upd.csv",
                        [BOM_ROWS[0], ["R1", "10k", "Vishay", "CRCW060310K0FKEA", "10k"]])
        rows = self.load(bom)
        self.assertIn("Update", rows["R1"][5])
        self.assertEqual(rows["R1"][3], YAGEO_10K)
        self.apply()
        p = props_of(self.lib.main_sch, "R1")
        self.assertEqual(p["MPN"], "CRCW060310K0FKEA")
        self.assertEqual(p["LCSC"], "C98220")       # untouched
        self.assertEqual(p["Manufacturer"], "Vishay")
        self.assert_valid()

    def test_export_preview_csv(self):
        self.load()
        out = self.lib.root / "preview.csv"
        self.h.files.append(str(out))
        self.h.click(self.tab(), "Export Preview CSV")
        with open(out, newline="", encoding="utf-8") as f:
            data = list(csv.reader(f))
        self.assertEqual(data[0], ["Reference", "Value", "Footprint", "Current MPN", "New MPN", "Action", "File"])
        by_ref = {r[0]: r for r in data[1:]}
        self.assertEqual(len(by_ref), 9)
        self.assertEqual(by_ref["R1"][5], "ok")
        self.assertEqual(by_ref["C10"][5], "add")
        self.assertEqual(by_ref["C10"][6], "power.kicad_sch")
        self.assertEqual(self.h.last_dialog()[0], "showinfo")

    def test_export_before_load_is_an_error(self):
        self.h.select_tab("BOM")
        self.h.click(self.tab(), "Export Preview CSV")
        self.assertEqual(self.h.last_dialog()[:2], ("showerror", "Error"))

    # ── Apply ──
    def test_apply_writes_mpn_to_right_symbols_in_both_sheets(self):
        self.load()
        self.apply()
        kinds = [d[0] for d in self.h.dialogs]
        self.assertIn("askyesno", kinds)
        self.assertEqual(self.h.last_dialog("showinfo")[1], "Success")
        self.assertIn("Updated 8 components in 2 file(s)", self.h.last_dialog("showinfo")[2])

        main, power = self.lib.main_sch, self.lib.power_sch
        self.assertEqual(props_of(main, "U1")["MPN"], "STM32G431CBU6")
        self.assertEqual(props_of(main, "R2")["MPN"], YAGEO_10K)
        self.assertEqual(props_of(main, "R3")["MPN"], YAGEO_10K)
        self.assertEqual(props_of(main, "C1")["MPN"], "CL05B104KO5NNNC")
        self.assertEqual(props_of(main, "C2")["MPN"], "CL05B104KO5NNNC")
        self.assertEqual(props_of(power, "U2")["MPN"], "TPS62133RGTR")
        self.assertEqual(props_of(power, "L1")["MPN"], "NR4018T2R2M")
        self.assertEqual(props_of(power, "C10")["MPN"], "CL21A106KAYNNNE")
        # existing properties untouched
        r2 = props_of(main, "R2")
        self.assertEqual((r2["Value"], r2["Footprint"], r2["Datasheet"]), ("10k", R603, "~"))
        # R1 was already OK -> byte-identical; refs not in BOM untouched
        self.assert_untouched(main, "R1", "R4", "R5")
        self.assert_untouched(power, "R10")
        self.assert_valid()
        # the table reloads and everything is now OK
        for r in self.h.rows(self.h.app.bom_tree):
            self.assertIn("OK", r[5], r)

    def test_apply_also_writes_manufacturer_and_description(self):
        """Documents current behaviour: not just MPN. Manufacturer and
        Description (if absent) from the BOM are written too, but only on
        'add'/'update' rows; the confirm dialog does not mention it."""
        self.load()
        self.apply()
        r2 = props_of(self.lib.main_sch, "R2")
        self.assertEqual(r2["Manufacturer"], "YAGEO")
        self.assertEqual(r2["Description"], "10k 1%")
        # R1 was 'OK' so it gets no Manufacturer -> R1 and R2 now differ
        self.assertNotIn("Manufacturer", props_of(self.lib.main_sch, "R1"))

    def test_apply_creates_backups_of_original_files(self):
        self.load()
        self.apply()
        for p in (self.lib.main_sch, self.lib.power_sch):
            bak = p.with_name(p.name + ".bak")
            self.assertTrue(bak.exists(), bak)
            self.assertEqual(bak.read_text(encoding="utf-8"), self.orig[p])

    def test_apply_twice_is_idempotent(self):
        self.load()
        self.apply()
        after_first = {p: p.read_text(encoding="utf-8") for p in self.orig}
        self.apply()
        self.assertEqual(self.h.last_dialog()[:2], ("showinfo", "No Changes"))
        # reload + apply again from a fresh load too
        self.load()
        self.apply()
        for p, t in after_first.items():
            self.assertEqual(p.read_text(encoding="utf-8"), t)
        for s in placed_symbols(self.lib.main_sch) + placed_symbols(self.lib.power_sch):
            names = [k for k, _ in s["props"]]
            self.assertLessEqual(names.count("MPN"), 1, s["ref"])

    def test_apply_before_load_is_an_error(self):
        self.h.select_tab("BOM")
        self.apply()
        self.assertEqual(self.h.last_dialog()[:2], ("showerror", "Error"))

    def test_declining_confirmation_writes_nothing(self):
        self.load()
        self.h.answer(False)
        self.apply()
        for p, t in self.orig.items():
            self.assertEqual(p.read_text(encoding="utf-8"), t)
        self.assertFalse(self.lib.main_sch.with_name("sensor-node.kicad_sch.bak").exists())

    # BUG: BOM Apply inserts new properties one tab deeper than their siblings (hard-coded "\n\t\t\t"), unlike KiCad's own layout
    @unittest.expectedFailure
    def test_new_property_block_is_formatted_like_kicad(self):
        self.load()
        self.apply()
        self.assertEqual(prop_indent(self.lib.main_sch, "R2", "MPN"),
                         prop_indent(self.lib.main_sch, "R2", "Reference"))

    # BUG: 'add' path appends Manufacturer even when the symbol already has one -> duplicate property in the file
    @unittest.expectedFailure
    def test_add_does_not_duplicate_existing_manufacturer(self):
        body = sch_symbol("R2", "Device:R", "10k", R603, {"Manufacturer": "Yageo Corp"})
        self.lib.main_sch.write_text(schematic(body), encoding="utf-8")
        self.load()
        self.apply()
        props_of(self.lib.main_sch, "R2")   # asserts no duplicate property names

    # BUG: schematic walker only finds "\n\t(symbol\n" (KiCad 8/9 tab layout); KiCad 6/7 files yield zero components -> every BOM row "Not found"
    @unittest.expectedFailure
    def test_kicad7_format_schematic_is_parsed(self):
        k7 = self.lib.project / "k7.kicad_sch"
        k7.write_text(
            '(kicad_sch (version 20230121) (generator eeschema)\n\n'
            f'  (uuid {uuid.uuid4()})\n\n  (paper "A4")\n\n  (lib_symbols\n  )\n\n'
            '  (symbol (lib_id "Device:R") (at 100 100 0) (unit 1)\n'
            '    (in_bom yes) (on_board yes) (dnp no)\n'
            f'    (uuid {uuid.uuid4()})\n'
            '    (property "Reference" "R2" (at 0 0 0)\n      (effects (font (size 1.27 1.27)))\n    )\n'
            '    (property "Value" "10k" (at 0 0 0)\n      (effects (font (size 1.27 1.27)))\n    )\n'
            f'    (property "Footprint" "{R603}" (at 0 0 0)\n      (effects (font (size 1.27 1.27)) hide)\n    )\n'
            '  )\n)\n', encoding="utf-8")
        rows = self.load(sch=k7)
        self.assertIn("Add MPN", rows["R2"][5])

    # BUG: lookup is keyed by reference, so for a multi-unit symbol only the LAST unit gets the MPN
    @unittest.expectedFailure
    def test_multi_unit_symbol_gets_mpn_on_every_unit(self):
        ua = sch_symbol("U3", "Amplifier_Operational:LM358", "LM358", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
        ub = ua.replace("(unit 1)", "(unit 2)").replace(re.search(r'uuid "([^"]+)"', ua).group(1), str(uuid.uuid4()))
        self.lib.main_sch.write_text(schematic(ua + ub), encoding="utf-8")
        bom = write_csv(self.lib.project / "u3.csv", [BOM_ROWS[0], ["U3", "LM358", "TI", "LM358DR", "Op amp"]])
        self.load(bom)
        self.apply()
        units = [s for s in placed_symbols(self.lib.main_sch) if s["ref"] == "U3"]
        self.assertEqual(len(units), 2)
        for u in units:
            self.assertEqual(dict(u["props"]).get("MPN"), "LM358DR", f"unit {u['unit']}")

    # BUG: a sub-sheet used twice has per-instance refs in (instances (path .. (reference "R20"))); walker only reads the Reference property -> R20 "Not found"
    @unittest.expectedFailure
    def test_subsheet_used_twice_second_instance_ref_is_matched(self):
        main = schematic(sch_symbol("R2", "Device:R", "10k", R603), ("power.kicad_sch", "power.kicad_sch"))
        r10 = sch_symbol("R10", "Device:R", "100k", "Resistor_SMD:R_0402_1005Metric")
        inst = ('\t\t(instances\n\t\t\t(project "sensor-node"\n'
                '\t\t\t\t(path "/a/s1"\n\t\t\t\t\t(reference "R10")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n'
                '\t\t\t\t(path "/a/s2"\n\t\t\t\t\t(reference "R20")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n'
                '\t\t\t)\n\t\t)\n')
        r10 = r10[:-3] + inst + "\t)\n"
        self.lib.main_sch.write_text(main, encoding="utf-8")
        self.lib.power_sch.write_text(schematic(r10), encoding="utf-8")
        parse_sexp(self.lib.power_sch.read_text())
        bom = write_csv(self.lib.project / "inst.csv",
                        [BOM_ROWS[0], ["R10, R20", "100k", "YAGEO", "RC0402FR-07100KL", "100k"]])
        rows = self.load(bom)
        self.assertIn("Add MPN", rows["R10"][5])
        self.assertNotIn("Not found", rows["R20"][5])

    # BUG: BOM value is never compared with the schematic value; a stale BOM silently puts a 10k MPN on a 4k7 resistor
    @unittest.expectedFailure
    def test_value_mismatch_between_bom_and_schematic_is_flagged(self):
        bom = write_csv(self.lib.project / "stale.csv", [BOM_ROWS[0], ["R4", "10k", "YAGEO", YAGEO_10K, "10k"]])
        rows = self.load(bom)
        self.assertNotIn("Add MPN", rows["R4"][5], "R4 is 4k7 in the schematic but BOM says 10k")

    # BUG: a BOM row with a blank MPN on a symbol with no MPN is shown as "OK" ('' == ''); nothing was assigned
    @unittest.expectedFailure
    def test_blank_bom_mpn_is_not_reported_ok(self):
        bom = write_csv(self.lib.project / "blank.csv", [BOM_ROWS[0], ["R4", "4k7", "", "", "4k7"]])
        rows = self.load(bom)
        self.assertNotIn("OK", rows["R4"][5])


# ═════════════════════════════════════════════════════════════════════════
# PASSIVES
# ═════════════════════════════════════════════════════════════════════════

class PassivesBase(Base):
    def scan(self, path=None):
        self.h.select_tab("Passives")
        self.h.app.passives_sch_file.set(str(path or self.lib.main_sch))
        self.h.click(self.tab(), "Scan")
        return {r[1]: r for r in self.h.rows(self.h.app.passives_tree)}

    def check(self, *refs):
        """Click the checkbox cell like a user; fall back to the handler if the row is off-screen."""
        tree = self.h.app.passives_tree
        for ref in refs:
            tree.see(ref)
            self.h.pump()
            bbox = tree.bbox(ref, "#1")
            if bbox:
                x, y, w, hgt = bbox
                self._click_time = getattr(self, "_click_time", 0) + 5000   # never a double-click
                tree.event_generate("<Button-1>", x=x + w // 2, y=y + hgt // 2, time=self._click_time)
            else:
                self.h.app._passives_toggle_check(ref)
            self.h.pump()

    def checked(self):
        return set(self.h.app.passives_checked)

    def seed_library(self, **buckets):
        lib = {"version": 1, "resistors": [], "capacitors": [], "inductors": []}
        lib.update(buckets)
        kp.PASSIVES_LIB_FILE.parent.mkdir(parents=True, exist_ok=True)
        kp.PASSIVES_LIB_FILE.write_text(json.dumps(lib), encoding="utf-8")
        self.h.app.passives_library = kp.passives_library_load()
        return lib

    def library(self):
        return json.loads(kp.PASSIVES_LIB_FILE.read_text(encoding="utf-8"))

    def apply_manual(self, mpn="", lcsc=""):
        self.h.app.passives_manual_mpn.set(mpn)
        self.h.app.passives_manual_lcsc.set(lcsc)
        self.h.click(self.tab(), "Apply Manual")


class PassivesScanFilterTests(PassivesBase):
    def test_scan_counts_both_sheets(self):
        rows = self.scan()
        self.assertEqual(set(rows), {"R1", "R2", "R3", "R4", "R5", "R10", "C1", "C2", "C10", "L1"})
        self.assertTrue(self.h.app.passives_status.get().startswith(
            "Found 10 passives  ·  6 R, 3 C, 1 L"))
        self.assertEqual(rows["R1"][5:], ("RC0603FR-0710KL", "C98220"))
        self.assertEqual(rows["C10"][4], "0805")
        self.assertEqual(self.h.app.passives_count_var.get(), "10 shown · 0 checked")

    def test_scan_from_kicad_pro(self):
        rows = self.scan(self.lib.project / "sensor-node.kicad_pro")
        self.assertEqual(len(rows), 10)

    def test_scan_groups_by_type_then_value(self):
        self.scan()
        order = [r[1] for r in self.h.rows(self.h.app.passives_tree)]
        self.assertEqual(order, ["C2", "C1", "C10", "L1", "R4", "R5", "R1", "R2", "R3", "R10"])

    def test_normalize_passive_value(self):
        n = kp.normalize_passive_value
        self.assertEqual(n("10k"), n("10K"))
        self.assertEqual(n("4k7"), n("4.7k"))
        self.assertNotEqual(n("1m"), n("1M"))

    def test_type_status_text_filters(self):
        self.scan()
        a, tree = self.h.app, self.h.app.passives_tree
        refs = lambda: [r[1] for r in self.h.rows(tree)]  # noqa: E731
        a.passives_filter_type.set("Capacitors"); self.h.pump()
        self.assertEqual(sorted(refs()), ["C1", "C10", "C2"])
        a.passives_filter_type.set("Inductors"); self.h.pump()
        self.assertEqual(refs(), ["L1"])
        a.passives_filter_type.set("All")
        a.passives_filter_status.set("Has MPN and LCSC"); self.h.pump()
        self.assertEqual(refs(), ["R1"])
        a.passives_filter_status.set("Missing MPN"); self.h.pump()
        self.assertEqual(len(refs()), 9)
        a.passives_filter_status.set("Missing Footprint"); self.h.pump()
        self.assertEqual(refs(), [])
        a.passives_filter_status.set("All")
        a.passives_filter_text.set("0402"); self.h.pump()
        self.assertEqual(sorted(refs()), ["C1", "C2", "R10"])
        a.passives_filter_text.set("c98220"); self.h.pump()
        self.assertEqual(refs(), ["R1"])
        self.assertEqual(a.passives_count_var.get(), "1 shown · 0 checked")

    def test_check_visible_and_uncheck_all(self):
        self.scan()
        a = self.h.app
        a.passives_filter_text.set("10k"); self.h.pump()     # R1, R2, R3 (10k/10K)
        self.h.click(self.tab(), "Check Visible")
        self.assertEqual(self.checked(), {"R1", "R2", "R3"})
        self.assertIn("3 checked", a.passives_selection_var.get())
        a.passives_filter_text.set(""); self.h.pump()
        self.assertEqual(a.passives_count_var.get(), "10 shown · 3 checked")
        rows = {r[1]: r for r in self.h.rows(a.passives_tree)}
        self.assertEqual(rows["R2"][0], "☑")
        self.h.click(self.tab(), "Uncheck All")
        self.assertEqual(self.checked(), set())
        self.assertEqual(a.passives_selection_var.get(), "No rows checked.")

    def test_check_visible_only_checks_the_anchor_group(self):
        self.scan()
        self.h.app.passives_filter_type.set("Resistors"); self.h.pump()
        self.h.click(self.tab(), "Check Visible")          # anchor = first row R4 (4.7k)
        self.assertEqual(self.checked(), {"R4", "R5"})
        self.assertIn("skipped 4", self.h.app.passives_status.get())

    def test_equivalent_spellings_can_be_checked_together(self):
        self.scan()
        self.check("R2", "R3")                               # 10k + 10K
        self.check("R4")                                     # different group -> blocked
        self.assertEqual(self.checked(), {"R2", "R3"})
        self.assertEqual(self.h.last_dialog()[:2], ("showwarning", "Selection Mismatch"))
        self.h.click(self.tab(), "Uncheck All")
        self.check("R4", "R5")                               # 4k7 + 4.7k
        self.assertEqual(self.checked(), {"R4", "R5"})
        self.check("R4")                                     # toggling off works
        self.assertEqual(self.checked(), {"R5"})

    def test_heterogeneous_selection_is_blocked(self):
        self.scan()
        self.check("R2", "C1", "R10")                        # R10 is 100k/0402
        self.assertEqual(self.checked(), {"R2"})
        self.assertEqual(sum(1 for d in self.h.dialogs if d[1] == "Selection Mismatch"), 2)

    # BUG: normalize_passive_value only canonicalises spelling, not magnitude: 100nF != 0.1uF, so C1/C2 can't be grouped or share a library entry
    @unittest.expectedFailure
    def test_100nF_and_0u1F_are_the_same_part(self):
        self.scan()
        self.check("C1", "C2")
        self.assertEqual(self.checked(), {"C1", "C2"})

    # BUG: tree rows use iid=ref, so a duplicated reference (e.g. R2 in two sheets) raises TclError mid-refresh and the table is left half-filled
    @unittest.expectedFailure
    def test_duplicate_reference_does_not_break_scan(self):
        power = self.lib.power_sch.read_text()
        self.lib.power_sch.write_text(power.replace('"R10"', '"R2"'), encoding="utf-8")
        self.scan()
        rows = self.h.rows(self.h.app.passives_tree)
        self.assertEqual(self.cb_errors, [])
        self.assertEqual(len(rows), 10)


class PassivesApplyTests(PassivesBase):
    def test_apply_manual_writes_only_checked_symbols(self):
        self.scan()
        self.check("R2", "R3")
        self.apply_manual("RC0603FR-0710KL", "C25804")
        self.assertIn("Updated 2 components", self.h.last_dialog("showinfo")[2])
        m = self.lib.main_sch
        for r in ("R2", "R3"):
            p = props_of(m, r)
            self.assertEqual((p["MPN"], p["LCSC"]), (YAGEO_10K, "C25804"))
            self.assertEqual(p["Footprint"], R603)
        self.assertEqual(props_of(m, "R3")["Value"], "10K")          # value text not normalized on disk
        self.assert_untouched(m, "R1", "R4", "R5", "C1", "C2", "U1")
        self.assertEqual(self.lib.power_sch.read_text(), self.orig[self.lib.power_sch])
        self.assert_valid()
        # new property formatted like KiCad's own (same indent as siblings)
        self.assertEqual(prop_indent(m, "R2", "MPN"), prop_indent(m, "R2", "Reference"))
        # backup + table refreshed + selection cleared
        self.assertEqual(m.with_name(m.name + ".bak").read_text(), self.orig[m])
        rows = {r[1]: r for r in self.h.rows(self.h.app.passives_tree)}
        self.assertEqual(rows["R3"][5:], (YAGEO_10K, "C25804"))
        self.assertEqual(self.checked(), set())

    def test_apply_manual_to_subsheet(self):
        self.scan()
        self.check("R10")
        self.apply_manual(lcsc="C25741")
        p = props_of(self.lib.power_sch, "R10")
        self.assertEqual(p["LCSC"], "C25741")
        self.assertNotIn("MPN", p)                                   # only LCSC given
        self.assertEqual(self.lib.main_sch.read_text(), self.orig[self.lib.main_sch])
        self.assert_valid()

    def test_apply_manual_requires_input_and_selection(self):
        self.scan()
        self.apply_manual()
        self.assertEqual(self.h.last_dialog()[:2], ("showwarning", "Nothing to Apply"))
        self.apply_manual("X")
        self.assertEqual(self.h.last_dialog()[:2], ("showwarning", "No Selection"))
        for p, t in self.orig.items():
            self.assertEqual(p.read_text(), t)

    def test_apply_twice_is_idempotent(self):
        self.scan()
        self.check("R2")
        self.apply_manual(YAGEO_10K, "C25804")
        first = self.lib.main_sch.read_text()
        self.check("R2")
        self.apply_manual(YAGEO_10K, "C25804")                      # same values -> no conflict prompt
        self.assertEqual(self.lib.main_sch.read_text(), first)
        props_of(self.lib.main_sch, "R2")                           # no duplicates

    def test_overwrite_all_confirmation(self):
        self.scan()
        self.check("R1", "R2")
        self.h.click_later("Overwrite All")
        self.apply_manual("RC0603JR-0710KL")
        self.assertEqual(props_of(self.lib.main_sch, "R1")["MPN"], "RC0603JR-0710KL")
        self.assertEqual(props_of(self.lib.main_sch, "R1")["LCSC"], "C98220")
        self.assertEqual(props_of(self.lib.main_sch, "R2")["MPN"], "RC0603JR-0710KL")
        self.assert_valid()

    def test_overwrite_cancel_writes_nothing(self):
        self.scan()
        self.check("R1", "R2")
        self.h.click_later("Cancel")
        self.apply_manual("RC0603JR-0710KL")
        for p, t in self.orig.items():
            self.assertEqual(p.read_text(), t)
        self.assertEqual(self.checked(), {"R1", "R2"})

    def _add_library_entry_via_manager(self, tab_index, values):
        self.h.click(self.tab(), "Manage Library")
        mgr = self.h.last_window()
        nb = next(w for w in self.h.walk(mgr) if isinstance(w, ttk.Notebook))
        nb.select(tab_index); self.h.pump()
        self.h.click(mgr, "Add")
        form = self.h.last_window()
        self.fill(form, values)
        self.h.click(form, "Save")
        self.assertFalse(form.winfo_exists())
        return mgr

    def test_apply_from_library_added_via_manager(self):
        self.scan()
        mgr = self._add_library_entry_via_manager(0, {
            "Value": "10k", "Package (full KiCad footprint)": R603, "Tolerance": "1%",
            "Power": "0.1W", "Manufacturer": "Yageo", "MPN": YAGEO_10K, "LCSC": "C25804"})
        nb = next(w for w in self.h.walk(mgr) if isinstance(w, ttk.Notebook))
        self.assertEqual(self.h.rows(self.h.trees(nb.nametowidget(nb.tabs()[0]))[0])[0][3], YAGEO_10K)
        self.h.click(mgr, "Close")
        lib = self.library()
        self.assertEqual(lib["resistors"][0]["id"], "r_0001")
        self.assertEqual(lib["resistors"][0]["mpn"], YAGEO_10K)

        self.check("R2", "R3")
        self.assertIn(YAGEO_10K, self.h.app.passives_lib_combo_var.get())
        self.h.click(self.tab(), "Apply from Library")
        for r in ("R2", "R3"):
            p = props_of(self.lib.main_sch, r)
            self.assertEqual((p["MPN"], p["LCSC"], p["Tolerance"], p["Power"], p["Manufacturer"]),
                             (YAGEO_10K, "C25804", "1%", "0.1W", "Yageo"))
            self.assertNotIn("Voltage", p)                           # empty lib fields not written
            self.assertEqual(p["Datasheet"], "~")
        self.assert_untouched(self.lib.main_sch, "R1", "R4")
        self.assert_valid()

    def test_apply_from_library_subsheet_capacitor(self):
        self.seed_library(capacitors=[{"id": "c_0001", "value": "10uF", "package": "Capacitor_SMD:C_0805_2012Metric",
                                       "voltage": "25V", "dielectric": "X5R", "mpn": "CL21A106KAYNNNE",
                                       "lcsc": "C15850"}])
        self.scan()
        self.check("C10")
        self.h.click(self.tab(), "Apply from Library")
        p = props_of(self.lib.power_sch, "C10")
        self.assertEqual((p["MPN"], p["LCSC"], p["Voltage"], p["Dielectric"]),
                         ("CL21A106KAYNNNE", "C15850", "25V", "X5R"))
        self.assertEqual(self.lib.main_sch.read_text(), self.orig[self.lib.main_sch])
        self.assert_valid()

    def test_apply_from_library_without_match_warns(self):
        self.scan()
        self.check("R10")
        self.assertEqual(self.h.app.passives_lib_combo_var.get(), "(no library matches)")
        self.h.click(self.tab(), "Apply from Library")
        self.assertEqual(self.h.last_dialog()[:2], ("showwarning", "No Library Match"))

    # BUG: a library datasheet URL "conflicts" with KiCad's empty placeholder "~", so every apply pops the overwrite dialog
    @unittest.expectedFailure
    def test_library_datasheet_does_not_prompt_over_tilde(self):
        self.seed_library(resistors=[{"id": "r_0001", "value": "4.7k", "package": R603, "mpn": "RC0603FR-074K7L",
                                      "datasheet": "https://example.com/yageo.pdf"}])
        self.scan()
        self.check("R4")
        self.h.click_later("Cancel", delay_ms=300)                  # only reached if the prompt appears
        self.h.click(self.tab(), "Apply from Library")
        self.assertEqual(props_of(self.lib.main_sch, "R4").get("MPN"), "RC0603FR-074K7L")

    # BUG: paren matching ignores quoted strings; a ')' inside a property value makes the new property land INSIDE that string -> corrupt file
    @unittest.expectedFailure
    def test_paren_inside_property_value_does_not_corrupt_file(self):
        body = (sch_symbol("R2", "Device:R", "10k", R603, {"Note": "see note 1)"}))
        self.lib.main_sch.write_text(schematic(body), encoding="utf-8")
        self.orig[self.lib.main_sch] = self.lib.main_sch.read_text()
        self.scan()
        self.check("R2")
        self.apply_manual(YAGEO_10K)
        self.assert_valid(self.lib.main_sch)
        p = props_of(self.lib.main_sch, "R2")
        self.assertEqual((p["MPN"], p["Note"]), (YAGEO_10K, "see note 1)"))

    def test_edit_row_changes_one_symbol(self):
        self.scan()
        tree = self.h.app.passives_tree
        tree.focus("R4")
        self.h.click(self.tab(), "Edit Row")
        dlg = self.h.last_window()
        vals = self.form_values(dlg)
        self.assertEqual(vals["Value"], "4k7")
        self.assertEqual(vals["Footprint"], R603)
        self.fill(dlg, {"LCSC": "C23162", "Value": "4.7k"})
        self.h.click(dlg, "Save Changes")
        self.assertIn("Value: '4k7' → '4.7k'", self.h.last_dialog("askyesno")[2])
        p = props_of(self.lib.main_sch, "R4")
        self.assertEqual((p["Value"], p["LCSC"]), ("4.7k", "C23162"))
        self.assertNotIn("MPN", p)
        self.assert_untouched(self.lib.main_sch, "R5", "R1")
        self.assert_valid()

    def test_edit_row_without_focus(self):
        self.scan()
        self.h.click(self.tab(), "Edit Row")
        self.assertEqual(self.h.last_dialog()[:2], ("showinfo", "No Row"))

    # BUG: Edit Row says "Empty fields will be left untouched" but clearing a field writes "" over the existing value
    @unittest.expectedFailure
    def test_edit_row_empty_field_left_untouched(self):
        self.scan()
        self.h.app.passives_tree.focus("R1")
        self.h.click(self.tab(), "Edit Row")
        dlg = self.h.last_window()
        self.fill(dlg, {"LCSC": "", "Tolerance": "1%"})
        self.h.click(dlg, "Save Changes")
        p = props_of(self.lib.main_sch, "R1")
        self.assertEqual(p["Tolerance"], "1%")
        self.assertEqual(p["LCSC"], "C98220")


class PassivesLibraryTests(PassivesBase):
    def test_save_selected_to_library(self):
        self.scan()
        self.check("R4", "R5")
        self.h.app.passives_manual_mpn.set("RC0603FR-074K7L")
        self.h.click(self.tab(), "Save Selected to Library")
        form = self.h.last_window()
        vals = self.form_values(form)
        self.assertEqual((vals["Value"], vals["Package (full KiCad footprint)"], vals["MPN"]),
                         ("4.7k", R603, "RC0603FR-074K7L"))
        self.fill(form, {"Tolerance": "1%"})
        self.h.click(form, "Save")
        lib = self.library()
        self.assertEqual(len(lib["resistors"]), 1)
        e = lib["resistors"][0]
        self.assertEqual((e["id"], e["value"], e["package"], e["mpn"], e["tolerance"]),
                         ("r_0001", "4.7k", R603, "RC0603FR-074K7L", "1%"))
        self.assertIn("RC0603FR-074K7L", self.h.app.passives_lib_combo_var.get())
        # nothing written to schematics
        for p, t in self.orig.items():
            self.assertEqual(p.read_text(), t)

    def test_save_selected_requires_mpn_or_lcsc(self):
        self.scan()
        self.check("R4")
        self.h.click(self.tab(), "Save Selected to Library")
        form = self.h.last_window()
        self.h.click(form, "Save")
        self.assertEqual(self.h.last_dialog()[:2], ("showwarning", "Missing Fields"))
        self.assertTrue(form.winfo_exists())
        self.assertFalse(kp.PASSIVES_LIB_FILE.exists())

    def _make_three_complete_groups(self):
        self.scan()
        self.check("C10")
        self.apply_manual("CL21A106KAYNNNE", "C15850")
        self.check("L1")
        self.apply_manual("NR4018T2R2M")
        # R1 already has MPN+LCSC; queue order = C10, L1, R1

    def test_save_all_complete_walkthrough(self):
        self._make_three_complete_groups()
        self.h.click(self.tab(), "Save All Complete")
        dlg = self.h.last_window()
        self.assertIn("1 of 3", " ".join(str(w.cget("text")) for w in self.h.walk(dlg)
                                          if isinstance(w, ttk.Label)))
        self.fill(dlg, {"Voltage": "25V"})
        self.h.click(dlg, "Save & Next")                            # C10 saved with voltage
        self.h.click(dlg, "Skip")                                   # L1 skipped
        self.h.click(dlg, "Save All Remaining")                     # R1 minimal, confirm = True
        self.assertFalse(dlg.winfo_exists())
        lib = self.library()
        self.assertEqual([e["mpn"] for e in lib["capacitors"]], ["CL21A106KAYNNNE"])
        self.assertEqual(lib["capacitors"][0]["voltage"], "25V")
        self.assertEqual(lib["capacitors"][0]["value"], "10uF")
        self.assertEqual(lib["inductors"], [])
        self.assertEqual([(e["id"], e["mpn"], e["lcsc"]) for e in lib["resistors"]],
                         [("r_0001", YAGEO_10K, "C98220")])
        self.assertEqual(self.h.last_dialog("showinfo")[1], "Walkthrough Complete")
        # running again: only the skipped L1 is left
        self.h.click(self.tab(), "Save All Complete")
        dlg = self.h.last_window()
        self.assertIn("1 of 1", " ".join(str(w.cget("text")) for w in self.h.walk(dlg)
                                          if isinstance(w, ttk.Label)))
        self.h.click(dlg, "Cancel")
        self.assertEqual(self.library()["inductors"], [])

    def test_save_all_cancel_keeps_already_saved(self):
        self._make_three_complete_groups()
        self.h.click(self.tab(), "Save All Complete")
        dlg = self.h.last_window()
        self.h.click(dlg, "Save & Next")
        self.h.click(dlg, "Cancel")
        self.assertFalse(dlg.winfo_exists())
        lib = self.library()
        self.assertEqual(len(lib["capacitors"]), 1)
        self.assertEqual(lib["resistors"] + lib["inductors"], [])

    def test_save_all_nothing_new(self):
        self.seed_library(resistors=[{"id": "r_0001", "value": "10k", "package": R603, "mpn": YAGEO_10K}])
        self.scan()
        self.h.click(self.tab(), "Save All Complete")
        self.assertEqual(self.h.last_dialog()[:2], ("showinfo", "Nothing to Save"))

    def _open_manager(self, tab_index=0):
        self.h.select_tab("Passives")
        self.h.click(self.tab(), "Manage Library")
        mgr = self.h.last_window()
        nb = next(w for w in self.h.walk(mgr) if isinstance(w, ttk.Notebook))
        nb.select(tab_index); self.h.pump()
        trees = [self.h.trees(nb.nametowidget(t))[0] for t in nb.tabs()]   # R, C, L
        return mgr, trees

    SEED = [{"id": "r_0001", "value": "10k", "package": R603, "mpn": YAGEO_10K, "lcsc": "C25804"},
            {"id": "r_0002", "value": "4.7k", "package": R603, "mpn": "RC0603FR-074K7L", "lcsc": "C23162"}]

    def test_manager_edit_and_remove(self):
        self.seed_library(resistors=[dict(e) for e in self.SEED])
        mgr, trees = self._open_manager()
        self.assertEqual(len(self.h.rows(trees[0])), 2)
        trees[0].focus("r_0002")
        self.h.click(mgr, "Edit")
        form = self.h.last_window()
        self.assertEqual(self.form_values(form)["MPN"], "RC0603FR-074K7L")
        self.fill(form, {"Tolerance": "1%", "LCSC": "C99999"})
        self.h.click(form, "Save")
        e = self.library()["resistors"][1]
        self.assertEqual((e["id"], e["lcsc"], e["tolerance"]), ("r_0002", "C99999", "1%"))
        self.assertEqual(len(self.library()["resistors"]), 2)

        trees[0].focus("r_0001")
        self.h.answer(False)
        self.h.click(mgr, "Remove")
        self.assertEqual(len(self.library()["resistors"]), 2)
        self.h.click(mgr, "Remove")
        self.assertEqual([e["id"] for e in self.library()["resistors"]], ["r_0002"])
        self.assertEqual([r[3] for r in self.h.rows(trees[0])], ["RC0603FR-074K7L"])

    def test_manager_add_uses_next_id(self):
        self.seed_library(resistors=[dict(e) for e in self.SEED])
        mgr, _ = self._open_manager()
        self.h.click(mgr, "Add")
        form = self.h.last_window()
        self.fill(form, {"Value": "100k", "Package (full KiCad footprint)": "Resistor_SMD:R_0402_1005Metric",
                         "MPN": "RC0402FR-07100KL"})
        self.h.click(form, "Save")
        self.assertEqual([e["id"] for e in self.library()["resistors"]], ["r_0001", "r_0002", "r_0003"])

    def test_manager_export(self):
        lib = self.seed_library(resistors=[dict(e) for e in self.SEED])
        mgr, _ = self._open_manager()
        out = self.lib.root / "export.json"
        self.h.files.append(str(out))
        self.h.click(mgr, "Export")
        self.assertEqual(json.loads(out.read_text()), lib)
        self.assertIn("Exported 2 entries", self.h.last_dialog("showinfo")[2])

    def _import(self, data, answer):
        src = self.lib.root / "import.json"
        src.write_text(json.dumps(data))
        mgr, trees = self._open_manager()
        self.h.files.append(str(src))
        self.h.answer(answer)
        self.h.click(mgr, "Import")
        return trees

    def test_import_merge(self):
        self.seed_library(resistors=[dict(self.SEED[0])])
        trees = self._import({"resistors": [dict(self.SEED[0]), dict(self.SEED[1])],
                              "capacitors": [{"id": "c_0001", "value": "100nF",
                                              "package": "Capacitor_SMD:C_0402_1005Metric", "mpn": "CL05B104KO5NNNC"}]},
                             True)
        lib = self.library()
        self.assertEqual([e["id"] for e in lib["resistors"]], ["r_0001", "r_0002"])
        self.assertEqual(len(lib["capacitors"]), 1)
        self.assertEqual(len(self.h.rows(trees[0])), 2)
        self.assertEqual(len(self.h.rows(trees[1])), 1)

    def test_import_replace(self):
        self.seed_library(resistors=[dict(e) for e in self.SEED])
        self._import({"inductors": [{"id": "l_0001", "value": "2.2uH", "package": "x", "mpn": "NR4018T2R2M"}]}, False)
        lib = self.library()
        self.assertEqual(lib["resistors"], [])
        self.assertEqual([e["mpn"] for e in lib["inductors"]], ["NR4018T2R2M"])

    def test_import_cancel_changes_nothing(self):
        lib = self.seed_library(resistors=[dict(e) for e in self.SEED])
        self._import({"resistors": []}, None)
        self.assertEqual(self.library(), lib)

    def test_import_bad_json(self):
        self.seed_library()
        src = self.lib.root / "bad.json"
        src.write_text("{nope")
        mgr, _ = self._open_manager()
        self.h.files.append(str(src))
        self.h.click(mgr, "Import")
        self.assertEqual(self.h.last_dialog()[:2], ("showerror", "Import Failed"))

    # BUG: merge de-dupes by id only; libraries from two machines both number from r_0001, so different parts are silently dropped
    @unittest.expectedFailure
    def test_import_merge_keeps_different_part_with_same_id(self):
        self.seed_library(resistors=[dict(self.SEED[0])])
        other = {"id": "r_0001", "value": "1k", "package": R603, "mpn": "RC0603FR-071KL"}
        self._import({"resistors": [other]}, True)
        mpns = [e["mpn"] for e in self.library()["resistors"]]
        self.assertIn("RC0603FR-071KL", mpns)
        ids = [e["id"] for e in self.library()["resistors"]]
        self.assertEqual(len(ids), len(set(ids)), "ids must stay unique")


if __name__ == "__main__":
    unittest.main()
