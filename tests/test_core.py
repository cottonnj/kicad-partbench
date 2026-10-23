"""Tests for the non-GUI helpers in kicad_partbench.py (stdlib unittest only)."""
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kicad_partbench as kp  # noqa: E402


class PassiveValueTests(unittest.TestCase):
    def test_rkm_notation(self):
        self.assertEqual(kp.normalize_passive_value("4k7"), "4.7k")
        self.assertEqual(kp.normalize_passive_value("4.7k"), "4.7k")

    def test_micro_sign_folds_to_u(self):
        self.assertEqual(kp.normalize_passive_value("10µF"), "10uF")

    def test_milli_and_mega_stay_distinct(self):
        self.assertNotEqual(kp.normalize_passive_value("1m"),
                            kp.normalize_passive_value("1M"))

    def test_type_from_reference(self):
        self.assertEqual(kp._passive_type_from_ref("R1"), "R")
        self.assertEqual(kp._passive_type_from_ref("C12"), "C")
        self.assertEqual(kp._passive_type_from_ref("L3"), "L")
        self.assertIsNone(kp._passive_type_from_ref("U1"))

    def test_footprint_short(self):
        self.assertEqual(kp.footprint_short("Resistor_SMD:R_0603_1608Metric"), "0603")


class OctopartBomTests(unittest.TestCase):
    def test_csv_bom(self):
        with tempfile.TemporaryDirectory() as d:
            bom = Path(d) / "bom.csv"
            # Excel-style UTF-8 BOM, grouped designators, numeric-looking MPN, empty cells
            bom.write_bytes(
                "﻿Designator,Matched Part Number,Value,Footprint\n"
                "\"R1, R2\",RC0603FR-0710KL,10k,\n"
                "C1,0603,100nF,C_0603\n".encode("utf-8"))
            rows = kp.parse_octopart_bom(bom)
        self.assertEqual([r["ref"] for r in rows], ["R1", "R2", "C1"])
        self.assertEqual(rows[0]["mpn"], "RC0603FR-0710KL")
        self.assertEqual(rows[0]["footprint"], "")
        self.assertEqual(rows[2]["mpn"], "0603")

    def test_missing_reference_column(self):
        with tempfile.TemporaryDirectory() as d:
            bom = Path(d) / "bom.csv"
            bom.write_text("Part,MPN\nx,y\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                kp.parse_octopart_bom(bom)


SCH = textwrap.dedent("""\
    (kicad_sch
    \t(version 20231120)
    \t(generator "eeschema")
    \t(lib_symbols
    \t)
    \t(symbol
    \t\t(lib_id "Device:R")
    \t\t(property "Reference" "R1"
    \t\t)
    \t\t(property "Value" "10k"
    \t\t)
    \t\t(property "Footprint" "Resistor_SMD:R_0603_1608Metric"
    \t\t)
    \t\t(property "MPN" "RC0603FR-0710KL"
    \t\t)
    \t)
    \t(symbol
    \t\t(lib_id "Device:C")
    \t\t(property "Reference" "C1"
    \t\t)
    \t\t(property "Value" "100nF"
    \t\t)
    \t\t(property "Footprint" "Capacitor_SMD:C_0402_1005Metric"
    \t\t)
    \t)
    )
    """)


class SchematicTests(unittest.TestCase):
    def test_parse_components(self):
        with tempfile.TemporaryDirectory() as d:
            sch = Path(d) / "board.kicad_sch"
            sch.write_text(SCH, encoding="utf-8")
            data = kp.parse_kicad_schematic(sch)
            passives = kp.parse_passives_from_project(sch)
        refs = {c["ref"]: c for c in data["components"]}
        self.assertEqual(set(refs), {"R1", "C1"})
        self.assertEqual(refs["R1"]["mpn"], "RC0603FR-0710KL")
        by_ref = {p["ref"]: p for p in passives}
        self.assertEqual(by_ref["C1"]["footprint_short"], "0402")
        self.assertEqual(by_ref["R1"]["mpn"], "RC0603FR-0710KL")


FIXTURES = Path(__file__).resolve().parent / "fixtures"
M050_PCB = FIXTURES / "m050_rev12_board.kicad_pcb"


def _fp(at, pads, layer="F.Cu", ref="U1", extra=""):
    """A minimal (footprint ...) block. pads = [(number, x, y, w, h, angle_or_None)]."""
    lines = [f'\t(footprint "Test:FP"', f'\t\t(layer "{layer}")', f"\t\t(at {at})",
             f'\t\t(property "Reference" "{ref}"\n\t\t\t(at 0 0 0)\n\t\t)']
    if extra:
        lines.append(extra)
    for num, x, y, w, h, a in pads:
        at_s = f"{x} {y}" if a is None else f"{x} {y} {a}"
        lines.append(f'\t\t(pad "{num}" smd rect\n\t\t\t(at {at_s})\n\t\t\t(size {w} {h})'
                     f'\n\t\t\t(layers "F.Cu")\n\t\t)')
    lines.append("\t)")
    return "\n".join(lines)


def _board(*footprints, aux="0 0"):
    return ("(kicad_pcb\n\t(version 20260206)\n\t(setup\n\t\t(aux_axis_origin "
            + aux + ")\n\t)\n" + "\n".join(footprints) + "\n)\n")


# Two square pads whose centre is at local (3, 0)
TWO_PADS = [("1", 2, 0, 0.5, 0.5, None), ("2", 4, 0, 0.5, 0.5, None)]


class PadSpecTests(unittest.TestCase):
    def test_ranges_lists_and_names(self):
        self.assertEqual(kp.parse_pad_spec("1-3, 5,EP"), {"1", "2", "3", "5", "EP"})

    def test_empty(self):
        self.assertEqual(kp.parse_pad_spec("  "), set())

    def test_bad_specs(self):
        for bad in ("3-1", "A1-A3", "1-2-3", "1,,2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                kp.parse_pad_spec(bad)


class PcbParseTests(unittest.TestCase):
    def test_quoted_paren_does_not_end_block(self):
        extra = '\t\t(property "Description" "header (2x5, :) pitch"\n\t\t\t(at 0 0 0)\n\t\t)'
        fp = kp.parse_pcb_footprint(_board(_fp("10 20", TWO_PADS, extra=extra)), "U1")
        self.assertEqual([p["number"] for p in fp["pads"]], ["1", "2"])

    def test_missing_ref(self):
        with self.assertRaises(KeyError):
            kp.parse_pcb_footprint(_board(_fp("0 0", TWO_PADS)), "U9")

    def test_list_footprints_biggest_first(self):
        footprints = kp.list_pcb_footprints(M050_PCB.read_text(encoding="utf-8"))
        self.assertEqual(footprints[0]["ref"], "Module1")
        self.assertEqual(footprints[0]["pad_count"], 204)
        self.assertEqual({f["ref"] for f in footprints}, {"Module1", "J6", "J7"})

    def test_aux_origin_and_courtyard(self):
        fp = kp.parse_pcb_footprint(M050_PCB.read_text(encoding="utf-8"), "Module1")
        self.assertEqual(fp["aux"], (48.5, 179.7))
        self.assertEqual((fp["x"], fp["y"], fp["angle"]), (103.95, 92.94, 90.0))
        self.assertEqual(len(fp["lines"]), 8)


class PlacementTransformTests(unittest.TestCase):
    def place(self, angle, layer="F.Cu", sub=None):
        fp = kp.parse_pcb_footprint(_board(_fp(f"10 20 {angle}", TWO_PADS, layer=layer)), "U1")
        sub = sub or {"ref": "J1", "pads": "1-2"}
        return kp.subpart_placement(fp, sub)

    def test_pads_mode_top(self):
        # KiCad is y-down and angles turn counter-clockwise on screen
        expected = {0: (13, 20, 0.0), 90: (10, 17, 90.0), 180: (7, 20, 180.0), 270: (10, 23, 270.0)}
        for angle, (x, y, rot) in expected.items():
            with self.subTest(angle=angle):
                p = self.place(angle)
                self.assertAlmostEqual(p["board_x"], x, places=6)
                self.assertAlmostEqual(p["board_y"], y, places=6)
                self.assertAlmostEqual(p["cpl_x"], x, places=6)
                self.assertAlmostEqual(p["cpl_y"], -y, places=6)
                self.assertEqual(p["rotation"], rot)
                self.assertEqual(p["layer"], "top")

    def test_pads_mode_bottom_mirrors_rotation(self):
        expected = {0: 180.0, 90: 90.0, 180: 0.0, 270: 270.0}
        for angle, rot in expected.items():
            with self.subTest(angle=angle):
                p = self.place(angle, layer="B.Cu")
                self.assertEqual(p["rotation"], rot)
                self.assertEqual(p["layer"], "bottom")

    def test_rotation_offset_wraps(self):
        p = self.place(90, sub={"ref": "J1", "pads": "1-2", "rotation_offset": -90})
        self.assertEqual(p["rotation"], 0.0)
        p = self.place(270, sub={"ref": "J1", "pads": "1-2", "rotation_offset": 180})
        self.assertEqual(p["rotation"], 90.0)

    def test_offset_mode_turns_with_parent(self):
        expected = {0: (13, 20), 90: (10, 17), 180: (7, 20), 270: (10, 23)}
        for angle, (x, y) in expected.items():
            for layer in ("F.Cu", "B.Cu"):
                with self.subTest(angle=angle, layer=layer):
                    p = self.place(angle, layer=layer, sub={
                        "ref": "J1", "mode": "offset", "xy_offset_mm": [3, 0]})
                    self.assertAlmostEqual(p["board_x"], x, places=6)
                    self.assertAlmostEqual(p["board_y"], y, places=6)

    def test_nudge_is_in_parent_frame(self):
        p = self.place(90, sub={"ref": "J1", "pads": "1-2", "xy_offset_mm": [1, 0]})
        self.assertAlmostEqual(p["board_x"], 10, places=6)
        self.assertAlmostEqual(p["board_y"], 16, places=6)

    def test_pad_box_uses_absolute_pad_angle(self):
        # Pad angles in the file already include the footprint rotation.
        pads = [("1", 0, 0, 2, 0.5, 90), ("2", 5, 1, 0.5, 0.5, 90)]
        fp = kp.parse_pcb_footprint(_board(_fp("0 0 90", pads)), "U1")
        p = kp.subpart_placement(fp, {"ref": "J1", "pads": "1,2"})
        self.assertAlmostEqual(p["board_x"], 0.5, places=6)
        self.assertAlmostEqual(p["board_y"], -2.125, places=6)

    def test_missing_pads(self):
        fp = kp.parse_pcb_footprint(_board(_fp("0 0", TWO_PADS)), "U1")
        p = kp.subpart_placement(fp, {"ref": "J1", "pads": "1-3"})
        self.assertTrue(any("3" in w for w in p["warnings"]))
        with self.assertRaises(ValueError):
            kp.subpart_placement(fp, {"ref": "J1", "pads": "7-9"})


class M050FixtureTests(unittest.TestCase):
    """Real Rev-12 board: Module1 at (103.95, 92.94, 90), aux origin (48.5, 179.7)."""

    def setUp(self):
        self.text = M050_PCB.read_text(encoding="utf-8")
        self.config = {"version": 1, "parents": [{
            "ref": "Module1", "drop_parent_from_cpl": True,
            "subparts": kp.apply_preset(kp.MULTIPART_PRESETS[kp.CM5_PRESET])}]}

    def test_cm5_preset_placements(self):
        placements, warnings = kp.multipart_placements(self.text, self.config)
        self.assertEqual(warnings, [])
        expected = {"J6": (82.45, 59.44, 33.95, 120.26), "J7": (82.45, 93.44, 33.95, 86.26)}
        for ref, (bx, by, cx, cy) in expected.items():
            p = placements[ref]
            self.assertAlmostEqual(p["board_x"], bx, places=6)
            self.assertAlmostEqual(p["board_y"], by, places=6)
            self.assertAlmostEqual(p["cpl_x"], cx, places=6)
            self.assertAlmostEqual(p["cpl_y"], cy, places=6)
            # Rev-4 as ordered: both connectors at 0 with Module1 at 90
            self.assertEqual(p["rotation"], 0.0)

    def test_preset_mismatch_warns(self):
        preset = json_copy(kp.MULTIPART_PRESETS[kp.CM5_PRESET])
        preset["subparts"][0]["expect_local_mm"] = [34.5, -21.5]
        self.config["parents"][0]["subparts"] = kp.apply_preset(preset)
        _placements, warnings = kp.multipart_placements(self.text, self.config)
        self.assertEqual(len(warnings), 1)
        self.assertIn("J6", warnings[0])

    def test_ghosts(self):
        placements, _ = kp.multipart_placements(self.text, self.config)
        ghosts = {g["ref"]: g for g in kp.find_ghosts(self.text, self.config, placements)}
        self.assertAlmostEqual(ghosts["J6"]["distance_mm"], 0.0, places=6)
        self.assertAlmostEqual(ghosts["J7"]["distance_mm"], 48.650806, places=5)


def json_copy(obj):
    import json
    return json.loads(json.dumps(obj))


CPL_HEADER = ["Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer"]
CONN = "CONN-SMD_100P-P0.40_10164227-1001A1RLF"
CPL_ROWS = [
    ["C1", "100nF", "C_0402", "10.0", "20.0", "0.0", "top"],
    ["J7", "GT-B0403FSV14-100B1101", CONN, "33.999911", "86.101148", "0.0", "top"],
    ["Module1", "ComputeModule5-CM5", "Raspberry-Pi-5-Compute-Module", "31.5", "103.102274",
     "90.0", "top"],
    ["R1", "10k", "R_0402", "1.0", "2.0", "90.0", "top"],
]
BOM_HEADER = ["Comment", "Designator", "Footprint", "LCSC", "Quantity"]
BOM_ROWS = [
    ["100nF", "C1,C2", "C_0402", "C1525", "2"],
    ["GT-B0403FSV14-100B1101", "J7", CONN, "C42376244", "1"],
    ["ComputeModule5-CM5", "Module1,U5", "Raspberry-Pi-5-Compute-Module", "", "2"],
    ["10k", "R1", "R_0402", "C25744", "1"],
]


class PatchTests(unittest.TestCase):
    def setUp(self):
        text = M050_PCB.read_text(encoding="utf-8")
        self.config = {"version": 1, "parents": [{
            "ref": "Module1", "drop_parent_from_cpl": True,
            "subparts": kp.apply_preset(kp.MULTIPART_PRESETS[kp.CM5_PRESET])}]}
        self.placements, _ = kp.multipart_placements(text, self.config)

    def test_patch_cpl(self):
        rows, diff = kp.patch_cpl(CPL_HEADER, CPL_ROWS, self.config, self.placements)
        self.assertEqual([r[0] for r in rows], ["C1", "J6", "J7", "R1"])
        self.assertEqual(rows[1], ["J6", "GT-B0403FSV14-100B1101", CONN, "33.95", "120.26",
                                   "0.0", "top"])
        self.assertEqual(rows[2][3:6], ["33.95", "86.26", "0.0"])
        kinds = {d["ref"]: d["kind"] for d in diff}
        self.assertEqual(kinds, {"Module1": "removed", "J7": "replaced", "J6": "added"})

    def test_patch_cpl_twice_is_same(self):
        once, _ = kp.patch_cpl(CPL_HEADER, CPL_ROWS, self.config, self.placements)
        twice, diff = kp.patch_cpl(CPL_HEADER, once, self.config, self.placements)
        self.assertEqual(once, twice)
        self.assertTrue(all(d["kind"] == "unchanged" for d in diff))

    def test_patch_bom(self):
        rows, diff = kp.patch_bom(BOM_HEADER, BOM_ROWS, self.config)
        self.assertEqual(rows, [
            ["100nF", "C1,C2", "C_0402", "C1525", "2"],
            ["GT-B0403FSV14-100B1101", "J6,J7", CONN, "C42376244", "2"],
            ["ComputeModule5-CM5", "U5", "Raspberry-Pi-5-Compute-Module", "", "1"],
            ["10k", "R1", "R_0402", "C25744", "1"],
        ])
        kinds = sorted(d["kind"] for d in diff)
        self.assertEqual(kinds, ["changed", "replaced"])

    def test_patch_bom_twice_is_same(self):
        once, _ = kp.patch_bom(BOM_HEADER, BOM_ROWS, self.config)
        twice, diff = kp.patch_bom(BOM_HEADER, once, self.config)
        self.assertEqual(once, twice)
        self.assertTrue(all(d["kind"] == "unchanged" for d in diff))

    def test_patch_bom_without_quantity_column(self):
        rows, _ = kp.patch_bom(BOM_HEADER[:4], [r[:4] for r in BOM_ROWS[:2]], self.config)
        self.assertEqual(rows[1], ["GT-B0403FSV14-100B1101", "J6,J7", CONN, "C42376244"])

    def test_keep_parent_when_asked(self):
        self.config["parents"][0]["drop_parent_from_cpl"] = False
        rows, _ = kp.patch_cpl(CPL_HEADER, CPL_ROWS, self.config, self.placements)
        self.assertIn("Module1", [r[0] for r in rows])


class MultipartFileTests(unittest.TestCase):
    def test_csv_backup_and_noop(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "CPL-x.csv"
            kp.write_csv_with_backup(path, CPL_HEADER, CPL_ROWS)  # no original: no .bak
            self.assertFalse(path.with_name("CPL-x.csv.bak").exists())
            new = CPL_ROWS[:2]
            self.assertTrue(kp.write_csv_with_backup(path, CPL_HEADER, new))
            header, rows = kp.read_csv_rows(path.with_name("CPL-x.csv.bak"))
            self.assertEqual(rows, CPL_ROWS)
            self.assertFalse(kp.write_csv_with_backup(path, CPL_HEADER, new))
            self.assertEqual(kp.read_csv_rows(path), (CPL_HEADER, new))
            self.assertNotIn(b"\r\r", path.read_bytes())

    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            pro = Path(d) / "board.kicad_pro"
            self.assertEqual(kp.multipart_config_load(pro), {"version": 1, "parents": []})
            cfg = {"version": 1, "parents": [{"ref": "Module1", "subparts": [
                {"ref": "J6", "pads": "1-100"}]}]}
            kp.multipart_config_save(pro, cfg)
            self.assertTrue((Path(d) / "board.multipart.json").exists())
            sub = kp.multipart_config_load(pro)["parents"][0]["subparts"][0]
            self.assertEqual(sub["mode"], "pads")
            self.assertEqual(sub["rotation_offset"], 0)

    def test_config_errors(self):
        with tempfile.TemporaryDirectory() as d:
            pro = Path(d) / "board.kicad_pro"
            path = Path(d) / "board.multipart.json"
            path.write_text("{nope", encoding="utf-8")
            with self.assertRaises(ValueError):
                kp.multipart_config_load(pro)
            path.write_text('{"version": 2, "parents": []}', encoding="utf-8")
            with self.assertRaises(ValueError):
                kp.multipart_config_load(pro)

    def test_jlc_output_paths(self):
        paths = kp.jlc_output_paths(Path("/p/Rev12/board.kicad_pro"))
        self.assertEqual(paths["pcb"], Path("/p/Rev12/board.kicad_pcb"))
        self.assertEqual(paths["cpl"], Path("/p/Rev12/jlcpcb/production_files/CPL-board.csv"))
        self.assertEqual(paths["bom"], Path("/p/Rev12/jlcpcb/production_files/BOM-board.csv"))

    def test_user_presets(self):
        with tempfile.TemporaryDirectory() as d, \
                unittest.mock.patch.object(kp, "PASSIVES_LIB_DIR", Path(d)):
            self.assertIn(kp.CM5_PRESET, kp.multipart_presets_all())
            kp.multipart_preset_save_user("My board", {"subparts": [{"ref": "J1", "pads": "1-4"}]})
            presets = kp.multipart_presets_all()
            self.assertIn("My board", presets)
            self.assertIn(kp.CM5_PRESET, presets)


if __name__ == "__main__":
    unittest.main()
