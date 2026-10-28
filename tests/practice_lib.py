"""Build a throwaway practice KiCad library, vendor ZIPs and project.

Everything is generated under a root directory you pass in (normally a
TemporaryDirectory), so tests never touch a real library.

    lib = build(Path(tmp))
    lib.personal, lib.sym_file, lib.pretty, lib.models, lib.zips, ...

The data is small but covers the cases the features are meant to handle:
three vendor ZIP layouts, a pre-existing library with duplicate symbols,
non-standard MPN fields, broken 3D model paths, and a hierarchical project
with passives plus an Octopart BOM in CSV and XLSX form.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

STEP = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('practice'),'2;1');\nENDSEC;\n"
        "DATA;\nENDSEC;\nEND-ISO-10303-21;\n")
PIN_NAMES = ["VDD", "GND", "SDA", "SCL", "EN", "OUT", "IN", "FB", "SW", "PG", "D+", "D-"]

# (mpn, ref prefix, footprint, pin count, vendor layout)
VENDOR_PARTS = [
    ("STM32G431CBU6", "U", "QFN50P700X700X60-49N", 12, "ultralibrarian"),
    ("TPS62133RGTR", "U", "QFN50P300X300X100-17N", 8, "samacsys"),
    ("USB4105-GF-A", "J", "GCT_USB4105-GF-A", 10, "snapeda"),
    ("AP2112K-3.3TRG1", "U", "SOT95P280X145-5N", 5, "ultralibrarian"),
    ("W25Q128JVSIQ", "U", "SOIC127P790X216-8N", 8, "samacsys"),
    ("BSS138", "Q", "SOT95P240X110-3N", 3, "snapeda"),
]
ZIP_NAMES = {"ultralibrarian": "ul_{}.zip", "snapeda": "{}.zip", "samacsys": "LIB_{}.zip"}


# ─── S-expression builders ────────────────────────────────────────────────

def symbol(name: str, ref: str, footprint: str, npins: int, extra_props: dict | None = None) -> str:
    pins = ""
    half = (npins + 1) // 2
    for i in range(npins):
        left = i < half
        row = i if left else i - half
        pins += (f'      (pin passive line (at {-12.7 if left else 12.7} {7.62 - row * 2.54:.2f} {0 if left else 180}) (length 2.54)\n'
                 f'        (name "{PIN_NAMES[i % len(PIN_NAMES)]}" (effects (font (size 1.27 1.27))))\n'
                 f'        (number "{i + 1}" (effects (font (size 1.27 1.27))))\n      )\n')
    props = {"Reference": ref, "Value": name, "Footprint": footprint, "Datasheet": ""}
    props.update(extra_props or {})
    ptxt = "".join(f'    (property "{k}" "{v}" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
                   for k, v in props.items())
    return (f'  (symbol "{name}" (in_bom yes) (on_board yes)\n{ptxt}'
            f'    (symbol "{name}_0_1"\n'
            f'      (rectangle (start -10.16 10.16) (end 10.16 -10.16) (stroke (width 0.254) (type default)) (fill (type background)))\n'
            f'    )\n    (symbol "{name}_1_1"\n{pins}    )\n  )\n')


def symbol_lib(*blocks: str) -> str:
    return '(kicad_symbol_lib (version 20211014) (generator "practice")\n' + "".join(blocks) + ")\n"


def footprint(name: str, npads: int, model: str | None = None, rotate: str = "0 0 0") -> str:
    pads = "".join(f'  (pad "{i + 1}" smd rect (at {i * 0.5:.2f} 0) (size 0.3 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
                   for i in range(npads))
    mdl = (f'  (model "{model}"\n    (offset (xyz 0 0 0))\n    (scale (xyz 1 1 1))\n    (rotate (xyz {rotate}))\n  )\n'
           if model else "")
    return (f'(footprint "{name}" (version 20221018) (generator pcbnew)\n  (layer "F.Cu")\n  (attr smd)\n'
            f'  (fp_text reference "REF**" (at 0 -2) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))\n'
            f'  (fp_text value "{name}" (at 0 2) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))\n'
            f'{pads}{mdl})\n')


# The (model ...) line each vendor ships inside its footprint, pointing at the
# vendor's own folder layout. Real downloads have these; pass vendor_model=True.
VENDOR_MODEL_LINES = {
    "ultralibrarian": "${{KIPRJMOD}}/STEP/{fp}.step",
    "snapeda": "${{KIPRJMOD}}/{mpn}.step",
    "samacsys": "C:\\\\Users\\\\vendor\\\\SamacSys_Parts.3dshapes\\\\{mpn}.stp",
}


def vendor_zip(path: Path, mpn: str, ref: str, fp: str, npins: int, layout: str,
               vendor_model: bool = False) -> None:
    sym = symbol_lib(symbol(mpn, ref, fp, npins, {"MPN": mpn}))
    model = VENDOR_MODEL_LINES[layout].format(fp=fp, mpn=mpn) if vendor_model else None
    mod = footprint(fp, min(npins, 8), model, rotate="-90 0 0")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if layout == "ultralibrarian":
            z.writestr(f"KiCADv6/{mpn}.kicad_sym", sym)
            z.writestr(f"KiCADv6/footprints.pretty/{fp}.kicad_mod", mod)
            z.writestr(f"STEP/{fp}.step", STEP)
        elif layout == "snapeda":
            z.writestr(f"{mpn}.kicad_sym", sym)
            z.writestr(f"{fp}.kicad_mod", mod)
            z.writestr(f"{mpn}.step", STEP)
        else:  # samacsys
            z.writestr(f"{mpn}/KiCad/{mpn}.kicad_sym", sym)
            z.writestr(f"{mpn}/KiCad/{fp}.kicad_mod", mod)
            z.writestr(f"{mpn}/3D/{mpn}.stp", STEP)
    path.write_bytes(buf.getvalue())


# ─── Schematic builders (KiCad 8/9 tab-indented format) ─────────────────

def sch_symbol(ref: str, lib_id: str, value: str, fp: str, props: dict | None = None) -> str:
    allp = [("Reference", ref), ("Value", value), ("Footprint", fp), ("Datasheet", "~")]
    allp += list((props or {}).items())
    ptxt = "".join(f'\t\t(property "{k}" "{v}"\n\t\t\t(at 0 0 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
                   f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n' for k, v in allp)
    return (f'\t(symbol\n\t\t(lib_id "{lib_id}")\n\t\t(at 100 100 0)\n\t\t(unit 1)\n\t\t(in_bom yes)\n'
            f'\t\t(on_board yes)\n\t\t(uuid "{uuid.uuid4()}")\n{ptxt}\t)\n')


def schematic(body: str, sheets: tuple[str, ...] = ()) -> str:
    sh = "".join(f'\t(sheet\n\t\t(at 180 120)\n\t\t(size 30 20)\n\t\t(uuid "{uuid.uuid4()}")\n'
                 f'\t\t(property "Sheetname" "{Path(s).stem}"\n\t\t\t(at 180 119 0)\n\t\t)\n'
                 f'\t\t(property "Sheetfile" "{s}"\n\t\t\t(at 180 141 0)\n\t\t)\n\t)\n' for s in sheets)
    return (f'(kicad_sch\n\t(version 20231120)\n\t(generator "eeschema")\n\t(generator_version "9.0")\n'
            f'\t(uuid "{uuid.uuid4()}")\n\t(paper "A4")\n\t(lib_symbols\n\t)\n{body}{sh})\n')


def write_xlsx(path: Path, rows: list[list[str]]) -> None:
    """Minimal .xlsx with shared strings (what Octopart exports)."""
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(s):
        if s not in index:
            index[s] = len(strings)
            strings.append(s)
        return index[s]

    def col(n):
        out = ""
        n += 1
        while n:
            n, r = divmod(n - 1, 26)
            out = chr(65 + r) + out
        return out

    sheet_rows = ""
    for r, row in enumerate(rows, 1):
        cells = "".join(f'<c r="{col(c)}{r}" t="s"><v>{sid(v)}</v></c>' for c, v in enumerate(row) if v != "")
        sheet_rows += f'<row r="{r}">{cells}</row>'
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("xl/sharedStrings.xml",
                   f'<?xml version="1.0"?><sst {ns}>' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<?xml version="1.0"?><worksheet {ns}><sheetData>{sheet_rows}</sheetData></worksheet>')


# ─── The practice library ─────────────────────────────────────────────────

@dataclass
class PracticeLib:
    root: Path
    home: Path
    personal: Path
    sym_file: Path
    pretty: Path
    models: Path
    zips: Path
    downloads: Path
    project: Path
    main_sch: Path
    power_sch: Path
    bom_csv: Path
    bom_xlsx: Path
    prefs: dict

    def symbols_text(self) -> str:
        return self.sym_file.read_text(encoding="utf-8")


BOM_ROWS = [
    ["Designator", "Value", "Matched Manufacturer", "Matched Part Number", "Description"],
    ["U1", "STM32G431CBU6", "STMicroelectronics", "STM32G431CBU6", "MCU"],
    ["U2", "TPS62133RGTR", "Texas Instruments", "TPS62133RGTR", "Buck"],
    ["L1", "2.2uH", "Taiyo Yuden", "NR4018T2R2M", "Inductor"],
    ["R1, R2, R3", "10k", "YAGEO", "RC0603FR-0710KL", "10k 1%"],
    ["C1, C2", "100nF", "Samsung", "CL05B104KO5NNNC", "100nF X7R"],
    ["C10", "10uF", "Samsung", "CL21A106KAYNNNE", "10uF X5R"],
]

R603 = "Resistor_SMD:R_0603_1608Metric"
R402 = "Resistor_SMD:R_0402_1005Metric"
C402 = "Capacitor_SMD:C_0402_1005Metric"
C805 = "Capacitor_SMD:C_0805_2012Metric"


def build(root: Path) -> PracticeLib:
    home = root / "home"
    personal = root / "kicad" / "personal"
    zips = root / "kicad" / "zips"
    downloads = home / "Downloads"
    project = root / "projects" / "sensor-node"
    for d in (personal, zips, downloads, project):
        d.mkdir(parents=True, exist_ok=True)
    pretty = personal / "personal.pretty"
    models = personal / "3d"
    step = models / "step"
    pretty.mkdir()
    step.mkdir(parents=True)

    # Vendor zips: first four in zips/, last two still in Downloads
    for i, (mpn, ref, fp, n, layout) in enumerate(VENDOR_PARTS):
        dest = (zips if i < 4 else downloads) / ZIP_NAMES[layout].format(mpn)
        vendor_zip(dest, mpn, ref, fp, n, layout)
    (downloads / "datasheet.pdf").write_bytes(b"%PDF-1.4\n")

    # Existing library: LM358DR imported twice (_imp1 copy), a symbol whose
    # part number lives in a non-standard field, and a plain resistor.
    sym_file = personal / "personal.kicad_sym"
    sym_file.write_text(symbol_lib(
        symbol("LM358DR", "U", "personal:SOIC127P600X175-8N", 8, {"MPN": "LM358DR"}),
        symbol("LM358DR_imp1", "U", "personal:SOIC127P600X175-8N", 8, {"MPN": "LM358DR"}),
        symbol("NE555DR", "U", "personal:SOIC127P600X175-8N", 8, {"Manufacturer_Part_Number": "NE555DR"}),
        symbol("R_Generic", "R", "", 2),
    ), encoding="utf-8")

    # Existing footprints with the 3D problems the 3D Models tab looks for
    for n in ("SOIC127P600X175-8N.step", "SOT-23.step", "QFN-20_4x4mm.step", "SW_Tactile_6x6mm.step"):
        (step / n).write_text(STEP)
    (pretty / "SOIC127P600X175-8N.kicad_mod").write_text(
        footprint("SOIC127P600X175-8N", 8, "../3d/step/SOIC127P600X175-8N.step"))            # OK
    (pretty / "SOT-23-3_legacy.kicad_mod").write_text(
        footprint("SOT-23-3_legacy", 3, "..\\3d\\step\\SOT-23.step"))                         # backslashes
    (pretty / "QFN-20_4x4mm.kicad_mod").write_text(
        footprint("QFN-20_4x4mm", 8, "${KIPRJMOD}/models/QFN20.step"))                        # missing, exact match exists
    (pretty / "SW_Tactile_6x6.kicad_mod").write_text(footprint("SW_Tactile_6x6", 4))           # no model, fuzzy match
    (pretty / "Crystal_3225.kicad_mod").write_text(
        footprint("Crystal_3225", 4, "../3d/step/ABM8-3225.step"))                            # missing, no match

    # Hierarchical project
    main = [
        sch_symbol("U1", "personal:STM32G431CBU6", "STM32G431CBU6", "personal:QFN50P700X700X60-49N"),
        sch_symbol("R1", "Device:R", "10k", R603, {"MPN": "RC0603FR-0710KL", "LCSC": "C98220"}),
        sch_symbol("R2", "Device:R", "10k", R603),
        sch_symbol("R3", "Device:R", "10K", R603),
        sch_symbol("R4", "Device:R", "4k7", R603),
        sch_symbol("R5", "Device:R", "4.7k", R603),
        sch_symbol("C1", "Device:C", "100nF", C402),
        sch_symbol("C2", "Device:C", "0.1uF", C402),
    ]
    power = [
        sch_symbol("U2", "personal:TPS62133RGTR", "TPS62133RGTR", "personal:QFN50P300X300X100-17N"),
        sch_symbol("L1", "Device:L", "2.2uH", "Inductor_SMD:L_Taiyo-Yuden_NR-40xx"),
        sch_symbol("C10", "Device:C", "10uF", C805),
        sch_symbol("R10", "Device:R", "100k", R402),
    ]
    main_sch = project / "sensor-node.kicad_sch"
    power_sch = project / "power.kicad_sch"
    main_sch.write_text(schematic("".join(main), ("power.kicad_sch",)), encoding="utf-8")
    power_sch.write_text(schematic("".join(power)), encoding="utf-8")
    (project / "sensor-node.kicad_pro").write_text(json.dumps({"meta": {"filename": "sensor-node.kicad_pro"}}))

    bom_csv = project / "bom.csv"
    with open(bom_csv, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(BOM_ROWS)
    bom_xlsx = project / "bom.xlsx"
    write_xlsx(bom_xlsx, BOM_ROWS)

    prefs = {
        "last_source_dir": str(zips),
        "last_personal_dir": str(personal),
        "sym_choice": sym_file.name,
        "fp_choice": pretty.name,
        "models_root": str(models),
        "rel_models": True,
        "footprint_nickname": "personal",
        "auto_detect_nickname": False,
        "autoset": True,
        "skip_imported": True,
        "downloads_dir": str(downloads),
        "inbox_zips_only": True,
    }
    return PracticeLib(root, home, personal, sym_file, pretty, models, zips, downloads,
                       project, main_sch, power_sch, bom_csv, bom_xlsx, prefs)


if __name__ == "__main__":  # python tests/practice_lib.py <dir>  -> build one to poke at by hand
    import sys
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "practice")
    out.mkdir(parents=True, exist_ok=True)
    lib = build(out)
    (lib.home).mkdir(parents=True, exist_ok=True)
    print(f"Practice library built under {out.resolve()}")
