#!/usr/bin/env python3
# KiCad Personal Library Importer (GUI)
# - Imports mixed ZIPs/folders of KiCad parts into a central personal library
# - Copies footprints/3D, fixes 3D model paths to ${PERSONAL_LIB_DIR}
# - Merges symbols while DEDUPING and writing a CLEAN single-root symbols.kicad_sym
# - Includes Validate/Repair for symbols.kicad_sym
#
# NOTE: This script never appends raw text to symbols.kicad_sym.
# It rebuilds the file solely from parsed (symbol "...") blocks.

import os, re, sys, shutil, tempfile, zipfile, time, json, csv
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

HEADER = '(kicad_symbol_lib (version 20211014) (generator "importer"))\n'

# -------------------- Core helpers --------------------
def ensure_dirs(personal_dir: Path):
    (personal_dir / "footprints.pretty").mkdir(parents=True, exist_ok=True)
    (personal_dir / "3d" / "step").mkdir(parents=True, exist_ok=True)
    (personal_dir / "3d" / "wrl").mkdir(parents=True, exist_ok=True)
    sym = personal_dir / "symbols.kicad_sym"
    if not sym.exists():
        # Create a minimal, valid, empty library
        sym.write_text(HEADER + ")\n", encoding="utf-8")

def find_files(root: Path, patterns):
    out = []
    for p in patterns:
        out += list(root.rglob(p))
    return out

def copy_3d_models(staging: Path, personal: Path, overwrite=False, log=lambda *a: None):
    steps = find_files(staging, ["*.step", "*.stp"])
    wrls  = find_files(staging, ["*.wrl"])
    mapping = {}
    copied = 0
    for f in steps:
        dest = personal / "3d" / "step" / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest); copied += 1
            log(f"[3D] Copied: {f.name}")
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/step/" + f.name
    for f in wrls:
        dest = personal / "3d" / "wrl" / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest); copied += 1
            log(f"[3D] Copied: {f.name}")
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/wrl/" + f.name
    return mapping, copied

def rewrite_models_in_mod(txt: str, model_map: dict):
    def repl(m):
        full = m.group(1)
        base = os.path.basename(full.replace("\\", "/"))
        if base in model_map:
            return f'(model "{model_map[base]}"'
        return m.group(0)
    return re.sub(r'\(model\s+"([^"]+)"', repl, txt)

def import_footprints(staging: Path, personal: Path, overwrite=False, log=lambda *a: None):
    mods = find_files(staging, ["*.kicad_mod"])
    imported, skipped, errors = [], [], []
    for src in mods:
        dest = personal / "footprints.pretty" / src.name
        try:
            if dest.exists() and not overwrite:
                skipped.append(src.name); log(f"[FP] Skip (exists): {src.name}")
            else:
                shutil.copy2(src, dest); imported.append(src.name)
                log(f"[FP] Imported: {src.name}")
        except Exception as e:
            errors.append(f"{src.name}: {e}"); log(f"[FP] ERROR {src.name}: {e}")
    return [personal / "footprints.pretty" / Path(p).name for p in mods], imported, skipped, errors

def update_footprints_3d(personal: Path, model_map: dict, only_paths=None, log=lambda *a: None):
    targets = only_paths or list((personal / "footprints.pretty").glob("*.kicad_mod"))
    updated = 0
    for mod_path in targets:
        try:
            txt = Path(mod_path).read_text(encoding="utf-8", errors="ignore")
            new = rewrite_models_in_mod(txt, model_map)
            if new != txt:
                Path(mod_path).write_text(new, encoding="utf-8"); updated += 1
                log(f"[FP] Updated 3D path: {Path(mod_path).name}")
        except Exception as e:
            log(f"[FP] ERROR updating 3D for {mod_path}: {e}")
    return updated

# ---- Symbol parsing / merging (safe) ----
def parse_symbol_blocks(text: str):
    """Return list of (name, block_text) for top-level (symbol "...") blocks using paren balancing."""
    blocks = []
    i, n = 0, len(text)
    while True:
        i = text.find("(symbol ", i)
        if i == -1: break
        mname = re.search(r'\(symbol\s+"([^"]+)"', text[i:])
        if not mname: break
        name = mname.group(1)
        depth, j = 0, i
        while j < n:
            if text[j] == "(": depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    j += 1; break
            j += 1
        block = text[i:j]
        blocks.append((name, block))
        i = j
    return blocks

