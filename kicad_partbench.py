#!/usr/bin/env python3
import os, re, sys, shutil, argparse, tempfile, zipfile
from pathlib import Path

HEADER = '(kicad_symbol_lib (version 20211014) (generator "importer"))\n'

def ensure_dirs(personal_dir: Path):
    (personal_dir / "footprints.pretty").mkdir(parents=True, exist_ok=True)
    (personal_dir / "3d" / "step").mkdir(parents=True, exist_ok=True)
    (personal_dir / "3d" / "wrl").mkdir(parents=True, exist_ok=True)
    sym = personal_dir / "symbols.kicad_sym"
    if not sym.exists():
        sym.write_text(HEADER + ")\n", encoding="utf-8")

def find_files(root: Path, patterns):
    out = []
    for p in patterns:
        out += list(root.rglob(p))
    return out

# ---------------- 3D models ----------------
def copy_3d_models(staging: Path, personal: Path, overwrite=False, log=print):
    steps = find_files(staging, ["*.step", "*.stp"])
    wrls  = find_files(staging, ["*.wrl"])
    mapping = {}
    for f in steps:
        dest = personal / "3d" / "step" / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest)
            log(f"[3D] Copied: {f.name} -> 3d/step/")
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/step/" + f.name
    for f in wrls:
        dest = personal / "3d" / "wrl" / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest)
            log(f"[3D] Copied: {f.name} -> 3d/wrl/")
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/wrl/" + f.name
    return mapping

def rewrite_models_in_mod(txt: str, model_map: dict):
    def repl(m):
        full = m.group(1)
        base = os.path.basename(full.replace("\\", "/"))
        if base in model_map:
            return f'(model "{model_map[base]}"'
        return m.group(0)
    return re.sub(r'\(model\s+"([^"]+)"', repl, txt)

# ---------------- Footprints ----------------
def import_footprints(staging: Path, personal: Path, overwrite=False, log=print):
    mods = find_files(staging, ["*.kicad_mod"])
    imported = []
    for src in mods:
        name = src.stem
        dest = personal / "footprints.pretty" / src.name
        if dest.exists() and not overwrite:
            log(f"[FP] Skip (exists): {src.name}")
        else:
            shutil.copy2(src, dest)
            log(f"[FP] Imported: {src.name}")
        imported.append(dest)
    return imported

def update_footprints_3d(personal: Path, model_map: dict, only_paths=None, log=print):
    targets = only_paths or list((personal / "footprints.pretty").glob("*.kicad_mod"))
    for mod_path in targets:
        txt = mod_path.read_text(encoding="utf-8", errors="ignore")
        new = rewrite_models_in_mod(txt, model_map)
        if new != txt:
            mod_path.write_text(new, encoding="utf-8")
            log(f"[FP] Updated 3D path: {mod_path.name}")

# ---------------- Symbols ----------------
def parse_symbol_blocks(text: str):
    """Return list of (name, block_text)."""
    blocks = []
    i = 0
    n = len(text)
    while True:
        i = text.find("(symbol ", i)
        if i == -1:
            break
        mname = re.search(r'\(symbol\s+"([^"]+)"', text[i:])
        if not mname:
            break
        name = mname.group(1)
        depth = 0
        j = i
        while j < n:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        block = text[i:j]
        blocks.append((name, block))
        i = j
    return blocks

def read_personal_symbols(sym_path: Path):
    if not sym_path.exists():
        return HEADER + ")\n", {}
    txt = sym_path.read_text(encoding="utf-8", errors="ignore")
    # Extract existing blocks
    existing = {}
    for name, block in parse_symbol_blocks(txt):
        # Normalize for content comparison
        norm = re.sub(r"\s+", " ", block).strip()
        existing[name] = (block, norm)
    return txt, existing

def write_symbols_file(sym_path: Path, blocks_by_name: dict):
    # Build clean single-root file
    out = [HEADER]
    for name, (block, _norm) in blocks_by_name.items():
        # indent each block for readability
        lines = block.strip() + "\n"
        lines = "\n".join("  " + ln for ln in lines.splitlines())
        out.append(lines + "\n")
    out.append(")\n")
    # Backup
    backup = sym_path.with_suffix(".kicad_sym.bak")
    if sym_path.exists():
        try:
            sym_path.rename(backup)
        except Exception:
            shutil.copy2(sym_path, backup)
    Path(sym_path).write_text("".join(out), encoding="utf-8")
    return str(backup)

