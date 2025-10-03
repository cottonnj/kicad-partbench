#!/usr/bin/env python3
# KiCad Personal Library Importer — Strict + Smart Dedupe (v3.5)
# - Strict writer: single-root, two-space indents, converts (hide)->(hide yes)
# - Fixes nested unit names: ParentName_u_c with u,c >= 1; parent prefix enforced
# - De-duplication modes: Exact, Ignore Properties (default), Pins-only (aggressive)
# - Ignores nonessential property noise when set; option to ignore Footprint for dedupe
# - Per-symbol verbose logging; Dry run option
# - Accurate summary (top-level symbols only)
# - Package pruning: process only leaf directories; if both ZIP and extracted folder exist, prefer one
# - Remembers last used Source/Personal directories (prefs at ~/.kicad_personal_importer.json)
# - "View Library Parts": aligned table + search + CSV; fixes 3D model detection

import os, re, shutil, tempfile, zipfile, time, json, csv, textwrap, hashlib
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

HEADER = '(kicad_symbol_lib (version 20211014) (generator "gui-strict-dedupe-v3.5")\n'
NONESSENTIAL_PROPS = {
    "Datasheet","Description","ki_description","ki_keywords","ki_locked","Comment","Keywords",
    "LCSC","LCSC Part","MPN","MFR","Manufacturer","Supplier","Supplier Part","Part Number",
    "Link","URL","Alt","Notes"
}
PREFS_FILE = Path.home() / ".kicad_personal_importer.json"

# -------------------- Filesystem helpers --------------------
def ensure_dirs(personal_dir: Path):
    (personal_dir / "footprints.pretty").mkdir(parents=True, exist_ok=True)
    (personal_dir / "3d" / "step").mkdir(parents=True, exist_ok=True)
    (personal_dir / "3d" / "wrl").mkdir(parents=True, exist_ok=True)
    sym = personal_dir / "symbols.kicad_sym"
    if not sym.exists():
        sym.write_text(HEADER + ")\n", encoding="utf-8")

def find_files(root: Path, patterns):
    out = []
    for pat in patterns:
        out += list(root.rglob(pat))
    return out

def package_has_content(p: Path):
    if p.is_file() and p.suffix.lower() == ".zip":
        return True
    if not p.is_dir():
        return False
    for pat in ("*.kicad_sym","*.kicad_mod","*.step","*.stp","*.wrl"):
        if list(p.glob(pat)):
            return True
    for sub in ("KiCADv6","kicad6","KiCad","Library.pretty","footprints.pretty"):
        if (p / sub).exists():
            return True
    return False

def gather_packages(source: Path, recurse=True):
    pkgs = []
    if source.is_file() and source.suffix.lower() == ".zip":
        pkgs.append(("zip", source)); return pkgs
    if source.is_dir():
        if package_has_content(source):
            pkgs.append(("dir", source))
        if recurse:
            for p in source.rglob("*"):
                if p.is_file() and p.suffix.lower() == ".zip":
                    pkgs.append(("zip", p))
                elif p.is_dir() and package_has_content(p):
                    pkgs.append(("dir", p))
    seen, uniq = set(), []
    for kind, p in pkgs:
        key = (kind, str(p.resolve()))
        if key not in seen:
            seen.add(key); uniq.append((kind, p))
    return uniq

def prune_packages(pkgs, prefer="folder"):
    # 1) Drop parent directories that contain any other package beneath them
    abs_pkgs = [(kind, Path(p).resolve()) for kind, p in pkgs]
    parents = set()
    for i, (kind_i, p_i) in enumerate(abs_pkgs):
        if kind_i != "dir":
            continue
        for j, (_k, p_j) in enumerate(abs_pkgs):
            if i != j and str(p_j).startswith(str(p_i) + os.sep):
                parents.add(p_i); break
    leaf = [(k,p) for (k,p) in abs_pkgs if not (k == "dir" and p in parents)]

    # 2) If both zip and extracted folder exist with same stem in same parent, keep one
    grouped = {}
    for kind, p in leaf:
        parent = p.parent
        stem = p.stem if kind == "zip" else p.name
        grouped.setdefault((str(parent), stem.lower()), []).append((kind, p))

    pruned = []
    for key, items in grouped.items():
        if len(items) == 1:
            pruned.extend(items)
        else:
            kinds = {k for k,_ in items}
            if "zip" in kinds and "dir" in kinds:
                if prefer == "zip":
                    chosen = [it for it in items if it[0] == "zip"][0]
                else:
                    chosen = [it for it in items if it[0] == "dir"][0]
                pruned.append(chosen)
            else:
                latest = max(items, key=lambda t: t[1].stat().st_mtime if t[1].exists() else 0)
                pruned.append(latest)
    return pruned

# -------------------- 3D + Footprints --------------------
def copy_3d_models(staging: Path, personal: Path, overwrite=False, log=lambda *a: None):
    steps = find_files(staging, ["*.step", "*.stp"])
    wrls  = find_files(staging, ["*.wrl"])
    mapping = {}
    copied = 0
    for f in steps:
        dest = personal / "3d" / "step" / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest); copied += 1; log(f"[3D] Copied: {f.name}")
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/step/" + f.name
    for f in wrls:
        dest = personal / "3d" / "wrl" / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest); copied += 1; log(f"[3D] Copied: {f.name}")
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/wrl/" + f.name
    return mapping, copied

