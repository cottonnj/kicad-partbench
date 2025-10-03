#!/usr/bin/env python3
import os, re, sys, shutil, tempfile, zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# Reuse core functions by embedding (no external import)

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

def import_footprints(staging: Path, personal: Path, overwrite=False, log=print):
    mods = find_files(staging, ["*.kicad_mod"])
    imported = []
    for src in mods:
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

def parse_symbol_blocks(text: str):
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
    existing = {}
    for name, block in parse_symbol_blocks(txt):
        norm = re.sub(r"\s+", " ", block).strip()
        existing[name] = (block, norm)
    return txt, existing

def write_symbols_file(sym_path: Path, blocks_by_name: dict):
    out = [HEADER]
    for name, (block, _norm) in blocks_by_name.items():
        lines = block.strip() + "\n"
        lines = "\n".join("  " + ln for ln in lines.splitlines())
        out.append(lines + "\n")
    out.append(")\n")
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
    merged = dict(existing)

    # Single footprint name?
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

    added = []
    for symlib in vendor_syms:
        txt = Path(symlib).read_text(encoding="utf-8", errors="ignore")
        for name, block in parse_symbol_blocks(txt):
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
                    k = 1
                    new_name = f"{name}_imp{k}"
                    while new_name in merged:
                        k += 1
                        new_name = f"{name}_imp{k}"
                    block = re.sub(r'\(symbol\s+"([^"]+)"', f'(symbol "{new_name}"', block, count=1)
                    merged[new_name] = (block, re.sub(r"\s+", " ", block).strip())
                    log(f"[SYM] Renamed to avoid conflict: {new_name}")
                    added.append(new_name)
                    continue
            else:
                merged[name] = (block, norm)
            added.append(name)
    backup = write_symbols_file(personal_sym, merged)
    log(f"[SYM] Wrote clean symbols file. Backup: {backup}")
    return added

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KiCad Personal Library Importer")
        self.geometry("760x520")

        self.staging = tk.StringVar()
        self.personal = tk.StringVar()
        self.nick = tk.StringVar(value="Personal_Footprints")
        self.autoset = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)

        r = 0
        tk.Label(self, text="Vendor folder or ZIP:").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        tk.Entry(self, textvariable=self.staging, width=70).grid(row=r, column=1, sticky="we", padx=6, pady=6)
        tk.Button(self, text="Browse…", command=self.pick_staging).grid(row=r, column=2, padx=6, pady=6)

        r += 1
        tk.Label(self, text="Personal library folder:").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        tk.Entry(self, textvariable=self.personal, width=70).grid(row=r, column=1, sticky="we", padx=6, pady=6)
        tk.Button(self, text="Browse…", command=self.pick_personal).grid(row=r, column=2, padx=6, pady=6)

        r += 1
        tk.Label(self, text="Footprint nickname:").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        tk.Entry(self, textvariable=self.nick, width=30).grid(row=r, column=1, sticky="w", padx=6, pady=6)

        r += 1
        tk.Checkbutton(self, text="Auto-set symbol Footprint when a single footprint is imported", variable=self.autoset).grid(row=r, column=0, columnspan=3, sticky="w", padx=12)
        r += 1
        tk.Checkbutton(self, text="Overwrite existing (footprints & symbols with same name)", variable=self.overwrite).grid(row=r, column=0, columnspan=3, sticky="w", padx=12)

        r += 1
        tk.Button(self, text="Import", command=self.run).grid(row=r, column=0, padx=6, pady=8, sticky="w")
        tk.Button(self, text="Quit", command=self.destroy).grid(row=r, column=2, padx=6, pady=8, sticky="e")

        r += 1
        self.log = ScrolledText(self, wrap="word", height=16)
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", padx=6, pady=6)
        self.grid_rowconfigure(r, weight=1)
        self.grid_columnconfigure(1, weight=1)

    def logprint(self, *args):
        msg = " ".join(str(a) for a in args)
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.update_idletasks()

    def pick_staging(self):
        path = filedialog.askopenfilename(title="Choose vendor ZIP or any file inside it")
        if not path:
            path = filedialog.askdirectory(title="…or choose the vendor folder")
        if path:
            self.staging.set(path)

    def pick_personal(self):
        path = filedialog.askdirectory(title="Choose your personal library folder")
        if path:
            self.personal.set(path)

    def run(self):
        try:
            staging_in = Path(self.staging.get()).expanduser()
            personal = Path(self.personal.get()).expanduser()
            if not staging_in.exists():
                messagebox.showerror("Error", "Select a valid vendor folder/ZIP")
                return
            if not personal.exists():
                messagebox.showerror("Error", "Select your personal library folder")
                return
            ensure_dirs(personal)
            # Unzip if needed
            if staging_in.is_file() and staging_in.suffix.lower() == ".zip":
                tmpdir = Path(tempfile.mkdtemp(prefix="kicad_import_"))
                with zipfile.ZipFile(staging_in, "r") as z:
                    z.extractall(tmpdir)
                staging = tmpdir
            else:
                staging = staging_in

            self.logprint(f"Staging:  {staging}")
            self.logprint(f"Personal: {personal}")

            model_map = copy_3d_models(staging, personal, overwrite=self.overwrite.get(), log=self.logprint)
            fps = import_footprints(staging, personal, overwrite=self.overwrite.get(), log=self.logprint)
            update_footprints_3d(personal, model_map, only_paths=fps, log=self.logprint)

            syms = [p for p in staging.rglob("*.kicad_sym")]
            if syms:
                added = merge_symbols(
                    personal / "symbols.kicad_sym",
                    syms,
                    fps,
                    autoset_fp=self.autoset.get(),
                    fp_nick=self.nick.get(),
                    overwrite=self.overwrite.get(),
                    log=self.logprint
                )
                self.logprint(f"[SYM] Imported {len(added)} new/updated symbol(s).")
            else:
                self.logprint("[SYM] No .kicad_sym files found.")

            self.logprint("\nDone. In KiCad, ensure:")
            self.logprint("  PERSONAL_LIB_DIR -> your personal folder")
            self.logprint("  Personal_Symbols -> ${PERSONAL_LIB_DIR}/symbols.kicad_sym")
            self.logprint("  Personal_Footprints -> ${PERSONAL_LIB_DIR}/footprints.pretty")
            messagebox.showinfo("Import complete", "Finished importing parts into your personal library.")
        except Exception as e:
            self.logprint("ERROR:", e)
            messagebox.showerror("Import failed", str(e))

if __name__ == "__main__":
    app = App()
    app.mainloop()
