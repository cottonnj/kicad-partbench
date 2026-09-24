"""Tests for the non-GUI helpers in kicad_partbench.py (stdlib unittest only)."""
import sys
import tempfile
import textwrap
import unittest
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


if __name__ == "__main__":
    unittest.main()