def rewrite_models_in_mod(txt: str, model_map: dict):
    def repl(m):
        full = m.group(1) or m.group(2)
        base = os.path.basename(full.replace("\\", "/"))
        if base in model_map: return f'(model "{model_map[base]}"'
        return m.group(0)
    # Support quoted or unquoted model path
    return re.sub(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', repl, txt)

def import_footprints(staging: Path, personal: Path, overwrite=False, log=lambda *a: None):
    mods = find_files(staging, ["*.kicad_mod"])
    imported, skipped, errors = [], [], []
    for src in mods:
        dest = personal / "footprints.pretty" / src.name
        try:
            if dest.exists() and not overwrite:
                skipped.append(src.name); log(f"[FP] Skip (exists): {src.name}")
            else:
                shutil.copy2(src, dest); imported.append(src.name); log(f"[FP] Imported: {src.name}")
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
                Path(mod_path).write_text(new, encoding="utf-8"); updated += 1; log(f"[FP] Updated 3D path: {Path(mod_path).name}")
        except Exception as e:
            log(f"[FP] ERROR updating 3D for {mod_path}: {e}")
    return updated


def _available_models(models_root: Path):
    avail = {}
    if models_root.exists():
        for f in models_root.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".step",".stp",".wrl"):
                avail.setdefault(f.name.lower(), f)
                avail.setdefault(f.stem.lower(), f)
    return avail

def _parse_models_from_mod(txt: str):
    models = []
    for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt):
        models.append(m.group(1) or m.group(2))
    return models

def _choose_local_model(avail: dict, key_stem: str):
    for ext in (".step",".stp",".wrl"):
        k = (key_stem + ext).lower()
        if k in avail:
            return avail[k]
    return avail.get(key_stem.lower())