def merge_symbols(personal_sym: Path, vendor_syms: list, imported_fp_paths: list, autoset_fp=True, fp_nick="Personal_Footprints", overwrite=False, log=print):
    _, existing = read_personal_symbols(personal_sym)
    # Build dict we will write: name -> (block, norm)
    merged = dict(existing)

    # Determine single imported footprint name if unambiguous
    fp_names = []
    for p in imported_fp_paths:
        try:
            txt = Path(p).read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'\(footprint\s+"([^"]+)"', txt)
            fp_names.append(m.group(1) if m else Path(p).stem)
        except Exception:
            fp_names.append(Path(p).stem)
    single_fp = None
    uniq = {n for n in fp_names if n}
    if len(uniq) == 1:
        single_fp = next(iter(uniq))

    # Iterate vendor libs
    imported_names = []
    for symlib in vendor_syms:
        txt = Path(symlib).read_text(encoding="utf-8", errors="ignore")
        for name, block in parse_symbol_blocks(txt):
            # Optionally set Footprint property
            if autoset_fp and single_fp:
                fpfull = f"{fp_nick}:{single_fp}"
                if re.search(r'\(property\s+"Footprint"\s+"[^"]*"', block):
                    block = re.sub(r'(\(property\s+"Footprint"\s+)"[^"]*"', r'\1"{}"'.format(fpfull), block, count=1)
                else:
                    block = block.rstrip()
                    if block.endswith(")"):
                        block = block[:-1] + f'\n    (property "Footprint" "{fpfull}" (at 0 0 0) (effects (font (size 1 1))) (hide))\n)\n'

            norm = re.sub(r"\s+", " ", block).strip()

            if name in merged:
                _, existing_norm = merged[name]
                if existing_norm == norm:
                    log(f"[SYM] Skip duplicate (identical): {name}")
                    continue
                if overwrite:
                    log(f"[SYM] Overwrite: {name}")
                    merged[name] = (block, norm)
                else:
                    # Rename with suffix
                    k = 1
                    new_name = f"{name}_imp{k}"
                    while new_name in merged:
                        k += 1
                        new_name = f"{name}_imp{k}"
                    block = re.sub(r'\(symbol\s+"([^"]+)"', f'(symbol "{new_name}"', block, count=1)
                    merged[new_name] = (block, re.sub(r"\s+", " ", block).strip())
                    log(f"[SYM] Renamed to avoid conflict: {new_name}")
                    imported_names.append(new_name)
                    continue
            else:
                merged[name] = (block, norm)
            imported_names.append(name)

    backup = write_symbols_file(personal_sym, merged)
    log(f"[SYM] Wrote clean symbols file. Backup: {backup}")
    return imported_names

def unzip_if_needed(path: Path):
    if path.is_dir():
        return path, None
    if path.is_file() and path.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="kicad_import_"))
        with zipfile.ZipFile(path, "r") as z:
            z.extractall(tmp)
        return tmp, tmp
    raise FileNotFoundError("Staging is neither a directory nor a .zip")

def main():
    ap = argparse.ArgumentParser(description="Import vendor KiCad parts into a central personal library (dedupe + valid symbols file).")
    ap.add_argument("--staging", required=True, help="Path to vendor folder or .zip")
    ap.add_argument("--personal", required=True, help="Path to personal library root (contains symbols.kicad_sym, footprints.pretty)")
    ap.add_argument("--nick_footprints", default="Personal_Footprints", help="Footprint library nickname")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing footprints and symbols with same name")
    ap.add_argument("--no-autoset-footprint", action="store_true", help="Do not auto-set symbol Footprint")
    args = ap.parse_args()

    staging_in = Path(args.staging).expanduser().resolve()
    personal = Path(args.personal).expanduser().resolve()
    tmpdir = None

    ensure_dirs(personal)
    staging, tmpdir = unzip_if_needed(staging_in)

    print(f"Staging:  {staging}")
    print(f"Personal: {personal}")

    model_map = copy_3d_models(staging, personal, overwrite=args.overwrite, log=print)
    fps = import_footprints(staging, personal, overwrite=args.overwrite, log=print)
    update_footprints_3d(personal, model_map, only_paths=fps, log=print)

    syms = find_files(staging, ["*.kicad_sym"])
    imported_names = []
    if syms:
        imported_names = merge_symbols(
            personal / "symbols.kicad_sym",
            syms,
            fps,
            autoset_fp=(not args.no_autoset_footprint),
            fp_nick=args.nick_footprints,
            overwrite=args.overwrite,
            log=print
        )
        print(f"[SYM] Imported {len(imported_names)} new/updated symbol(s).")
    else:
        print("[SYM] No .kicad_sym files found.")

    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\nDone. In KiCad, ensure:")
    print("  PERSONAL_LIB_DIR -> your personal folder")
    print("  Personal_Symbols -> ${PERSONAL_LIB_DIR}/symbols.kicad_sym")
    print("  Personal_Footprints -> ${PERSONAL_LIB_DIR}/footprints.pretty")
    print("Then reload libraries or restart KiCad.")

if __name__ == "__main__":
    main()