def read_personal_symbols(sym_path: Path):
    if not sym_path.exists():
        return {}, ""
    raw = sym_path.read_text(encoding="utf-8", errors="ignore")
    merged = {}
    for name, block in parse_symbol_blocks(raw):
        norm = re.sub(r"\s+", " ", block).strip()
        merged[name] = (block, norm)
    return merged, raw

def write_symbols_file(sym_path: Path, blocks_by_name: dict):
    """Rewrite as a clean, single-root file. Backup .bak first."""
    out = [HEADER]
    # stable order: by name
    for name in sorted(blocks_by_name.keys(), key=lambda s: s.lower()):
        block, _norm = blocks_by_name[name]
        lines = block.strip() + "\n"
        lines = "\n".join("  " + ln for ln in lines.splitlines())
        out.append(lines + "\n")
    out.append(")\n")
    backup = sym_path.with_suffix(".kicad_sym.bak")
    if sym_path.exists():
        try: sym_path.rename(backup)
        except Exception: shutil.copy2(sym_path, backup)
    Path(sym_path).write_text("".join(out), encoding="utf-8")
    return str(backup)

def merge_symbols(personal_sym: Path, vendor_syms: list, imported_fp_paths: list,
                  autoset_fp=True, fp_nick="Personal_Footprints", overwrite=False, log=lambda *a: None):
    existing, _raw = read_personal_symbols(personal_sym)
    merged = dict(existing)

    # Determine single footprint name if unique
    fp_names = []
    for p in imported_fp_paths:
        try:
            txt = Path(p).read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'\(footprint\s+"([^"]+)"', txt)
            fp_names.append(m.group(1) if m else Path(p).stem)
        except Exception:
            fp_names.append(Path(p).stem)
    single_fp = next(iter({n for n in fp_names if n}), None) if len({n for n in fp_names if n}) == 1 else None

    stats = {"added":[], "skipped_identical":[], "renamed":[], "overwritten":[]}

    for symlib in vendor_syms:
        txt = Path(symlib).read_text(encoding="utf-8", errors="ignore")
        for name, block in parse_symbol_blocks(txt):
            # Auto-set Footprint
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
                _blk, existing_norm = merged[name]
                if existing_norm == norm:
                    stats["skipped_identical"].append(name)
                    log(f"[SYM] Skip duplicate (identical): {name}")
                    continue
                if overwrite:
                    merged[name] = (block, norm); stats["overwritten"].append(name)
                    log(f"[SYM] Overwrite: {name}")
                else:
                    # rename to avoid collision
                    k, new_name = 1, f"{name}_imp1"
                    while new_name in merged:
                        k += 1; new_name = f"{name}_imp{k}"
                    block = re.sub(r'\(symbol\s+"([^"]+)"', f'(symbol "{new_name}"', block, count=1)
                    merged[new_name] = (block, re.sub(r"\s+", " ", block).strip())
                    stats["renamed"].append((name, new_name))
                    stats["added"].append(new_name)
                    log(f"[SYM] Renamed to avoid conflict: {new_name}")
                    continue
            else:
                merged[name] = (block, norm); stats["added"].append(name)

    backup = write_symbols_file(personal_sym, merged)
    log(f"[SYM] Wrote clean symbols file. Backup: {backup}")
    return stats

# ---- Package gathering (zip/folder collections) ----
def package_has_content(p: Path):
    return any(p.rglob("*.kicad_mod")) or any(p.rglob("*.kicad_sym")) or any(p.rglob("*.step")) or any(p.rglob("*.stp")) or any(p.rglob("*.wrl"))

def gather_packages(source: Path, recurse=True):
    pkgs = []
    if source.is_file() and source.suffix.lower() == ".zip":
        return [("zip", source)]
    if source.is_dir():
        if package_has_content(source):
            pkgs.append(("dir", source))
        if recurse:
            for p in source.rglob("*"):
                if p.is_file() and p.suffix.lower() == ".zip":
                    pkgs.append(("zip", p))
                elif p.is_dir() and package_has_content(p):
                    pkgs.append(("dir", p))
        # dedupe
        seen, uniq = set(), []
        for kind, p in pkgs:
            key = (kind, str(p.resolve()))
            if key not in seen:
                seen.add(key); uniq.append((kind, p))
        return uniq
    return []

def unzip_if_needed(path: Path):
    if path.is_dir():
        return path, None
    if path.is_file() and path.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="kicad_import_"))
        with zipfile.ZipFile(path, "r") as z:
            z.extractall(tmp)
        return tmp, tmp
    raise FileNotFoundError("Source is neither a directory nor a .zip")