def _rewrite_model_block(txt: str, local_path: Path):
    rel = "${PERSONAL_LIB_DIR}/3d/" + ( "step/" if local_path.suffix.lower() in (".step",".stp") else "wrl/" ) + local_path.name
    def repl(m):
        _ = m.group(0)
        return re.sub(r'\(model\s+(?:"[^"]+"|[^\s)]+)', f'(model "{rel}"', _)
    return re.sub(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', repl, txt, count=1)

def _append_model_block(txt: str, local_path: Path):
    rel = "${PERSONAL_LIB_DIR}/3d/" + ( "step/" if local_path.suffix.lower() in (".step",".stp") else "wrl/" ) + local_path.name
    insert = f'\n  (model "{rel}")\n'
    if txt.rstrip().endswith(')'):
        return txt.rstrip()[:-1] + insert + ")\n"
    return txt + insert

def audit_footprints(fp_dir: Path, models_root: Path):
    rows = []
    avail = _available_models(models_root)
    for mod_path in sorted(fp_dir.glob("*.kicad_mod"), key=lambda p: p.name.lower()):
        txt = mod_path.read_text(encoding="utf-8", errors="ignore")
        models = _parse_models_from_mod(txt)
        if not models:
            status, icon = ("None","❌"); found = 0; total = 0
        else:
            total = len(models); found = 0
            for mpath in models:
                base = Path(mpath.replace("\\","/")).name
                stem = Path(base).stem
                if base.lower() in avail or _choose_local_model(avail, stem):
                    found += 1
            if found == 0: status, icon = ("None","❌")
            elif found == total: status, icon = ("All","✅")
            else: status, icon = ("Partial","⚠️")
        rows.append((mod_path.stem, status, icon, f"{found}/{total}"))
    return rows

def fix_footprint_models(fp_dir: Path, models_root: Path, dry_run: bool=True, add_if_missing: bool=True):
    avail_full = _available_models(models_root)
    changed = 0; total = 0; modified = []
    for mod_path in sorted(fp_dir.glob("*.kicad_mod"), key=lambda p: p.name.lower()):
        total += 1
        txt = mod_path.read_text(encoding="utf-8", errors="ignore")
        models = _parse_models_from_mod(txt)
        new_txt = txt
        if models:
            for mpath in models:
                base = Path(mpath.replace("\\","/")).name
                stem = Path(base).stem
                local = avail_full.get(base.lower()) or _choose_local_model(avail_full, stem)
                if local:
                    new_txt = _rewrite_model_block(new_txt, local)
        elif add_if_missing:
            stem = mod_path.stem
            local = _choose_local_model(avail_full, stem)
            if local:
                new_txt = _append_model_block(new_txt, local)
        if new_txt != txt:
            changed += 1; modified.append(mod_path)
            if not dry_run:
                bak = mod_path.with_suffix(".kicad_mod.bak")
                try: mod_path.rename(bak)
                except Exception: pass
                mod_path.write_text(new_txt, encoding="utf-8")
    return changed, total, modified

# -------------------- Symbols: parse/sanitize/hash --------------------
def parse_symbol_blocks(text: str):
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
            ch = text[j]
            if ch == "(": depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0: j += 1; break
            j += 1
        block = text[i:j]
        blocks.append((name, block))
        i = j
    return blocks

def fix_units_in_block(parent_name: str, block: str):
    matches = list(re.finditer(r'\(symbol\s+"([^"]+)"', block))
    if not matches or len(matches) == 1: return block
    new_block = block
    for m in reversed(matches[1:]):
        child = m.group(1)
        mu = re.match(r'^(.*)_(\d+)_(\d+)$', child)
        if mu:
            base, u, c = mu.group(1), int(mu.group(2)), int(mu.group(3))
            new_name = f'{parent_name}_{max(1,u)}_{max(1,c)}'
        else:
            new_name = f'{parent_name}_1_1'
        start, end = m.span(1)
        new_block = new_block[:start] + new_name + new_block[end:]
    return new_block

def sanitize_block(parent_name: str, block: str) -> str:
    s = textwrap.dedent(block.strip())
    k = s.find("(symbol ");  s = s[k:] if k >= 0 else s
    s = re.sub(r'\(hide\)', '(hide yes)', s)
    s = fix_units_in_block(parent_name, s)
    lines = []
    for ln in s.splitlines():
        lines.append("  " + ln.lstrip().replace("\t", "  "))
    return "\n".join(lines)

def strip_footprint_prop(block: str):
    return re.sub(r'\(\s*property\s+"Footprint"\s+"[^"]*"\s*\(.*?\)\s*\)', '', block, flags=re.DOTALL)

def strip_prop_ids(block: str):
    s = re.sub(r'\(id\s+\d+\)', '', block)
    s = re.sub(r'\(uuid\s+[^)]+\)', '', s)
    s = re.sub(r'\(at\s+[^)]+\)', '', s)
    s = re.sub(r'\(effects\s+\(.*?\)\)', '', s, flags=re.DOTALL)
    return s

def drop_nonessential_props(block: str):
    def repl(m):
        name = m.group(1)
        if name in NONESSENTIAL_PROPS:
            return ''
        return f'(property "{name}" "{m.group(2)}")'
    return re.sub(r'\(\s*property\s+"([^"]+)"\s+"([^"]*)".*?\)', repl, block, flags=re.DOTALL)

def pins_signature(block: str):
    pins = []
    for m in re.finditer(r'\(pin\s+([a-zA-Z0-9_]+)\s+[a-zA-Z0-9_]+\s*\(.*?\(number\s+"([^"]+)"\).*?\(name\s+"([^"]*)"\)', block, flags=re.DOTALL):
        ptype, num, name = m.group(1), m.group(2), m.group(3)
        pins.append((ptype, num, name))
    pins.sort()
    return json.dumps(pins, separators=(',',':'))

def canonical_content(block: str, mode: str, ignore_footprint=True) -> str:
    s = re.sub(r'\s+', ' ', block).strip()
    if mode == "Exact":
        return s
    s = strip_prop_ids(s)
    if ignore_footprint:
        s = strip_footprint_prop(s)
    s = drop_nonessential_props(s)
    s = re.sub(r'\s+', ' ', s).strip()
    if mode == "Pins-only":
        return pins_signature(s)
    return s

def read_personal_symbols(sym_path: Path, mode: str, ignore_footprint=True):
    if not sym_path.exists():
        return {}, {}
    raw = sym_path.read_text(encoding="utf-8", errors="ignore")
    merged = {}
    content_map = {}
    for name, block in parse_symbol_blocks(raw):
        sanitized = sanitize_block(name, block)
        canon = canonical_content(sanitized, mode, ignore_footprint=ignore_footprint)
        merged[name] = sanitized
        content_map[hashlib.sha1(canon.encode("utf-8")).hexdigest()] = name
    return merged, content_map

def write_symbols_file(sym_path: Path, blocks_by_name: dict):
    out = [HEADER]
    for name in sorted(blocks_by_name.keys(), key=lambda s: s.lower()):
        out.append(blocks_by_name[name].rstrip() + "\n")
    out.append(")\n")
    backup = sym_path.with_suffix(".kicad_sym.bak")
    if sym_path.exists():
        try: sym_path.rename(backup)
        except Exception: shutil.copy2(sym_path, backup)
    Path(sym_path).write_text("".join(out), encoding="utf-8")
    return str(backup)

def merge_symbols_strict(personal_sym: Path, vendor_syms: list, imported_fp_paths: list,
                         autoset_fp=True, fp_nick="Personal_Footprints", overwrite=False,
                         mode="Ignore Properties", ignore_footprint_for_dedupe=True, log=lambda *a: None):
    existing, content_index = read_personal_symbols(personal_sym, mode, ignore_footprint=ignore_footprint_for_dedupe)
    merged = dict(existing)

    # unique footprint in this package?
    fp_names = []
    for p in imported_fp_paths:
        try:
            txt = Path(p).read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'\(footprint\s+"([^"]+)"', txt)
            fp_names.append(m.group(1) if m else Path(p).stem)
        except Exception:
            fp_names.append(Path(p).stem)
    uniq = {n for n in fp_names if n}
    single_fp = next(iter(uniq)) if len(uniq) == 1 else None

    stats = {"added":[], "skipped_identical":[], "renamed":[], "overwritten":[], "skipped_content_dupe":[], "debug_symbols":[]}

    for symlib in vendor_syms:
        txt = Path(symlib).read_text(encoding="utf-8", errors="ignore")
        for name, raw_block in parse_symbol_blocks(txt):
            block = textwrap.dedent(raw_block.strip())
            if autoset_fp and single_fp:
                fpfull = f"{fp_nick}:{single_fp}"
                if re.search(r'\(property\s+"Footprint"\s+"[^"]*"', block):
                    block = re.sub(r'(\(property\s+"Footprint"\s+)"[^"]*"', r'\1"{}"'.format(fpfull), block, count=1)
                else:
                    if block.endswith(")"):
                        block = block[:-1] + f'\n    (property "Footprint" "{fpfull}" (at 0 0 0) (effects (font (size 1 1))) (hide yes))\n)\n'
            sanitized = sanitize_block(name, block)
            canon = canonical_content(sanitized, mode, ignore_footprint=ignore_footprint_for_dedupe)
            h = hashlib.sha1(canon.encode("utf-8")).hexdigest()

            if h in content_index:
                kept = content_index[h]
                stats["skipped_content_dupe"].append((name, kept))
                stats["debug_symbols"].append((name, "SKIP_DUP", kept))
                log(f"[SYM] Skip duplicate by content: {name} == {kept}")
                continue

            if name in merged:
                if overwrite:
                    merged[name] = sanitized; content_index[h] = name; stats["overwritten"].append(name)
                    stats["debug_symbols"].append((name, "OVERWRITE", name)); log(f"[SYM] Overwrite: {name}")
                else:
                    k, new_name = 1, f"{name}_imp1"
                    while new_name in merged:
                        k += 1; new_name = f"{name}_imp{k}"
                    sanitized = re.sub(r'\(symbol\s+"([^"]+)"', f'(symbol "{new_name}"', sanitized, count=1)
                    merged[new_name] = sanitized; content_index[h] = new_name
                    stats["renamed"].append((name, new_name)); stats["debug_symbols"].append((name, "RENAME", new_name))
                    log(f"[SYM] Renamed: {new_name}")
            else:
                merged[name] = sanitized; content_index[h] = name; stats["added"].append(name); stats["debug_symbols"].append((name, "ADD", name))

    return stats, merged

# ---- unzip/prepare ----
def unzip_if_needed(path: Path):
    if path.is_dir(): return path, None
    if path.is_file() and path.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="kicad_import_"))
        with zipfile.ZipFile(path, "r") as z: z.extractall(tmp)
        return tmp, tmp
    raise FileNotFoundError("Source is neither a directory nor a .zip")

def process_one_package(kind: str, path: Path, personal: Path, fp_nick="Personal_Footprints",
                        autoset_fp=True, overwrite=False, mode="Ignore Properties",
                        ignore_footprint_for_dedupe=True, dry_run=False, log=lambda *a: None):
    res = {
        "package": str(path), "kind": kind,
        "footprints_imported": 0, "footprints_skipped": 0, "footprints_errors": 0,
        "symbols_added": 0, "symbols_skipped_identical": 0, "symbols_renamed": 0, "symbols_overwritten": 0,
        "symbols_skipped_content_dupe": 0, "models_copied": 0, "errors": [],
        "symbol_names_debug": []
    }
    try:
        if kind == "zip": staging, tmpdir = unzip_if_needed(path)
        else: staging, tmpdir = (path, None)

        model_map, copied3d = copy_3d_models(staging, personal, overwrite=overwrite, log=log)
        res["models_copied"] = copied3d

        fps_paths, imported, skipped, fp_errs = import_footprints(staging, personal, overwrite=overwrite, log=log)
        res["footprints_imported"] = len(imported); res["footprints_skipped"] = len(skipped); res["footprints_errors"] = len(fp_errs)

        syms = find_files(staging, ["*.kicad_sym"])
        if syms:
            stats, merged = merge_symbols_strict(personal / "symbols.kicad_sym", syms, fps_paths,
                                                 autoset_fp=autoset_fp, fp_nick=fp_nick, overwrite=overwrite,
                                                 mode=mode, ignore_footprint_for_dedupe=ignore_footprint_for_dedupe, log=log)
            res["symbols_added"] = len(stats["added"])
            res["symbols_skipped_identical"] = len(stats.get("skipped_identical", []))
            res["symbols_renamed"] = len(stats["renamed"])
            res["symbols_overwritten"] = len(stats["overwritten"])
            res["symbols_skipped_content_dupe"] = len(stats["skipped_content_dupe"])
            res["symbol_names_debug"] = stats["debug_symbols"]
            if not dry_run:
                write_symbols_file(personal / "symbols.kicad_sym", merged)
        if tmpdir: shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        res["errors"].append(str(e))
    return res