def process_one_package(kind: str, path: Path, personal: Path, fp_nick="Personal_Footprints",
                        autoset_fp=True, overwrite=False, log=lambda *a: None):
    res = {
        "package": str(path), "kind": kind,
        "footprints_imported": 0, "footprints_skipped": 0, "footprints_errors": 0,
        "symbols_added": 0, "symbols_skipped_identical": 0, "symbols_renamed": 0, "symbols_overwritten": 0,
        "models_copied": 0, "errors": []
    }
    try:
        if kind == "zip":
            staging, tmpdir = unzip_if_needed(path)
        else:
            staging, tmpdir = (path, None)

        model_map, copied3d = copy_3d_models(staging, personal, overwrite=overwrite, log=log)
        res["models_copied"] = copied3d

        fps_paths, imported, skipped, fp_errs = import_footprints(staging, personal, overwrite=overwrite, log=log)
        res["footprints_imported"] = len(imported)
        res["footprints_skipped"] = len(skipped)
        res["footprints_errors"] = len(fp_errs)

        update_footprints_3d(personal, model_map, only_paths=fps_paths, log=log)

        syms = find_files(staging, ["*.kicad_sym"])
        if syms:
            stats = merge_symbols(personal / "symbols.kicad_sym", syms, fps_paths, autoset_fp=autoset_fp,
                                  fp_nick=fp_nick, overwrite=overwrite, log=log)
            res["symbols_added"] = len(stats["added"])
            res["symbols_skipped_identical"] = len(stats["skipped_identical"])
            res["symbols_renamed"] = len(stats["renamed"])
            res["symbols_overwritten"] = len(stats["overwritten"])
        if tmpdir: shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        res["errors"].append(str(e))
    return res