# -------------------- GUI --------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KiCad Personal Library Importer — Strict + Smart Dedupe (v3.5)")
        self.geometry("1100x780")

        self.source = tk.StringVar(); self.personal = tk.StringVar()
        self.nick = tk.StringVar(value="Personal_Footprints")
        self.autoset = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)
        self.recurse = tk.BooleanVar(value=True)
        self.ignore_fp_dedupe = tk.BooleanVar(value=True)
        self.dedupe_mode = tk.StringVar(value="Ignore Properties")
        self.dry_run = tk.BooleanVar(value=False)
        self.prefer_pkg = tk.StringVar(value="folder")  # or "zip"

        self.prefs = self.load_prefs()
        if self.prefs.get("last_source_dir"): self.source.set(self.prefs["last_source_dir"])
        if self.prefs.get("last_personal_dir"): self.personal.set(self.prefs["last_personal_dir"])

        r = 0
        tk.Label(self, text="Source (ZIP, single folder, or a directory containing many packages):").grid(row=r, column=0, columnspan=6, sticky="w", padx=8, pady=(10,4)); r += 1
        tk.Entry(self, textvariable=self.source, width=120).grid(row=r, column=0, columnspan=5, sticky="we", padx=8)
        tk.Button(self, text="Browse…", command=self.pick_source).grid(row=r, column=5, padx=8); r += 1

        tk.Label(self, text="Personal library folder (contains symbols.kicad_sym & footprints.pretty):").grid(row=r, column=0, columnspan=6, sticky="w", padx=8, pady=(10,4)); r += 1
        tk.Entry(self, textvariable=self.personal, width=120).grid(row=r, column=0, columnspan=5, sticky="we", padx=8)
        tk.Button(self, text="Browse…", command=self.pick_personal).grid(row=r, column=5, padx=8); r += 1

        tk.Label(self, text="Footprint nickname:").grid(row=r, column=0, sticky="e", padx=8, pady=(6,2))
        tk.Entry(self, textvariable=self.nick, width=30).grid(row=r, column=1, sticky="w", padx=8, pady=(6,2))
        tk.Label(self, text="De-duplication mode:").grid(row=r, column=2, sticky="e", padx=8)
        ttk.Combobox(self, textvariable=self.dedupe_mode, values=["Exact","Ignore Properties","Pins-only"]).grid(row=r, column=3, sticky="w", padx=8)
        tk.Label(self, text="If ZIP and folder both exist (same stem):").grid(row=r, column=4, sticky="e", padx=8)
        ttk.Combobox(self, textvariable=self.prefer_pkg, values=["folder","zip"]).grid(row=r, column=5, sticky="w", padx=8); r += 1

        tk.Checkbutton(self, text="Auto-set symbol Footprint when a single footprint is imported", variable=self.autoset).grid(row=r, column=0, columnspan=6, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Overwrite existing items with same name", variable=self.overwrite).grid(row=r, column=0, columnspan=6, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Process recursively when source is a directory", variable=self.recurse).grid(row=r, column=0, columnspan=6, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Ignore Footprint when de-duplicating (recommended)", variable=self.ignore_fp_dedupe).grid(row=r, column=0, columnspan=6, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Dry run (don’t write file; just show what would happen)", variable=self.dry_run).grid(row=r, column=0, columnspan=6, sticky="w", padx=10); r += 1

        btn_row = tk.Frame(self); btn_row.grid(row=r, column=0, columnspan=6, sticky="we", padx=8, pady=6)
        tk.Button(btn_row, text="Import", command=self.run).pack(side="left", padx=4)
        tk.Button(btn_row, text="Validate/Repair symbols.kicad_sym", command=self.repair_now).pack(side="left", padx=4)
        tk.Button(btn_row, text="View Library Parts", command=self.view_library_parts).pack(side="left", padx=4)
        tk.Button(btn_row, text="Audit Footprints (3D)", command=self.audit_footprints_gui).pack(side="left", padx=4)
        tk.Button(btn_row, text="Fix 3D Paths…", command=self.fix_3d_paths_gui).pack(side="left", padx=4)
        tk.Button(btn_row, text="Export Report", command=self.export_report).pack(side="left", padx=4)
        tk.Button(btn_row, text="Quit", command=self.destroy).pack(side="right", padx=4); r += 1

        self.log = ScrolledText(self, wrap="word", height=28)
        self.log.grid(row=r, column=0, columnspan=6, sticky="nsew", padx=8, pady=8)
        self.grid_rowconfigure(r, weight=1); self.grid_columnconfigure(1, weight=1)

        self.results = []

    # ---------- Preferences ----------
    def load_prefs(self):
        try:
            if PREFS_FILE.exists():
                return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def save_prefs(self):
        try:
            data = {
                "last_source_dir": self.source.get(),
                "last_personal_dir": self.personal.get()
            }
            PREFS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _initialdir_for(self, path_str: str):
        try:
            if path_str:
                p = Path(path_str).expanduser()
                if p.exists():
                    if p.is_file(): return str(p.parent)
                    if p.is_dir(): return str(p)
            if self.prefs.get("last_source_dir") and Path(self.prefs["last_source_dir"]).exists():
                return self.prefs["last_source_dir"]
        except Exception:
            pass
        return str(Path.home())

    def pick_source(self):
        initdir = self._initialdir_for(self.source.get())
        path = filedialog.askopenfilename(title="Choose vendor ZIP (or Cancel to pick a folder)", initialdir=initdir)
        if not path:
            path = filedialog.askdirectory(title="Choose a folder (single package or a directory of packages)", initialdir=initdir)
        if path:
            self.source.set(path)
            dir_path = str(Path(path).parent if Path(path).is_file() else Path(path))
            self.prefs["last_source_dir"] = dir_path
            self.save_prefs()

    def pick_personal(self):
        initdir = self._initialdir_for(self.personal.get())
        path = filedialog.askdirectory(title="Choose your personal library folder", initialdir=initdir)
        if path:
            self.personal.set(path)
            self.prefs["last_personal_dir"] = path
            self.save_prefs()

    def logprint(self, *args):
        msg = " ".join(str(a) for a in args)
        self.log.insert("end", msg + "\n"); self.log.see("end"); self.update_idletasks()

    def _count_top_level(self, sym_path: Path):
        try:
            raw = sym_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return 0
        return len(parse_symbol_blocks(raw))

    # ---------- Main actions ----------
    def run(self):
        self.results = []
        try:
            src = Path(self.source.get()).expanduser()
            personal = Path(self.personal.get()).expanduser()
            if not src.exists(): messagebox.showerror("Error", "Select a valid source (ZIP or folder)"); return
            if not personal.exists(): messagebox.showerror("Error", "Select your personal library folder"); return
            ensure_dirs(personal)
            self.save_prefs()

            pkgs = gather_packages(src, recurse=self.recurse.get())
            if not pkgs: messagebox.showwarning("Nothing found", "No KiCad parts found in that source."); return
            pruned = prune_packages(pkgs, prefer=self.prefer_pkg.get())
            self.logprint(f"Discovered {len(pkgs)} package candidates; using {len(pruned)} after pruning parents and zip/folder duplicates.")
            pkgs = pruned

            initial_count = self._count_top_level(personal / "symbols.kicad_sym")
            self.logprint(f"Starting import… (initial top-level symbols: {initial_count})\n")

            for idx, (kind, path) in enumerate(pkgs, 1):
                self.logprint(f"== [{idx}/{len(pkgs)}] {kind.upper()} :: {path}")
                def local_log(*a): self.logprint('   ', *a)
                res = process_one_package(
                    kind, path, personal,
                    fp_nick=self.nick.get(),
                    autoset_fp=self.autoset.get(),
                    overwrite=self.overwrite.get(),
                    mode=self.dedupe_mode.get(),
                    ignore_footprint_for_dedupe=self.ignore_fp_dedupe.get(),
                    dry_run=self.dry_run.get(),
                    log=local_log
                )
                self.results.append(res)
                for name, action, other in res.get("symbol_names_debug", []):
                    if action == "ADD":         self.logprint("   [SYM] ADD    ", name)
                    elif action == "SKIP_DUP":  self.logprint("   [SYM] SKIP   ", name, "==", other)
                    elif action == "RENAME":    self.logprint("   [SYM] RENAME ", name, "->", other)
                    elif action == "OVERWRITE": self.logprint("   [SYM] OVERWR ", name)
                if res["errors"]:
                    self.logprint("   ", "ERRORS:", "; ".join(res["errors"]))
                self.logprint("   ",
                    f"FP +{res['footprints_imported']} / skipped {res['footprints_skipped']} ;",
                    f"SYM +{res['symbols_added']} / renamed {res['symbols_renamed']} / overwritten {res['symbols_overwritten']} / content-dupes {res['symbols_skipped_content_dupe']} ;",
                    f"3D +{res['models_copied']}"
                )

            sym_path = personal / "symbols.kicad_sym"
            if not self.dry_run.get():
                final_count = self._count_top_level(sym_path)
                self.logprint(f"\nSummary: symbols.kicad_sym contains {final_count} top-level symbol(s).")
                messagebox.showinfo("Import complete", f"Processed {len(pkgs)} package(s). symbols.kicad_sym contains {final_count} symbol(s).")
            else:
                adds = sum(1 for r in self.results for (_n,a,_o) in r.get("symbol_names_debug", []) if a in ("ADD","RENAME"))
                est = initial_count + adds
                self.logprint(f"\nSummary (dry-run): initial {initial_count} -> estimated {est} top-level symbol(s) after this run.")
                messagebox.showinfo("Dry run complete", f"Processed {len(pkgs)} package(s). Estimated symbols after run: {est}.")
            self.logprint("Done. Reload libraries in KiCad to see updates.")
        except Exception as e:
            self.logprint("ERROR:", e); messagebox.showerror("Import failed", str(e))

    def export_report(self):
        if not self.results: messagebox.showwarning("No data", "Run an import first."); return
        ts = time.strftime("%Y%m%d-%H%M%S")
        save_json = filedialog.asksaveasfilename(title="Save JSON report", defaultextension=".json",
                                                 initialfile=f"kicad_personal_import_report_{ts}.json",
                                                 initialdir=self._initialdir_for(self.source.get()))
        if save_json:
            with open(save_json, "w", encoding="utf-8") as f: json.dump(self.results, f, indent=2)
        save_csv = filedialog.asksaveasfilename(title="Save CSV summary", defaultextension=".csv",
                                                initialfile=f"kicad_personal_import_report_{ts}.csv",
                                                initialdir=self._initialdir_for(self.source.get()))
        if save_csv:
            cols = ["package","kind","footprints_imported","footprints_skipped","footprints_errors",
                    "symbols_added","symbols_renamed","symbols_overwritten","symbols_skipped_identical",
                    "symbols_skipped_content_dupe","models_copied","errors","symbol_names_debug"]
            with open(save_csv, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
                for r in self.results:
                    row = {k: r.get(k, "") for k in cols}
                    if isinstance(row.get("errors"), list): row["errors"] = "; ".join(row["errors"])
                    if isinstance(row.get("symbol_names_debug"), list):
                        row["symbol_names_debug"] = "; ".join([f"{a}:{b}->{c}" for a,b,c in row["symbol_names_debug"]])
                    w.writerow(row)
        messagebox.showinfo("Saved", "Report saved.")

    def repair_now(self):
        personal = Path(self.personal.get()).expanduser()
        sym = personal / "symbols.kicad_sym"
        if not personal.exists() or not sym.exists():
            messagebox.showerror("Error", "Select a valid personal library folder first."); return
        self.logprint(f"Repairing {sym} …")
        raw = sym.read_text(encoding="utf-8", errors="ignore")
        merged = {}
        for name, block in parse_symbol_blocks(raw):
            merged[name] = sanitize_block(name, block)
        if not merged:
            self.logprint("   No (symbol …) blocks found. Leaving file as-is.")
            messagebox.showwarning("Nothing to repair", "No symbols found inside the file.")
            return
        backup = write_symbols_file(sym, merged)
        self.logprint(f"   Wrote clean symbols file. Backup at: {backup}")
        messagebox.showinfo("Repaired", "symbols.kicad_sym rebuilt successfully.")

    def audit_footprints_gui(self):
        personal = Path(self.personal.get()).expanduser()
        fp_dir = personal / "footprints.pretty"
        models_root = personal / "3d"
        if not fp_dir.exists():
            messagebox.showerror("Error", f"No footprints.pretty at: {fp_dir}"); return
        rows = audit_footprints(fp_dir, models_root)
        win = tk.Toplevel(self); win.title("Footprints 3D Audit"); win.geometry("900x600")
        ok = sum(1 for _,s,_,_ in rows if s=="All"); part = sum(1 for _,s,_,_ in rows if s=="Partial"); none = sum(1 for _,s,_,_ in rows if s=="None")
        tk.Label(win, text=f"Footprints: {len(rows)}   |   3D: All {ok}, Partial {part}, None {none}").pack(anchor="w", padx=8, pady=6)
        cols = ("Footprint Module","3D","Models Found")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=24)
        for col,w in (("Footprint Module",520),("3D",160),("Models Found",160)):
            tree.heading(col, text=col); tree.column(col, width=w, anchor="center" if col!="Footprint Module" else "w")
        for mod, status, icon, cnt in rows:
            tree.insert("", "end", values=(mod, f"{icon} {status}", cnt))
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        def export_csv():
            ts = time.strftime("%Y%m%d-%H%M%S")
            path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"footprints_3d_audit_{ts}.csv")
            if not path: return
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(cols)
                for mod, status, icon, cnt in rows:
                    w.writerow((mod, status, cnt))
            messagebox.showinfo("Saved", f"Exported {len(rows)} rows.")
        btns = tk.Frame(win); btns.pack(fill="x", padx=8, pady=(0,8))
        tk.Button(btns, text="Export CSV", command=export_csv).pack(side="left")
        tk.Button(btns, text="Close", command=win.destroy).pack(side="right")

    def fix_3d_paths_gui(self):
        personal = Path(self.personal.get()).expanduser()
        fp_dir = personal / "footprints.pretty"
        models_root = personal / "3d"
        if not fp_dir.exists():
            messagebox.showerror("Error", f"No footprints.pretty at: {fp_dir}"); return

        win = tk.Toplevel(self); win.title("Fix 3D Paths in Footprints"); win.geometry("560x260")
        dry = tk.BooleanVar(value=True)
        add_missing = tk.BooleanVar(value=True)
        tk.Checkbutton(win, text="Dry run (show what would change; don't write files)", variable=dry).pack(anchor="w", padx=12, pady=8)
        tk.Checkbutton(win, text="Add (model …) to footprints that have none (by filename match)", variable=add_missing).pack(anchor="w", padx=12, pady=4)
        out = ScrolledText(win, wrap="word", height=8); out.pack(fill="both", expand=True, padx=8, pady=8)

        def run_fix():
            changed, total, mods = fix_footprint_models(fp_dir, models_root, dry_run=dry.get(), add_if_missing=add_missing.get())
            out.insert("end", f"{'Dry run' if dry.get() else 'Wrote'}: {changed} of {total} footprints would be/was updated.\n")
            for m in mods[:200]:
                out.insert("end", f" - {m.name}\n")
            out.see("end")
            messagebox.showinfo("Done", f"{'Dry run' if dry.get() else 'Updated'} {changed} of {total} footprints.")
        tk.Button(win, text="Run", command=run_fix).pack(side="left", padx=12, pady=6)
        tk.Button(win, text="Close", command=win.destroy).pack(side="right", padx=12, pady=6)

    # ---------- Library listing ----------
    def view_library_parts(self):
        personal = Path(self.personal.get()).expanduser()
        sym_path = personal / "symbols.kicad_sym"
        fp_dir = personal / "footprints.pretty"
        models_root = personal / "3d"
        if not sym_path.exists():
            messagebox.showerror("Error", f"No symbols.kicad_sym at: {sym_path}"); return
        raw = sym_path.read_text(encoding="utf-8", errors="ignore")
        blocs = parse_symbol_blocks(raw)
        if not blocs:
            messagebox.showwarning("Empty", "No symbols found in your library."); return

        # Build a quick index of available 3D models (case-insensitive)
        avail_models = set()
        if models_root.exists():
            for p in models_root.rglob("*"):
                if p.is_file() and p.suffix.lower() in (".step",".stp",".wrl"):
                    avail_models.add(p.name.lower())

        def footprint_of(block: str):
            m = re.search(r'\(\s*property\s+"Footprint"\s+"([^"]+)"', block)
            return m.group(1) if m else None

        def footprint_file(mod_name: str):
            if not mod_name: return None
            return fp_dir / f"{mod_name}.kicad_mod"

        def parse_models_in_footprint(mod_name: str):
            """Return list of model paths (as strings) from the footprint, supports quoted or unquoted."""
            if not mod_name: return []
            fpath = footprint_file(mod_name)
            if not fpath or not fpath.exists():
                return []
            txt = fpath.read_text(encoding="utf-8", errors="ignore")
            models = []
            # Support: (model "foo/bar.step") or (model ${VAR}/foo/bar.step)
            for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt):
                path = m.group(1) or m.group(2)
                models.append(path)
            return models

        def model_found(path: str):
            """Decide if we have a 3D file corresponding to this model path in our personal 3d folder."""
            if not path: return False
            base = Path(path.replace("\\","/")).name
            base_l = base.lower()
            if base_l in avail_models:
                return True
            # try ignoring extension (e.g., vendor uses .wrl but we have .step)
            stem = Path(base_l).stem
            for ext in (".step",".stp",".wrl"):
                if (stem + ext) in avail_models:
                    return True
            return False

        # Rows: (Symbol, Footprint Module, FP_text, FP_icon, ThreeD_text, ThreeD_icon, FoundCount)
        rows = []
        stats = {"ok_fp":0,"missing_fp":0,"none_fp":0,"ok_3d":0,"some_3d":0,"none_3d":0}
        for name, block in sorted(blocs, key=lambda t: t[0].lower()):
            fp_prop = footprint_of(block)
            if fp_prop:
                mod_name = fp_prop.split(":")[-1].strip()
            else:
                mod_name = None

            if mod_name:
                fpath = footprint_file(mod_name)
                if fpath and fpath.exists():
                    fp_text, fp_icon = "OK", "✅"
                    stats["ok_fp"] += 1
                else:
                    fp_text, fp_icon = "Missing", "❌"
                    stats["missing_fp"] += 1
            else:
                fp_text, fp_icon = "No prop", "—"
                stats["none_fp"] += 1

            # 3D models
            if mod_name and fp_text == "OK":
                models = parse_models_in_footprint(mod_name)
                if not models:
                    three_d_text, three_d_icon = "None", "❌"
                    found = 0; total = 0
                    stats["none_3d"] += 1
                else:
                    found = sum(1 for m in models if model_found(m))
                    total = len(models)
                    if found == 0:
                        three_d_text, three_d_icon = "None", "❌"; stats["none_3d"] += 1
                    elif found == total:
                        three_d_text, three_d_icon = "All", "✅"; stats["ok_3d"] += 1
                    else:
                        three_d_text, three_d_icon = "Partial", "⚠️"; stats["some_3d"] += 1
            else:
                three_d_text, three_d_icon = "None", "—"
                found = 0; total = 0
                stats["none_3d"] += 1

            rows.append((name, mod_name or "", fp_text, fp_icon, three_d_text, three_d_icon, f"{found}/{total}"))

        # Create a window with a Treeview for aligned columns + search + CSV export
        win = tk.Toplevel(self)
        win.title("Library Parts Overview")
        win.geometry("1060x680")

        # Summary + legend
        summary = f"Symbols: {len(blocs)}   |   Footprint: OK {stats['ok_fp']}, Missing {stats['missing_fp']}, None {stats['none_fp']}   |   3D: All {stats['ok_3d']}, Partial {stats['some_3d']}, None {stats['none_3d']}"
        legend = "Legend: ✅ OK, ⚠️ Partial, ❌ Missing/None, — Not applicable"
        tk.Label(win, text=summary).pack(anchor="w", padx=8, pady=(8,2))
        tk.Label(win, text=legend, fg="#555").pack(anchor="w", padx=8, pady=(0,6))

        # Search row
        search_frame = tk.Frame(win); search_frame.pack(fill="x", padx=8, pady=4)
        tk.Label(search_frame, text="Filter:").pack(side="left")
        qvar = tk.StringVar()
        ent = tk.Entry(search_frame, textvariable=qvar, width=40)
        ent.pack(side="left", padx=6)
        def do_filter(*_):
            q = qvar.get().strip().lower()
            for i in tree.get_children():
                tree.delete(i)
            for r in rows:
                if q in r[0].lower() or q in (r[1] or "").lower():
                    tree.insert("", "end", values=(r[0], r[1], f"{r[3]} {r[2]}", f"{r[5]} {r[4]}", r[6]))
        qvar.trace_add("write", lambda *_: do_filter())

        # Treeview
        cols = ("Symbol","Footprint Module","Footprint","3D","Models Found")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=24)
        for col, w, anchor in (("Symbol",420,"w"),("Footprint Module",280,"w"),("Footprint",140,"center"),("3D",140,"center"),("Models Found",120,"center")):
            tree.heading(col, text=col)
            tree.column(col, width=w, anchor=anchor)
        # populate
        for r in rows:
            tree.insert("", "end", values=(r[0], r[1], f"{r[3]} {r[2]}", f"{r[5]} {r[4]}", r[6]))
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        # Buttons row
        btns = tk.Frame(win); btns.pack(fill="x", padx=8, pady=(0,8))
        def export_csv():
            ts = time.strftime("%Y%m%d-%H%M%S")
            path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"personal_library_overview_{ts}.csv")
            if not path: return
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(("Symbol","Footprint Module","Footprint","3D","Models Found"))
                for r in rows:
                    w.writerow((r[0], r[1], r[2], r[4], r[6]))  # textual statuses for CSV
            messagebox.showinfo("Saved", f"Exported {len(rows)} rows.")
        tk.Button(btns, text="Export to CSV", command=export_csv).pack(side="left")
        tk.Button(btns, text="Close", command=win.destroy).pack(side="right")


def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