# -------------------- GUI --------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KiCad Personal Library Importer")
        self.geometry("880x640")

        self.source = tk.StringVar()
        self.personal = tk.StringVar()
        self.nick = tk.StringVar(value="Personal_Footprints")
        self.autoset = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)
        self.recurse = tk.BooleanVar(value=True)

        r = 0
        tk.Label(self, text="Source (ZIP, single folder, or a directory containing many packages):").grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(10,4))
        r += 1
        tk.Entry(self, textvariable=self.source, width=94).grid(row=r, column=0, columnspan=2, sticky="we", padx=8)
        tk.Button(self, text="Browse…", command=self.pick_source).grid(row=r, column=2, padx=8)
        r += 1

        tk.Label(self, text="Personal library folder (contains symbols.kicad_sym & footprints.pretty):").grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(10,4))
        r += 1
        tk.Entry(self, textvariable=self.personal, width=94).grid(row=r, column=0, columnspan=2, sticky="we", padx=8)
        tk.Button(self, text="Browse…", command=self.pick_personal).grid(row=r, column=2, padx=8)
        r += 1

        tk.Label(self, text="Footprint nickname:").grid(row=r, column=0, sticky="e", padx=8, pady=(6,2))
        tk.Entry(self, textvariable=self.nick, width=30).grid(row=r, column=1, sticky="w", padx=8, pady=(6,2))
        r += 1

        tk.Checkbutton(self, text="Auto-set symbol Footprint when a single footprint is imported", variable=self.autoset).grid(row=r, column=0, columnspan=3, sticky="w", padx=10)
        r += 1
        tk.Checkbutton(self, text="Overwrite existing items with same name", variable=self.overwrite).grid(row=r, column=0, columnspan=3, sticky="w", padx=10)
        r += 1
        tk.Checkbutton(self, text="Process recursively when source is a directory", variable=self.recurse).grid(row=r, column=0, columnspan=3, sticky="w", padx=10)
        r += 1

        btn_row = tk.Frame(self)
        btn_row.grid(row=r, column=0, columnspan=3, sticky="we", padx=8, pady=6)
        tk.Button(btn_row, text="Import", command=self.run).pack(side="left", padx=4)
        tk.Button(btn_row, text="Validate/Repair symbols.kicad_sym", command=self.repair_now).pack(side="left", padx=4)
        tk.Button(btn_row, text="Export Report", command=self.export_report).pack(side="left", padx=4)
        tk.Button(btn_row, text="Quit", command=self.destroy).pack(side="right", padx=4)
        r += 1

        self.log = ScrolledText(self, wrap="word", height=22)
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", padx=8, pady=8)
        self.grid_rowconfigure(r, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.results = []

    def pick_source(self):
        path = filedialog.askopenfilename(title="Choose vendor ZIP (or Cancel to pick a folder)")
        if not path:
            path = filedialog.askdirectory(title="Choose a folder (single package or a directory of packages)")
        if path:
            self.source.set(path)

    def pick_personal(self):
        path = filedialog.askdirectory(title="Choose your personal library folder")
        if path:
            self.personal.set(path)

    def logprint(self, *args):
        msg = " ".join(str(a) for a in args)
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.update_idletasks()

    def run(self):
        self.results = []
        try:
            src = Path(self.source.get()).expanduser()
            personal = Path(self.personal.get()).expanduser()
            if not src.exists():
                messagebox.showerror("Error", "Select a valid source (ZIP or folder)")
                return
            if not personal.exists():
                messagebox.showerror("Error", "Select your personal library folder")
                return
            ensure_dirs(personal)

            # Gather packages
            pkgs = gather_packages(src, recurse=self.recurse.get())
            if not pkgs:
                messagebox.showwarning("Nothing found", "No KiCad parts found in that source.")
                return

            self.logprint(f"Found {len(pkgs)} package(s). Starting import...\n")

            for idx, (kind, path) in enumerate(pkgs, 1):
                self.logprint(f"== [{idx}/{len(pkgs)}] {kind.upper()} :: {path}")
                def local_log(*a): self.logprint("   ", *a)
                res = process_one_package(
                    kind, path, personal,
                    fp_nick=self.nick.get(),
                    autoset_fp=self.autoset.get(),
                    overwrite=self.overwrite.get(),
                    log=local_log
                )
                self.results.append(res)
                if res["errors"]:
                    self.logprint("   ", "ERRORS:", "; ".join(res["errors"]))
                self.logprint(
                    "   ",
                    f"FP +{res['footprints_imported']} / skipped {res['footprints_skipped']} ;",
                    f"SYM +{res['symbols_added']} / renamed {res['symbols_renamed']} / overwritten {res['symbols_overwritten']} ;",
                    f"3D +{res['models_copied']}"
                )
            self.logprint("\nDone. Reload libraries in KiCad to see updates.")
            messagebox.showinfo("Import complete", f"Processed {len(pkgs)} package(s). Use 'Export Report' to save details.")
        except Exception as e:
            self.logprint("ERROR:", e)
            messagebox.showerror("Import failed", str(e))

    def export_report(self):
        if not self.results:
            messagebox.showwarning("No data", "Run an import first."); return
        ts = time.strftime("%Y%m%d-%H%M%S")
        save_json = filedialog.asksaveasfilename(title="Save JSON report", defaultextension=".json",
                                                 initialfile=f"kicad_personal_import_report_{ts}.json")
        if save_json:
            with open(save_json, "w", encoding="utf-8") as f:
                json.dump(self.results, f, indent=2)
        save_csv = filedialog.asksaveasfilename(title="Save CSV summary", defaultextension=".csv",
                                                initialfile=f"kicad_personal_import_report_{ts}.csv")
        if save_csv:
            cols = ["package","kind","footprints_imported","footprints_skipped","footprints_errors",
                    "symbols_added","symbols_renamed","symbols_overwritten","symbols_skipped_identical",
                    "models_copied","errors"]
            with open(save_csv, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
                for r in self.results:
                    row = {k: r.get(k, "") for k in cols}
                    if isinstance(row.get("errors"), list): row["errors"] = "; ".join(row["errors"])
                    w.writerow(row)
        messagebox.showinfo("Saved", "Report saved.")

    def repair_now(self):
        personal = Path(self.personal.get()).expanduser()
        sym = personal / "symbols.kicad_sym"
        if not personal.exists() or not sym.exists():
            messagebox.showerror("Error", "Select a valid personal library folder first."); return
        self.logprint(f"Repairing {sym} …")
        # Read, parse, rebuild clean file
        merged, raw = read_personal_symbols(sym)
        if not merged:
            self.logprint("   No (symbol …) blocks found. Leaving file as-is.")
            messagebox.showwarning("Nothing to repair", "No symbols found inside the file.")
            return
        backup = write_symbols_file(sym, merged)
        self.logprint(f"   Wrote clean symbols file. Backup at: {backup}")
        messagebox.showinfo("Repaired", "symbols.kicad_sym rebuilt successfully.")

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
